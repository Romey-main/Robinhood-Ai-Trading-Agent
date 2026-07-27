"""
Scoring.

P&L alone is a bad scoreboard for a prediction-market bot over any sample a
human is willing to wait for. Two bots can differ by hundreds of dollars on
pure variance, and the one that got lucky will look like the one that was
right. So the arena grades four separate things:

  **Money**  -- P&L, return, max drawdown, Sharpe. What actually happened.

  **Forecasting** -- Brier, ECE, and above all *Brier skill against the
      market price*. This is the number that says whether a bot knows
      anything. A bot with zero skill can still print money for a while by
      taking variance; a bot with positive skill and bad execution is a
      fixable problem. Only this metric distinguishes them.

  **Execution** -- fees paid per contract, maker fill share, realised
      slippage against the mid at decision time. This is where the incumbent
      quietly loses: every entry crosses the spread and pays taker fees, and
      nothing in its reporting makes that visible.

  **Discipline** -- how often it traded when no edge existed. In the
      efficient regime the correct trade count is zero and every trade is a
      donation, so trade count in that regime is a direct measure of
      overtrading.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .calibration import brier, brier_skill_vs_market, expected_calibration_error, log_loss


@dataclass
class Scorecard:
    bot: str
    round_index: int = 0
    start_cents: int = 0
    end_cents: int = 0
    n_fills: int = 0
    n_contracts: int = 0
    n_exits: int = 0
    fees_cents: int = 0
    maker_fills: int = 0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    brier: float = float("nan")
    brier_skill: float = float("nan")
    ece: float = float("nan")
    log_loss: float = float("nan")
    n_forecasts: int = 0
    settled_trades: int = 0
    win_rate: float = float("nan")
    avg_edge_per_contract: float = float("nan")
    segments: dict = field(default_factory=dict)

    @property
    def pnl_cents(self) -> int:
        return self.end_cents - self.start_cents

    @property
    def return_pct(self) -> float:
        return 100.0 * self.pnl_cents / self.start_cents if self.start_cents else 0.0

    @property
    def fees_per_contract(self) -> float:
        return self.fees_cents / self.n_contracts if self.n_contracts else 0.0

    @property
    def maker_share(self) -> float:
        return self.maker_fills / self.n_fills if self.n_fills else 0.0

    def summary_row(self) -> dict:
        return {
            "bot": self.bot,
            "round": self.round_index,
            "pnl_$": round(self.pnl_cents / 100.0, 2),
            "return_%": round(self.return_pct, 2),
            "maxDD_%": round(self.max_drawdown_pct, 2),
            "sharpe": round(self.sharpe, 3),
            "trades": self.n_fills,
            "contracts": self.n_contracts,
            "fees_$": round(self.fees_cents / 100.0, 2),
            "fee_per_ct_c": round(self.fees_per_contract, 2),
            "maker_%": round(100.0 * self.maker_share, 1),
            "brier": None if _nan(self.brier) else round(self.brier, 4),
            "brier_skill_vs_mkt": None if _nan(self.brier_skill) else round(self.brier_skill, 4),
            "ece": None if _nan(self.ece) else round(self.ece, 4),
            "win_%": None if _nan(self.win_rate) else round(100.0 * self.win_rate, 1),
        }


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def max_drawdown_pct(equity: list) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    worst = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return 100.0 * worst


def sharpe_from_equity(equity: list) -> float:
    """Mean/SD of per-tick equity returns. Unannualised on purpose: the
    tick is an arbitrary unit here, and scaling it by a made-up number of
    periods per year would manufacture precision the data does not have."""
    if len(equity) < 3:
        return 0.0
    rets = []
    for a, b in zip(equity, equity[1:]):
        if a > 0:
            rets.append((b - a) / a)
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    return mu / sd if sd > 0 else 0.0


def price_bucket(price_cents: float) -> str:
    if price_cents < 15:
        return "0-15c"
    if price_cents < 35:
        return "15-35c"
    if price_cents < 65:
        return "35-65c"
    if price_cents < 85:
        return "65-85c"
    return "85-100c"


def spread_bucket(spread: int) -> str:
    if spread <= 1:
        return "tight"
    if spread <= 3:
        return "normal"
    return "wide"


def score_round(result, resolutions: dict, books_at_entry: dict) -> Scorecard:
    """Turn one bot's round into a scorecard.

    `resolutions`: ticker -> Resolution
    `books_at_entry`: (ticker, tick) -> mid price in cents, for slippage
    """
    sc = Scorecard(
        bot=result.bot,
        round_index=result.round_index,
        start_cents=result.start_bankroll_cents,
        end_cents=result.end_bankroll_cents,
    )

    sc.max_drawdown_pct = max_drawdown_pct(result.equity_curve)
    sc.sharpe = sharpe_from_equity(result.equity_curve)

    # -- execution + trade attribution ------------------------------------
    seg_pnl: dict = {}
    seg_n: dict = {}
    edge_total = 0.0
    edge_n = 0
    wins = 0
    settled = 0

    for fill in result.fills:
        sc.n_fills += 1
        sc.n_contracts += fill.contracts
        sc.fees_cents += fill.fee_cents
        if fill.is_maker:
            sc.maker_fills += 1
        if fill.is_exit:
            sc.n_exits += 1
            continue

        res = resolutions.get(fill.ticker)
        if res is None:
            continue
        settled += 1
        won = (res.outcome == 1) if fill.side.value == "yes" else (res.outcome == 0)
        if won:
            wins += 1
        gross = (100 - fill.price_cents) if won else (-fill.price_cents)
        pnl_c = gross * fill.contracts - fill.fee_cents
        edge_total += pnl_c
        edge_n += fill.contracts

        key = (
            price_bucket(fill.price_cents),
            spread_bucket(0),
            "maker" if fill.is_maker else "taker",
        )
        seg_pnl[key] = seg_pnl.get(key, 0) + pnl_c
        seg_n[key] = seg_n.get(key, 0) + fill.contracts

    sc.settled_trades = settled
    sc.win_rate = (wins / settled) if settled else float("nan")
    sc.avg_edge_per_contract = (edge_total / edge_n) if edge_n else float("nan")
    sc.segments = {
        "|".join(k): {"pnl_cents": v, "contracts": seg_n[k], "per_contract": v / seg_n[k]}
        for k, v in seg_pnl.items()
        if seg_n.get(k)
    }

    # -- forecasting -------------------------------------------------------
    pairs = []
    triples = []
    for ticker, p_final, p_market, _p_signal, outcome in result.forecasts:
        pairs.append((p_final, outcome))
        triples.append((p_final, p_market, outcome))
    sc.n_forecasts = len(pairs)
    if pairs:
        sc.brier = brier(pairs)
        sc.log_loss = log_loss(pairs)
        sc.ece = expected_calibration_error(pairs)
        sc.brier_skill = brier_skill_vs_market(triples)
    return sc


def head_to_head(a: Scorecard, b: Scorecard) -> dict:
    """Direct comparison, oriented so positive always favours `a`."""

    def d(x, y):
        if _nan(x) or _nan(y):
            return None
        return round(x - y, 4)

    return {
        "pnl_delta_$": round((a.pnl_cents - b.pnl_cents) / 100.0, 2),
        "return_delta_%": round(a.return_pct - b.return_pct, 2),
        "brier_delta": d(b.brier, a.brier),  # lower is better -> flip
        "brier_skill_delta": d(a.brier_skill, b.brier_skill),
        "maxDD_delta_%": d(b.max_drawdown_pct, a.max_drawdown_pct),
        "fee_per_ct_delta_c": d(b.fees_per_contract, a.fees_per_contract),
        "trades_delta": a.n_fills - b.n_fills,
    }
