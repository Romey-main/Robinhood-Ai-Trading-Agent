"""
The competition.

Rules, and why each one is what it is:

  **Identical worlds.** Every bot sees the same markets, the same books, the
  same signals, at the same ticks. Differences in the scoreboard are
  differences in decisions.

  **Independent books.** Each bot trades against its own copy of the order
  book. Sharing one book would mean whichever bot the loop polls first eats
  the only three contracts at the touch, and the winner would be partly
  decided by iteration order.

  **No ground truth reaches a bot.** Snapshots are stripped of `p_true` and
  `outcome` before they leave the feed (`feed.public_view`). Not a
  convention -- a copy.

  **Bankroll resets every round, knowledge does not.** Rounds are meant to be
  comparable samples, and compounding would let one lucky early round
  dominate the series. What carries forward is what the bots have *learned*:
  calibration curves, blend weights, tuned parameters, granted capabilities.
  That is the thing being built.

  **Learning happens between rounds, never inside one.** A round is a clean
  measurement; changing the bots mid-measurement would make the score
  uninterpretable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..feed import SyntheticConfig, SyntheticFeed, public_view
from ..exchange_sim import ExchangeConfig, PaperExchange
from ..portfolio import Portfolio
from ..scoring import Scorecard, head_to_head, price_bucket, score_round, spread_bucket
from ..types import RoundResult, Side
from .knowledge import KnowledgeBase
from .lessons import mine_all, render_markdown
from .transfer import TransferConfig, cross_learn


@dataclass
class TournamentConfig:
    rounds: int = 6
    bankroll_dollars: float = 1000.0
    n_markets: int = 150
    n_ticks: int = 60
    regime: str = "mixed"
    seed: int = 7
    learning: bool = True
    kb_path: str = "knowledge/kb.sqlite"
    lessons_path: str = "knowledge/LESSONS.md"
    results_path: str = "knowledge/results.json"
    transfer: TransferConfig = field(default_factory=TransferConfig)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)


def _attribute_pnl(fills: list, resolutions: dict) -> dict:
    """Realised P&L per entry fill, honouring early exits.

    Charging an entry with the settlement outcome would misprice every
    position the bot closed before expiry -- which for a bot with exits is
    most of them. Grouping by (ticker, side) and distributing the group's
    realised total across its entries keeps the totals exact and the
    attribution fair.
    """
    groups: dict = {}
    for f in fills:
        groups.setdefault((f.ticker, f.side), {"entries": [], "exits": []})
        groups[(f.ticker, f.side)]["exits" if f.is_exit else "entries"].append(f)

    out: dict = {}
    for (ticker, side), g in groups.items():
        entries, exits = g["entries"], g["exits"]
        if not entries:
            continue
        entry_contracts = sum(f.contracts for f in entries)
        if entry_contracts <= 0:
            continue
        cost = sum(f.contracts * f.price_cents + f.fee_cents for f in entries)
        proceeds = sum(f.contracts * f.price_cents - f.fee_cents for f in exits)
        exited = sum(f.contracts for f in exits)
        remaining = max(0, entry_contracts - exited)

        res = resolutions.get(ticker)
        if res is not None and remaining:
            won = (res.outcome == 1) if side is Side.YES else (res.outcome == 0)
            proceeds += 100 * remaining if won else 0
        total = proceeds - cost
        per_contract = total / entry_contracts
        for f in entries:
            out[id(f)] = (total * f.contracts / entry_contracts, per_contract)
    return out


class ViewLog:
    """A temporally spread sample of a bot's views of one market.

    Why not just keep the latest view: a market's last snapshot before it
    resolves is the one where the price is *already* nearly right. Training
    the blend weights only on those triples teaches the bot that the market
    is essentially perfect -- because at t-1 it very nearly is -- and drives
    the weight on its own signal toward zero regardless of how good that
    signal is earlier in the market's life. That is a bug in the *evaluation
    harness* masquerading as a finding about the bot.

    So views are kept spread across the whole life of the market. When the
    buffer fills, every second entry is dropped, which halves the resolution
    uniformly and keeps the earliest and latest views. The forecast used for
    *scoring* is the earliest one, since scoring a forecaster on near-settled
    markets flatters everybody and separates nobody.
    """

    def __init__(self, cap: int = 8):
        self.views = []
        self.cap = cap
        self._stride = 1
        self._seen = 0

    def add(self, view) -> None:
        self._seen += 1
        if (self._seen - 1) % self._stride == 0:
            self.views.append(view)
            if len(self.views) > self.cap:
                self.views = self.views[::2]
                self._stride *= 2

    @property
    def first(self):
        return self.views[0] if self.views else None

    @property
    def last(self):
        return self.views[-1] if self.views else None


def run_round(bots: list, cfg: TournamentConfig, round_index: int) -> tuple:
    """Run one round. Returns (results_by_bot, truth, fill_context)."""
    feed = SyntheticFeed(
        SyntheticConfig(
            n_markets=cfg.n_markets,
            n_ticks=cfg.n_ticks,
            regime=cfg.regime,
        ),
        seed=cfg.seed * 1000 + round_index,
    )
    truth = feed.truth
    bankroll = int(round(cfg.bankroll_dollars * 100))

    state = {}
    for i, bot in enumerate(bots):
        bot.start_round(round_index)
        pf = Portfolio(cash_cents=bankroll, start_cents=bankroll)
        ex = PaperExchange(pf, cfg.exchange, seed=cfg.seed * 100 + round_index * 10 + i)
        state[bot.name] = {
            "bot": bot,
            "pf": pf,
            "ex": ex,
            "result": RoundResult(
                bot=bot.name,
                round_index=round_index,
                start_bankroll_cents=bankroll,
                end_bankroll_cents=bankroll,
                params=bot.params.to_dict(),
            ),
            "views": {},  # ticker -> ViewLog
        }

    fill_context: dict = {}  # id(fill) -> {"spread":, "mid":, "ticker":}
    resolutions: dict = {}

    for tick in feed.ticks():
        public = {tk: public_view(s) for tk, s in tick.snapshots.items()}

        for st in state.values():
            bot, pf, ex, result = st["bot"], st["pf"], st["ex"], st["result"]

            # Resting orders meet the new book before any new decision is
            # made. A bot must live with the quotes it left out there.
            for f in ex.match_resting(public, tick.t):
                snap = public.get(f.ticker)
                fill_context[id(f)] = {
                    "spread": snap.spread if snap else 0,
                    "mid": snap.mid if snap else 0.0,
                }
                bot.on_fill(f)

            equity = pf.mark_to_market_cents(public)
            pf.touch_high_water(equity)

            from ..bots.base import BotContext

            cluster_exposure: dict = {}
            for pos in pf.positions.values():
                if pos.cluster:
                    cluster_exposure[pos.cluster] = (
                        cluster_exposure.get(pos.cluster, 0) + pos.cost_basis_cents
                    )

            ctx = BotContext(
                cash_cents=pf.cash_cents,
                equity_cents=equity,
                high_water_cents=pf.high_water_cents,
                start_cents=pf.start_cents,
                positions=dict(pf.positions),
                cluster_exposure=cluster_exposure,
                tick=tick.t,
            )

            action = bot.decide(public, tick.signals, ctx)

            for ticker, sig in action.signals.items():
                snap = public.get(ticker)
                if snap is None:
                    continue
                log = st["views"].get(ticker)
                if log is None:
                    log = st["views"][ticker] = ViewLog()
                log.add((snap.mid / 100.0, sig.p_signal, sig.p_final))

            for f in ex.submit(action.orders, public, tick.t):
                snap = public.get(f.ticker)
                fill_context[id(f)] = {
                    "spread": snap.spread if snap else 0,
                    "mid": snap.mid if snap else 0.0,
                }
                bot.on_fill(f)

            result.equity_curve.append(pf.mark_to_market_cents(public))

        # Settlement, then learning from what settled.
        if tick.resolutions:
            for res in tick.resolutions:
                resolutions[res.ticker] = res
            for st in state.values():
                st["ex"].settle(tick.resolutions)
                for res in tick.resolutions:
                    log = st["views"].get(res.ticker)
                    if log is None or not log.views:
                        continue
                    # Learn from the whole life of the market...
                    for p_market, p_signal, p_final in log.views:
                        st["bot"].learn(res, p_market, p_signal, p_final)
                    # ...but score the opening forecast, where knowing
                    # something actually distinguishes you from the crowd.
                    p_market, p_signal, p_final = log.first
                    st["result"].forecasts.append(
                        (res.ticker, p_final, p_market, p_signal, res.outcome)
                    )

    results = {}
    for name, st in state.items():
        pf, result = st["pf"], st["result"]
        result.end_bankroll_cents = pf.cash_cents + sum(
            p.cost_basis_cents for p in pf.positions.values()
        )
        result.fills = list(pf.fills)
        st["bot"].end_round(round_index)
        results[name] = result
    return results, truth, fill_context, resolutions


def record_round(
    kb: KnowledgeBase,
    round_index: int,
    results: dict,
    scorecards: dict,
    truth: dict,
    fill_context: dict,
    resolutions: dict,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    for name, result in results.items():
        kb.record_round(round_index, name, result.params, scorecards[name].summary_row(), ts)

        obs = []
        for ticker, p_final, p_market, p_signal, outcome in result.forecasts:
            t = truth.get(ticker, {})
            obs.append(
                (
                    round_index,
                    name,
                    ticker,
                    "",
                    p_market,
                    p_signal,
                    p_final,
                    outcome,
                    "inefficient" if t.get("inefficient") else "efficient",
                )
            )
        if obs:
            kb.record_observations(obs)

        attributed = _attribute_pnl(result.fills, resolutions)
        rows = []
        for f in result.fills:
            ctxf = fill_context.get(id(f), {})
            res = resolutions.get(f.ticker)
            pnl, per_ct = attributed.get(id(f), (0.0, 0.0))
            t = truth.get(f.ticker, {})
            rows.append(
                (
                    round_index,
                    name,
                    f.ticker,
                    f.side.value,
                    f.price_cents,
                    f.contracts,
                    f.fee_cents,
                    1 if f.is_maker else 0,
                    1 if f.is_exit else 0,
                    price_bucket(f.price_cents),
                    spread_bucket(int(ctxf.get("spread", 0))),
                    "inefficient" if t.get("inefficient") else "efficient",
                    res.outcome if res else None,
                    pnl,
                    per_ct,
                )
            )
        if rows:
            kb.record_trades(rows)


def run_tournament(bots: list, cfg: TournamentConfig | None = None) -> dict:
    cfg = cfg or TournamentConfig()
    kb = KnowledgeBase(cfg.kb_path)
    history = []

    for r in range(cfg.rounds):
        results, truth, fill_context, resolutions = run_round(bots, cfg, r)

        scorecards = {}
        for name, result in results.items():
            scorecards[name] = score_round(result, resolutions, {})

        record_round(kb, r, results, scorecards, truth, fill_context, resolutions)
        for bot in bots:
            kb.record_blob(r, bot.name, bot.export_knowledge())

        lessons = mine_all(kb, r, [b.name for b in bots])
        transfers = []
        if cfg.learning and r < cfg.rounds - 1:
            # No transfer after the final round: nothing would ever measure
            # its effect, and an unmeasured change is not learning.
            transfers = cross_learn(kb, r, bots, lessons, cfg.transfer)

        history.append(
            {
                "round": r,
                "scores": {n: sc.summary_row() for n, sc in scorecards.items()},
                "head_to_head": _pairwise(scorecards),
                "lessons": [
                    {
                        "kind": ls.kind,
                        "scope": ls.scope,
                        "source": ls.source_bot,
                        "statement": ls.statement,
                        "n": ls.support_n,
                        "p": ls.p_value,
                        "confidence": ls.confidence,
                    }
                    for ls in lessons
                ],
                "transfers": transfers,
                "params": {b.name: b.params.to_dict() for b in bots},
                "divergence": _divergence(bots),
            }
        )

    Path(cfg.lessons_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.lessons_path).write_text(render_markdown(kb))

    report = {
        "config": {
            "rounds": cfg.rounds,
            "bankroll_$": cfg.bankroll_dollars,
            "n_markets": cfg.n_markets,
            "n_ticks": cfg.n_ticks,
            "regime": cfg.regime,
            "seed": cfg.seed,
            "learning": cfg.learning,
        },
        "history": history,
        "totals": _totals(history),
    }
    Path(cfg.results_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.results_path).write_text(json.dumps(report, indent=2, default=str))
    kb.close()
    return report


def _pairwise(scorecards: dict) -> dict:
    names = sorted(scorecards)
    out = {}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            out[f"{a}_vs_{b}"] = head_to_head(scorecards[a], scorecards[b])
    return out


def _divergence(bots: list) -> dict:
    out = {}
    for i, a in enumerate(bots):
        for b in bots[i + 1 :]:
            out[f"{a.name}|{b.name}"] = round(a.params.distance(b.params), 4)
    return out


def _totals(history: list) -> dict:
    totals: dict = {}
    for row in history:
        for name, sc in row["scores"].items():
            t = totals.setdefault(
                name,
                {"pnl_$": 0.0, "trades": 0, "contracts": 0, "fees_$": 0.0,
                 "rounds_won": 0, "brier": [], "brier_skill": []},
            )
            t["pnl_$"] += sc["pnl_$"]
            t["trades"] += sc["trades"]
            t["contracts"] += sc["contracts"]
            t["fees_$"] += sc["fees_$"]
            if sc.get("brier") is not None:
                t["brier"].append(sc["brier"])
            if sc.get("brier_skill_vs_mkt") is not None:
                t["brier_skill"].append(sc["brier_skill_vs_mkt"])
        best = max(row["scores"].items(), key=lambda kv: kv[1]["pnl_$"])[0]
        totals[best]["rounds_won"] += 1
    for t in totals.values():
        t["pnl_$"] = round(t["pnl_$"], 2)
        t["fees_$"] = round(t["fees_$"], 2)
        t["mean_brier"] = round(sum(t["brier"]) / len(t["brier"]), 4) if t["brier"] else None
        t["mean_brier_skill"] = (
            round(sum(t["brier_skill"]) / len(t["brier_skill"]), 4) if t["brier_skill"] else None
        )
        t.pop("brier")
        t.pop("brier_skill")
    return totals
