"""
Bot B -- "Challenger". Same job as the incumbent, six specific changes.

Each change targets one thing the incumbent gets wrong, and each is
independently switchable so the tournament can attribute the difference
rather than just observe it.

1. **The market price is the anchor, not the enemy.** The incumbent computes
   `p_est` from a calculator and calls the difference from the price "edge".
   The challenger starts from the price and moves off it only as far as
   measured evidence justifies:

       belief = price + k * (calibrated_signal - price)

   where `k` is fitted by regressing *realised* edge on *claimed* edge across
   every market that has ever resolved. When the signal is worthless, k goes
   to zero, belief collapses onto the price, and the bot stops trading --
   which is the correct response to having no edge and one the incumbent
   structurally cannot reach.

   `k` also corrects the optimizer's curse. A bot screening hundreds of
   markets and trading its largest apparent edges is selecting for
   estimation error, not for opportunity. Fitting k on realised outcomes
   measures and removes exactly that bias, with no free parameters.

2. **Forecasts are calibrated against every resolution observed**, not just
   against the handful of markets it traded. The incumbent needs ten settled
   *trades* to learn one bit about a category; the challenger gets a training
   example from every market that settles anywhere in its universe, at zero
   risk. On a typical day that is two to three orders of magnitude more
   evidence.

3. **Fees are exact.** Per-order, rounded up, maker and taker distinguished
   (`fees.py`), and inside the Kelly payoff rather than only at the gate.

4. **Sizing accounts for its own error.** Kelly on a lower confidence bound
   of p, plus per-market, per-cluster and portfolio-gross exposure budgets,
   so that six legs of one game cannot each claim a full position.

5. **Passive first, and honest about it.** When the spread is wide enough and
   the clock allows, quote inside it rather than paying it -- but price the
   trade on the belief that survives *being filled*. A resting buy fills when
   someone chooses to sell into it, which is mostly when the price is on its
   way down, so the relevant probability is the one at the post-fill price.

A note on what is *not* claimed. The log-loss opinion pool in `blend.py` is
still fitted and still reported as this bot's forecast, because it produces
the better Brier score. It is deliberately **not** used to size trades. Doing
both was a real and expensive mistake: the pool weight `a` and the shrinkage
slope `k` are two estimates of the same quantity, so pooling and then
shrinking applied the correction twice and the bot believed roughly a fifth
of its own signal. Removing the double-shrinkage was worth about $59 per
round in the mixed regime.

Every threshold below is a parameter in the transfer vector, so the
tournament's cross-learning can tune them and the incumbent can adopt them.
"""

from __future__ import annotations

import math

from ..blend import MarketPriorBlender, disagreement_penalty
from ..calibration import EdgeShrinkage, ReliabilityCurve, clamp01
from ..fees import trade_fee_cents_per_contract
from ..kelly import SizingLimits, drawdown_multiplier, size_position
from ..types import Order, OrderStyle, Side, Signal
from .base import Bot, BotAction, BotContext, Params

CHALLENGER_DEFAULTS = {
    "max_spread_cents": 6.0,
    "min_net_edge_cents": 1.2,
    # Edge must also clear this many standard deviations of the bot's own
    # calibration error. A 2c edge on a forecast the bot knows is +/-8c wide
    # is not an edge, it is noise with a sign.
    "edge_sigma_multiple": 0.20,
    # Pseudo-observations pinning the measured edge-shrinkage slope toward
    # zero. Higher = more evidence required before claimed edge is believed.
    "edge_shrink_ridge": 400.0,
    # Fit a separate shrinkage slope per liquidity tier. Off by default, and
    # the reason is a measured one rather than a hunch: splitting the sample
    # three ways made each slope noisy enough that the segmentation cost more
    # than the extra resolution bought. On the mixed regime it turned -$1.49
    # per round into -$17.58. The hierarchy is still here because with an
    # order of magnitude more history per tier it should win -- but it has to
    # earn the switch on evidence, which is what the tournament is for.
    "use_tier_shrinkage": False,
    "kelly_fraction": 0.30,
    "kelly_uncertainty_z": 1.0,
    "max_pct_per_market": 0.05,
    "max_pct_per_cluster": 0.12,
    "max_gross_exposure_pct": 0.35,
    "max_single_order": 25.0,
    "max_orders_per_cycle": 12.0,
    "cash_reserve_pct": 0.10,
    "min_volume": 0.0,
    "disagreement_cap_points": 0.45,
    # Extra edge demanded in thin markets, where the resting offer you are
    # lifting is more likely to belong to someone who knows something.
    "adverse_selection_cents": 1.2,
    "adverse_volume_scale": 300.0,
    # Execution
    "maker_min_spread": 2.0,
    "maker_ttl_ticks": 3.0,
    "maker_min_ticks_left": 4.0,
    # How far the market is assumed to have moved *through* a resting order
    # by the time it fills. See `_conditional_belief`.
    "maker_adverse_cents": 1.5,
    # Exits
    "exit_profit_cents": 2.0,
    "exit_stop_cents": 3.0,
    "min_ticks_to_open": 2.0,
    # Capability flags -- all on. Turning one off reproduces the incumbent's
    # behaviour for that component, which is how the ablation is run.
    "use_market_prior": True,
    "use_calibration": True,
    "use_exact_fees": True,
    "use_maker_first": True,
    # Off by default, on measured evidence rather than taste. Across 8 seeds
    # x 8 rounds, switching exits ON cost $13.87 per round (se $6.20). The
    # logic is sound in the abstract -- bank the edge once the price has come
    # to you -- but every exit pays a second taker fee and hands back the
    # remaining edge, and in this market that costs more than the variance it
    # removes. Kept and tested rather than deleted: with a maker-side exit,
    # or in a market with fatter tails, the sign could flip.
    "use_exits": False,
    "use_cluster_caps": True,
}

CHALLENGER_BOUNDS = {
    "max_spread_cents": (1.0, 12.0),
    "min_net_edge_cents": (0.0, 10.0),
    "edge_sigma_multiple": (0.0, 3.0),
    "edge_shrink_ridge": (25.0, 5000.0),
    "kelly_fraction": (0.02, 0.60),
    "kelly_uncertainty_z": (0.0, 3.0),
    "max_pct_per_market": (0.005, 0.20),
    "max_pct_per_cluster": (0.01, 0.50),
    "max_gross_exposure_pct": (0.05, 1.00),
    "max_single_order": (1.0, 50.0),
    "max_orders_per_cycle": (1.0, 40.0),
    "cash_reserve_pct": (0.0, 0.50),
    "min_volume": (0.0, 2000.0),
    "disagreement_cap_points": (0.05, 1.0),
    "adverse_selection_cents": (0.0, 5.0),
    "adverse_volume_scale": (10.0, 2000.0),
    "maker_min_spread": (1.0, 8.0),
    "maker_ttl_ticks": (1.0, 10.0),
    "maker_min_ticks_left": (1.0, 20.0),
    "maker_adverse_cents": (0.0, 6.0),
    "exit_profit_cents": (0.0, 15.0),
    "exit_stop_cents": (0.0, 20.0),
    "min_ticks_to_open": (0.0, 10.0),
}


class ChallengerBot(Bot):
    name = "challenger"

    def __init__(self, params: Params | None = None, seed: int = 0):
        super().__init__(
            params
            or Params(
                CHALLENGER_DEFAULTS,
                # Its identity: it does not give up the market prior. Every
                # other parameter is on the table for transfer.
                locked={"use_market_prior"},
                bounds=CHALLENGER_BOUNDS,
            ),
            seed=seed,
        )
        # Two curves, two jobs. `signal_curve` fixes a miscalibrated input
        # before it reaches the pool. `belief_curve` calibrates the pool's
        # output -- the number actually staked. Calibrating only the input
        # leaves the thing you bet on untested, and any bias the pooling
        # itself introduces goes straight into position size.
        self.signal_curve = ReliabilityCurve(n_bins=10, prior_strength=25.0)
        self.belief_curve = ReliabilityCurve(n_bins=12, prior_strength=40.0)
        self.blender = MarketPriorBlender()
        self._resting_ttl: dict = {}
        # Measured fraction of claimed edge that turns out to be real, both
        # pooled and per liquidity tier. Persists across rounds like the rest
        # of the learned state.
        self.edge_shrink = EdgeShrinkage(ridge=self.params["edge_shrink_ridge"])
        self.edge_shrink_by_tier: dict = {}
        self._last_tier: dict = {}
        self._tier_hint: str = "mid"

    # -- belief -----------------------------------------------------------

    def _calibrated_signal(self, sig: float) -> float:
        """The private view alone, corrected for its own measured bias."""
        p_sig = clamp01(sig)
        return self.signal_curve.correct(p_sig) if self.params["use_calibration"] else p_sig

    def _pool_at(self, p_market: float, p_sig_cal: float) -> float:
        """Pooled belief if the market price were `p_market`, pre-calibration."""
        p = self.params
        if not p["use_market_prior"]:
            return clamp01(p_sig_cal)
        p_blend = self.blender.blend(p_market, p_sig_cal)
        # Shrink a large disagreement toward the price. A 40-point gap
        # against a liquid market is nearly always a data or matching error
        # -- the incumbent learned this the expensive way and bolted on a
        # hard 25c cutoff; this is the smooth version.
        shrink = disagreement_penalty(p_market, p_sig_cal, p["disagreement_cap_points"])
        return clamp01(p_market + (p_blend - p_market) * shrink)

    def _finalize(self, p_pool: float) -> tuple:
        """Calibrate the pooled belief and report its uncertainty."""
        if not self.params["use_calibration"]:
            return clamp01(p_pool), 0.0
        return clamp01(self.belief_curve.correct(p_pool)), self.belief_curve.uncertainty(p_pool)

    def believe(self, snap, sig: float) -> tuple:
        """Return (p_final, sigma). Never trusts the signal on its own."""
        p_pool = self._pool_at(snap.mid / 100.0, self._calibrated_signal(sig))
        return self._finalize(p_pool)

    def _conditional_belief_yes(self, snap, sig: float, side: Side, limit_cents: int) -> float:
        """YES-frame belief *conditional on a resting order actually filling*.

        This is the correction that makes passive execution honest, and
        leaving it out is how a maker strategy quietly bleeds. A resting buy
        does not fill at a random moment: it fills when somebody chooses to
        sell into it, which is overwhelmingly when the price is on its way
        down. So the relevant probability is not `p` at today's mid, it is
        `p` at the price the market will be at once it has traded through
        you.

        The first version of this bot omitted it and lost 25c per contract
        resting orders in 85c+ markets -- buying the last few cents of upside
        precisely when the market had started disagreeing. Re-blending at the
        post-fill price makes those trades fail their own hurdle instead.
        """
        p_market_cond = self._conditional_market_cents(side, limit_cents) / 100.0
        p_sig_cal = self._calibrated_signal(sig)
        p_final, _sigma = self._finalize(self._pool_at(clamp01(p_market_cond), p_sig_cal))
        return p_final

    def _conditional_market_cents(self, side: Side, limit_cents: int) -> float:
        """The YES price the market is assumed to show once we are filled."""
        delta = self.params["maker_adverse_cents"]
        if side is Side.YES:
            yes_cents_after = limit_cents - delta
        else:
            # A resting NO buy at `limit` fills when NO cheapens, i.e. when
            # the YES price rises through 100 - limit.
            yes_cents_after = (100 - limit_cents) + delta
        return min(99.0, max(1.0, yes_cents_after))

    # -- decision ---------------------------------------------------------

    def decide(self, snapshots: dict, signals: dict, ctx: BotContext) -> BotAction:
        p = self.params
        action = BotAction()
        if ctx.killed:
            return action

        size_mult = drawdown_multiplier(ctx.equity_cents, ctx.high_water_cents)
        if size_mult <= 0:
            return action

        limits = SizingLimits(
            kelly_fraction=p["kelly_fraction"],
            max_pct_per_market=p["max_pct_per_market"],
            max_pct_per_cluster=p["max_pct_per_cluster"],
            max_gross_exposure_pct=p["max_gross_exposure_pct"],
            max_contracts_per_order=int(p["max_single_order"]),
            cash_reserve_pct=p["cash_reserve_pct"],
        )

        # Pass 1: form beliefs on the whole universe and re-estimate how much
        # genuine mispricing is around. The cross-section must be the
        # *unfiltered* one -- estimating the dispersion of true edges from
        # the markets that already passed a max-edge filter would measure the
        # selection, not the market.
        beliefs = {}
        for ticker, snap in snapshots.items():
            sig = signals.get(ticker)
            if sig is None:
                continue
            p_final, sigma = self.believe(snap, sig)
            beliefs[ticker] = (p_final, sigma)
            self._last_tier[ticker] = self.liquidity_tier(snap)
            action.signals[ticker] = Signal(
                ticker=ticker,
                p_signal=sig,
                p_final=p_final,
                confidence=1.0 - min(1.0, sigma * 4.0),
                source="blend" if p["use_market_prior"] else "calculator",
            )

        # Pass 2: price and size the trades.
        candidates = []
        for ticker, snap in snapshots.items():
            sig = signals.get(ticker)
            if sig is None or ticker not in beliefs:
                continue
            p_final, sigma = beliefs[ticker]

            if snap.volume < p["min_volume"] or not snap.two_sided:
                continue
            if snap.spread > p["max_spread_cents"]:
                continue
            if snap.ticks_to_close < p["min_ticks_to_open"]:
                continue

            self._tier_hint = self._last_tier.get(ticker, "mid")
            for side in (Side.YES, Side.NO):
                entry = self._entry_price(snap, side)
                if entry is None:
                    continue
                price, style, ttl = entry
                if not (1 <= price <= 99):
                    continue

                if style is OrderStyle.MAKE:
                    # Price the trade on the belief that survives being
                    # filled, not the one that looked good before.
                    ref_cents = self._conditional_market_cents(side, price)
                    p_yes_eff = self._trade_belief_yes(ref_cents / 100.0, sig)
                else:
                    ref_cents = snap.mid
                    p_yes_eff = self._trade_belief_yes(ref_cents / 100.0, sig)

                p_side = p_yes_eff if side is Side.YES else 1.0 - p_yes_eff

                fee = self._fee(1, price, style is OrderStyle.MAKE)
                net = p_side * 100.0 - price - fee

                # Two hurdles beyond "positive": a floor in cents, and a
                # floor in units of the bot's own measured error.
                hurdle = p["min_net_edge_cents"]
                hurdle += p["edge_sigma_multiple"] * sigma * 100.0
                hurdle += self._adverse_buffer(snap)
                if net < hurdle:
                    continue

                candidates.append(
                    (net - hurdle, ticker, snap, side, price, p_side, sigma, style, ttl)
                )

        candidates.sort(key=lambda c: -c[0])
        placed = 0
        cluster_used = dict(ctx.cluster_exposure)
        market_used: dict = {}
        gross_used = 0
        for (tk, _side), pos in ctx.positions.items():
            market_used[tk] = market_used.get(tk, 0) + pos.cost_basis_cents
            gross_used += pos.cost_basis_cents
        cash = ctx.cash_cents

        for margin, ticker, snap, side, price, p_side, sigma, style, ttl in candidates:
            if placed >= int(p["max_orders_per_cycle"]):
                break
            if ticker in self.traded_tickers and style is OrderStyle.TAKE:
                # Re-entering the same market by crossing again is how a bot
                # pays the spread twice for one idea.
                continue
            # One live quote per market. Without this a passive bot re-quotes
            # every tick, ends up with several overlapping resting orders on
            # the same idea, and discovers its per-market cap only after they
            # all fill at once.
            if self._resting_ttl.get(ticker, -1) > ctx.tick:
                continue

            cluster_exposure = cluster_used.get(snap.cluster, 0) if p["use_cluster_caps"] else 0
            contracts = size_position(
                p=p_side,
                price_cents=price,
                bankroll_cents=ctx.equity_cents,
                limits=limits,
                sigma=sigma,
                z=p["kelly_uncertainty_z"],
                size_multiplier=size_mult,
                cluster_exposure_cents=cluster_exposure,
                market_exposure_cents=market_used.get(ticker, 0),
                gross_exposure_cents=gross_used,
                available_cash_cents=cash,
                is_maker=style is OrderStyle.MAKE,
            )
            if contracts < 1:
                continue

            cost = contracts * price + self._fee(contracts, price, style is OrderStyle.MAKE)
            cash -= cost
            cluster_used[snap.cluster] = cluster_used.get(snap.cluster, 0) + contracts * price
            market_used[ticker] = market_used.get(ticker, 0) + contracts * price
            gross_used += contracts * price
            if style is OrderStyle.MAKE:
                self._resting_ttl[ticker] = ctx.tick + ttl
            else:
                self.traded_tickers.add(ticker)
            placed += 1
            action.orders.append(
                Order(
                    ticker=ticker,
                    side=side,
                    contracts=contracts,
                    limit_cents=price,
                    style=style,
                    p_est=p_side,
                    reason=f"net+{margin:.1f}c sigma={sigma:.03f}",
                    ttl_ticks=ttl,
                )
            )

        if p["use_exits"]:
            action.orders.extend(self._exits(snapshots, beliefs, ctx))
        return action

    # -- execution --------------------------------------------------------

    def _entry_price(self, snap, side: Side):
        """Choose passive or aggressive entry. Returns (price, style, ttl).

        Quoting inside the spread saves the spread but only fills when
        someone chooses to trade with you, which the simulator makes
        appropriately unpleasant. Crossing is certain and expensive. The rule
        is: quote when there is spread worth capturing and time to wait for
        it, cross when there is not.
        """
        p = self.params
        ask = snap.ask_for(side)
        bid = snap.bid_for(side)
        if p["use_maker_first"] and snap.spread >= p["maker_min_spread"] and snap.ticks_to_close >= p["maker_min_ticks_left"]:
            improve = bid + 1
            if improve < ask:
                return improve, OrderStyle.MAKE, int(p["maker_ttl_ticks"])
        return ask, OrderStyle.TAKE, 1

    def _fee(self, contracts: int, price: float, is_maker: bool) -> float:
        if self.params["use_exact_fees"]:
            return trade_fee_cents_per_contract(contracts, price, is_maker)
        return 7.0 * price * (100.0 - price) / 10000.0

    @staticmethod
    def liquidity_tier(snap) -> str:
        """Coarse read on how much the displayed price deserves to be trusted.

        A 1c spread with real size behind it is a price several people have
        argued about. A 6c spread on a market that has barely traded is one
        person's guess. Those two deserve different levels of scepticism
        about one's own edge, and a single global number cannot express that.
        """
        if snap.spread <= 2 and snap.volume >= 250:
            return "deep"
        if snap.spread >= 5 or snap.volume < 80:
            return "thin"
        return "mid"

    def _trade_belief_yes(self, p_market: float, sig: float) -> float:
        """The belief a *trade* is priced on: market anchor plus measured edge.

            belief = p_market + k * (calibrated_signal - p_market)

        This is still a market prior -- the price is the anchor and the
        signal only moves you off it -- but the weight `k` is the one fitted
        by regressing realised edge on claimed edge, not the `a` fitted by
        log-loss on forecasts.

        Using both was the mistake. `a` and `k` are two estimates of the same
        thing: how much of a disagreement with the price is real. Pooling
        with `a` and *then* shrinking the result by `k` applies the
        correction twice, so the bot ends up believing roughly `a*k` of its
        own signal -- around a fifth when each is around a half. Measured
        across 8 seeds, that double-shrinkage cost $59 per round in the mixed
        regime and $137 in the mispriced one, purely by leaving edge on the
        table it had correctly identified.

        The log-loss pool is still fitted and still reported as the bot's
        forecast, because it is the better *forecast* -- it is what the Brier
        numbers are scored on. It just no longer gets a second vote on size.
        """
        p_sig_cal = self._calibrated_signal(sig)
        if not self.params["use_market_prior"]:
            return clamp01(p_sig_cal)
        gap_cents = (p_sig_cal - p_market) * 100.0
        return clamp01(p_market + self._shrink_gap(gap_cents, self._tier_hint) / 100.0)

    def _shrink_gap(self, gap_cents: float, tier: str) -> float:
        """Scale a claimed disagreement by the fraction that has been real.

        Two levels: the tier's own measured slope, partially pooled toward
        the global one so a tier with little history behaves like the average
        rather than refusing to trade. See `EdgeShrinkage`.

        The shrinkage applies to the *gap against the market* -- the quantity
        that was estimated, and therefore the one that suffers the optimizer's
        curse. Fees and the price paid are known exactly and are subtracted
        afterwards, never shrunk.
        """
        pooled_k = self.edge_shrink.k
        if not self.params.get("use_tier_shrinkage"):
            return gap_cents * pooled_k
        seg = self.edge_shrink_by_tier.get(tier)
        if seg is None:
            return gap_cents * pooled_k
        return gap_cents * seg.k_toward(pooled_k)

    def _adverse_buffer(self, snap) -> float:
        """Extra edge demanded when there is little volume to hide behind.

        A resting offer in a market that has barely traded is far more likely
        to be there because its owner knows something. Thin books therefore
        need a wider hurdle, not the same one.
        """
        p = self.params
        return p["adverse_selection_cents"] / math.sqrt(
            1.0 + max(0, snap.volume) / max(1.0, p["adverse_volume_scale"])
        )

    # -- exits ------------------------------------------------------------

    def _exits(self, snapshots: dict, beliefs: dict, ctx: BotContext) -> list:
        p = self.params
        orders = []
        for (ticker, side), pos in ctx.positions.items():
            if pos.contracts <= 0:
                continue
            snap = snapshots.get(ticker)
            if snap is None:
                continue
            belief = beliefs.get(ticker)
            if belief is None:
                continue
            p_final, _ = belief
            p_side = p_final if side is Side.YES else 1.0 - p_final

            bid = snap.bid_for(side)
            if bid <= 0:
                continue
            exit_fee = trade_fee_cents_per_contract(pos.contracts, bid, is_maker=False)
            proceeds = bid - exit_fee
            hold_value = p_side * 100.0

            # Take profit: the market has come to us. The edge is already in
            # the price, and holding to settlement from here is an unpaid
            # variance bet.
            if proceeds - hold_value >= p["exit_profit_cents"]:
                orders.append(
                    Order(
                        ticker=ticker,
                        side=side,
                        contracts=pos.contracts,
                        limit_cents=bid,
                        style=OrderStyle.TAKE,
                        p_est=p_side,
                        reason="take profit",
                        is_exit=True,
                    )
                )
                continue

            # Stop: our own belief has moved against the position far enough
            # that selling at a bad price beats holding a worse one.
            entry_value = pos.p_est_at_entry * 100.0
            if entry_value - hold_value >= p["exit_stop_cents"] and proceeds > hold_value:
                orders.append(
                    Order(
                        ticker=ticker,
                        side=side,
                        contracts=pos.contracts,
                        limit_cents=bid,
                        style=OrderStyle.TAKE,
                        p_est=p_side,
                        reason="stop: belief moved against",
                        is_exit=True,
                    )
                )
        return orders

    # -- learning ---------------------------------------------------------

    def learn(self, res, p_market, p_signal, p_final) -> None:
        """Every resolution is a training example, traded or not.

        Order matters: the pool is refitted on the *pre-calibration* belief
        so the two stages do not chase each other. Calibrating an input that
        is itself being recalibrated against the same outcomes is how a
        learner ends up confidently fitting its own output.
        """
        if p_market is None:
            return
        p_sig_cal = self._calibrated_signal(p_signal) if p_signal is not None else None

        # How much edge did we claim here, and how much was actually there?
        # Recorded for every market observed, so the shrinkage slope is fit
        # on the whole universe rather than only on trades -- which is what
        # keeps it from being another selected sample.
        # Fit the shrinkage on exactly the quantity trades are priced on --
        # the calibrated signal's disagreement with the price. Fitting it on
        # the pooled belief instead would measure a number the trading path
        # no longer uses.
        if p_signal is not None:
            claimed = (p_sig_cal - p_market) * 100.0
            realised = (res.outcome - p_market) * 100.0
            self.edge_shrink.observe(claimed, realised)
            tier = self._last_tier.get(res.ticker)
            if tier:
                seg = self.edge_shrink_by_tier.get(tier)
                if seg is None:
                    seg = self.edge_shrink_by_tier[tier] = EdgeShrinkage(
                        ridge=self.params["edge_shrink_ridge"]
                    )
                seg.observe(claimed, realised)

        if self.params["use_market_prior"]:
            self.blender.update(p_market, p_sig_cal, res.outcome)
        if self.params["use_calibration"]:
            if p_signal is not None:
                self.signal_curve.observe(p_signal, res.outcome)
            self.belief_curve.observe(
                self._pool_at(p_market, p_sig_cal if p_sig_cal is not None else p_market),
                res.outcome,
            )

    def start_round(self, round_index: int) -> None:
        # Only the per-round bookkeeping resets. The calibration curve and
        # blend weights persist across rounds -- that is the knowledge the
        # tournament is meant to accumulate.
        self.traded_tickers.clear()
        self._resting_ttl.clear()

    def export_knowledge(self) -> dict:
        return {
            "curve": self.signal_curve.to_dict(),
            "belief_curve": self.belief_curve.to_dict(),
            "blender": self.blender.to_dict(),
            "edge_shrink": self.edge_shrink.to_dict(),
            "edge_shrink_by_tier": {
                k: v.to_dict() for k, v in self.edge_shrink_by_tier.items()
            },
        }

    def import_knowledge(self, blob: dict, weight: float = 1.0) -> None:
        if "curve" in blob:
            peer = ReliabilityCurve.from_dict(blob["curve"])
            if peer.n_bins == self.signal_curve.n_bins:
                self.signal_curve.merge(peer, weight)
        if "belief_curve" in blob:
            peer = ReliabilityCurve.from_dict(blob["belief_curve"])
            if peer.n_bins == self.belief_curve.n_bins:
                self.belief_curve.merge(peer, weight)
