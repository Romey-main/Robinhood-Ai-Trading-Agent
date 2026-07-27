"""
Bot A -- "Incumbent". A faithful port of the original kalshi-bot's policy.

This is not a strawman. The original is a competent piece of work and the
port keeps the things it gets right:

  * fees are checked before entry, not after
  * fractional Kelly rather than flat sizing
  * a liquidity filter that refuses wide or one-sided books
  * a drawdown ladder and a daily-loss kill switch
  * a sanity backstop that drops implausibly large "edges" as data errors
  * a performance gate that mutes a category once it has proven unprofitable

What is faithfully preserved -- because these are the design decisions the
challenger is meant to test, and softening them would rig the match:

  * `p_est` comes straight from the calculator. The market price is treated
    as the thing to beat, never as evidence.
  * the fee model is the smooth per-contract formula, without the per-order
    ceiling
  * net edge subtracts a half-spread on top of paying the ask
  * every entry crosses the spread
  * there are no exits: positions are opened and held to settlement
  * one trade per ticker per session
  * learning happens only through realised P&L on its own trades

Mapping to the original source, for anyone checking the port:
  bot.py:487-655 (entry loop), strategies/sizing.py (fees, Kelly, grading),
  strategies/risk_manager.py (ladder, kill switch), tracker/performance.py
  (category muting).
"""

from __future__ import annotations

from ..calibration import clamp01
from ..types import Order, OrderStyle, Side, Signal
from .base import Bot, BotAction, BotContext, Params

INCUMBENT_DEFAULTS = {
    "max_spread_cents": 6.0,
    "min_net_edge_a": 1.5,
    "min_net_edge_b": 2.5,
    "kelly_fraction": 0.18,
    "max_pct_per_market": 0.07,
    "max_single_order": 5.0,
    "max_orders_per_cycle": 10.0,
    "min_volume": 100.0,
    "min_edge_cents": 2.0,
    "max_sane_edge_cents": 25.0,
    "grade_a_threshold": 0.70,
    "daily_loss_pct": 0.08,
    "perf_min_sample": 10.0,
    "perf_mute_roi": -10.0,
    # Capability flags. All off: this is the original's behaviour. They are
    # the switches the tournament can flip if -- and only if -- the evidence
    # from watching the challenger justifies it.
    "use_market_prior": False,
    "use_calibration": False,
    "use_exact_fees": False,
    "use_maker_first": False,
    "use_exits": False,
    "use_cluster_caps": False,
}

INCUMBENT_BOUNDS = {
    "max_spread_cents": (1.0, 12.0),
    "min_net_edge_a": (0.0, 10.0),
    "min_net_edge_b": (0.0, 12.0),
    "kelly_fraction": (0.02, 0.60),
    "max_pct_per_market": (0.005, 0.20),
    "max_single_order": (1.0, 50.0),
    "max_orders_per_cycle": (1.0, 40.0),
    "min_volume": (0.0, 2000.0),
    "min_edge_cents": (0.0, 10.0),
    "max_sane_edge_cents": (5.0, 60.0),
    "grade_a_threshold": (0.55, 0.90),
    "daily_loss_pct": (0.02, 0.30),
    "perf_min_sample": (3.0, 50.0),
    "perf_mute_roi": (-50.0, 0.0),
}


def smooth_fee_cents(price_cents: float) -> float:
    """The original's fee model: 0.07 * P * (1-P), no per-order rounding."""
    return 7.0 * price_cents * (100.0 - price_cents) / 10000.0


def kelly_contracts(
    p_est: float,
    price_cents: float,
    bankroll_cents: int,
    kelly_fraction: float,
    max_pct_per_market: float,
    max_contracts: int,
) -> int:
    """Port of strategies/sizing.py::kelly_contracts, in cents."""
    c = price_cents / 100.0
    if c <= 0 or c >= 1 or bankroll_cents <= 0:
        return 0
    f_star = p_est - (1.0 - p_est) * c / (1.0 - c)
    if f_star <= 0:
        return 0
    stake = kelly_fraction * f_star * bankroll_cents
    stake = min(stake, max_pct_per_market * bankroll_cents)
    contracts = int(stake / price_cents)
    if contracts == 0 and price_cents <= 0.10 * bankroll_cents:
        contracts = 1  # small-account floor, as in the original
    return max(0, min(contracts, max_contracts))


class IncumbentBot(Bot):
    name = "incumbent"

    def __init__(self, params: Params | None = None, seed: int = 0):
        super().__init__(
            params
            or Params(
                INCUMBENT_DEFAULTS,
                locked={"use_market_prior"},  # its identity: signal-primary
                bounds=INCUMBENT_BOUNDS,
            ),
            seed=seed,
        )
        # Category P&L, the original's only learning channel.
        self._cat_pnl: dict = {}
        self._cat_staked: dict = {}
        self._cat_n: dict = {}
        self._entry_by_ticker: dict = {}
        # Populated only if the tournament grants the calibration capability.
        self._curve = None
        self._blender = None

    # -- decision ---------------------------------------------------------

    def decide(self, snapshots: dict, signals: dict, ctx: BotContext) -> BotAction:
        p = self.params
        action = BotAction()
        if ctx.killed:
            return action

        size_mult, a_only = self._ladder(ctx)
        muted = self._muted_categories()
        bankroll = ctx.equity_cents

        candidates = []
        for ticker, snap in snapshots.items():
            sig = signals.get(ticker)
            if sig is None:
                continue

            p_est = self._believe(sig, snap)
            action.signals[ticker] = Signal(
                ticker=ticker,
                p_signal=sig,
                p_final=p_est,
                confidence=1.0,
                source="calculator",
            )

            if ticker in self.traded_tickers:
                continue
            if snap.volume < p["min_volume"]:
                continue
            if not snap.two_sided:
                continue
            if snap.spread > p["max_spread_cents"]:
                continue

            for side in (Side.YES, Side.NO):
                price = snap.ask_for(side)
                if not (2 <= price <= 98):
                    continue
                p_side = p_est if side is Side.YES else 1.0 - p_est
                raw_edge = p_side * 100.0 - price
                if raw_edge < p["min_edge_cents"]:
                    continue
                if abs(raw_edge) > p["max_sane_edge_cents"]:
                    continue  # treated as a data/match error, never traded
                candidates.append((raw_edge, ticker, snap, side, price, p_side))

        candidates.sort(key=lambda c: -c[0])
        placed = 0
        for raw_edge, ticker, snap, side, price, p_side in candidates:
            if placed >= int(p["max_orders_per_cycle"]):
                break
            if ticker in self.traded_tickers:
                continue

            category = snap.series
            if category in muted:
                continue

            if p["use_exact_fees"]:
                from ..fees import trade_fee_cents_per_contract

                fee = trade_fee_cents_per_contract(1, price, is_maker=False)
                net = raw_edge - fee
            else:
                # Original: smooth fee AND a half-spread, on top of paying
                # the ask. Conservative, and not the edge it actually books.
                net = raw_edge - smooth_fee_cents(price) - snap.spread / 2.0

            grade = "A" if (p_side >= p["grade_a_threshold"] or p_side <= 1 - p["grade_a_threshold"]) else "B"
            if a_only and grade != "A":
                continue
            min_edge = p["min_net_edge_a"] if grade == "A" else p["min_net_edge_b"]
            if net < min_edge:
                continue

            cluster_exposure = ctx.cluster_exposure.get(snap.cluster, 0)
            if p["use_cluster_caps"] and cluster_exposure > 0.12 * bankroll:
                continue

            contracts = kelly_contracts(
                p_side,
                price,
                bankroll,
                p["kelly_fraction"],
                p["max_pct_per_market"],
                int(p["max_single_order"]),
            )
            contracts = int(contracts * size_mult)
            if contracts < 1:
                continue

            style = OrderStyle.TAKE
            limit = price
            if p["use_maker_first"] and snap.spread >= 2:
                style = OrderStyle.MAKE
                limit = snap.bid_for(side) + 1

            self.traded_tickers.add(ticker)
            self._entry_by_ticker[ticker] = (category, price, contracts)
            action.orders.append(
                Order(
                    ticker=ticker,
                    side=side,
                    contracts=contracts,
                    limit_cents=limit,
                    style=style,
                    p_est=p_side,
                    reason=f"{grade} net={net:.1f}c",
                    ttl_ticks=2 if style is OrderStyle.MAKE else 1,
                )
            )
            placed += 1

        if p["use_exits"]:
            action.orders.extend(self._exits(snapshots, signals, ctx))
        return action

    def _believe(self, sig: float, snap) -> float:
        """The original believes its calculator, full stop.

        The capability flags let the tournament grant it the challenger's
        machinery later -- which is the point of the whole exercise -- but
        with everything off this returns the raw signal, unchanged.
        """
        p_est = clamp01(sig)
        if self.params["use_calibration"] and self._curve is not None:
            p_est = self._curve.apply(p_est)
        if self.params["use_market_prior"] and self._blender is not None:
            p_est = self._blender.blend(snap.mid / 100.0, p_est)
        return p_est

    def _exits(self, snapshots: dict, signals: dict, ctx: BotContext) -> list:
        """Only reachable once the `use_exits` capability has been granted."""
        from ..fees import trade_fee_cents_per_contract

        orders = []
        for (ticker, side), pos in ctx.positions.items():
            if pos.contracts <= 0:
                continue
            snap = snapshots.get(ticker)
            sig = signals.get(ticker)
            if snap is None or sig is None:
                continue
            p_now = self._believe(sig, snap)
            p_side = p_now if side is Side.YES else 1.0 - p_now
            bid = snap.bid_for(side)
            fee = trade_fee_cents_per_contract(pos.contracts, bid, is_maker=False)
            if bid - fee > p_side * 100.0 + 1.0:
                orders.append(
                    Order(
                        ticker=ticker,
                        side=side,
                        contracts=pos.contracts,
                        limit_cents=bid,
                        style=OrderStyle.TAKE,
                        p_est=p_side,
                        reason="exit: price above belief",
                        is_exit=True,
                    )
                )
        return orders

    # -- risk -------------------------------------------------------------

    def _ladder(self, ctx: BotContext) -> tuple:
        """The original's -10 / -15 / -20 step ladder."""
        hw = ctx.high_water_cents or ctx.start_cents
        if hw <= 0:
            return 1.0, False
        dd = (ctx.equity_cents - hw) / hw
        if dd <= -0.20:
            return 0.0, True
        if dd <= -0.15:
            return 0.5, True
        if dd <= -0.10:
            return 0.5, False
        return 1.0, False

    def _muted_categories(self) -> set:
        p = self.params
        muted = set()
        for cat, n in self._cat_n.items():
            if n < p["perf_min_sample"]:
                continue
            staked = self._cat_staked.get(cat, 0) or 1
            roi = 100.0 * self._cat_pnl.get(cat, 0) / staked
            if roi < p["perf_mute_roi"]:
                muted.add(cat)
        return muted

    # -- learning ---------------------------------------------------------

    def learn(self, res, p_market, p_signal, p_final) -> None:
        """Realised P&L per category -- the original's only feedback loop.

        Note what is *not* here: the resolution of a market it did not trade
        teaches it nothing. That is the difference the challenger exploits.
        """
        entry = self._entry_by_ticker.pop(res.ticker, None)
        if entry is None:
            # Untraded market. Wire up the shared learning channels only if
            # the corresponding capability has been granted.
            if self.params["use_calibration"] and self._curve is not None and p_signal is not None:
                self._curve.observe(p_signal, res.outcome)
            if self.params["use_market_prior"] and self._blender is not None and p_market is not None:
                self._blender.update(p_market, p_signal, res.outcome)
            return
        category, price, contracts = entry
        payout = 100 * contracts if res.outcome == 1 else 0
        pnl = payout - price * contracts
        self._cat_pnl[category] = self._cat_pnl.get(category, 0) + pnl
        self._cat_staked[category] = self._cat_staked.get(category, 0) + price * contracts
        self._cat_n[category] = self._cat_n.get(category, 0) + 1
        if self.params["use_calibration"] and self._curve is not None and p_signal is not None:
            self._curve.observe(p_signal, res.outcome)
        if self.params["use_market_prior"] and self._blender is not None and p_market is not None:
            self._blender.update(p_market, p_signal, res.outcome)

    def grant_capability(self, name: str) -> None:
        """Turn on a capability learned from the peer, wiring its machinery."""
        if name in self.params.values:
            self.params[name] = True
        if name == "use_calibration" and self._curve is None:
            from ..calibration import ReliabilityCurve

            self._curve = ReliabilityCurve()
        if name == "use_market_prior" and self._blender is None:
            from ..blend import MarketPriorBlender

            self._blender = MarketPriorBlender()

    def export_knowledge(self) -> dict:
        blob = {}
        if self._curve is not None:
            blob["curve"] = self._curve.to_dict()
        if self._blender is not None:
            blob["blender"] = self._blender.to_dict()
        return blob

    def import_knowledge(self, blob: dict, weight: float = 1.0) -> None:
        from ..blend import MarketPriorBlender
        from ..calibration import ReliabilityCurve

        if "curve" in blob and self.params["use_calibration"]:
            peer = ReliabilityCurve.from_dict(blob["curve"])
            if self._curve is None:
                self._curve = ReliabilityCurve()
            self._curve.merge(peer, weight)
        if "blender" in blob and self.params["use_market_prior"]:
            if self._blender is None:
                self._blender = MarketPriorBlender.from_dict(blob["blender"])
