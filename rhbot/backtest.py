"""Walk-forward backtester for the systematic sleeves.

Each rebalance runs the *full* pipeline — strategy -> data-quality -> portfolio
risk vetting — so the backtest measures the strategy you'd actually trade, not
an idealized one. Transaction costs are charged on turnover because at small
size, costs are often the whole story. Periods where the risk layer says
DO_NOT_TRADE are held in cash (zero return), exactly as live would behave.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from .panel import Panel
from .portfolio_risk import vet
from .strategy_config import StrategyConfig
from .universe import Membership, Denylist


def weekly_rebalance_dates(panel: Panel) -> list[str]:
    """Last trading day of each ISO week."""
    from datetime import date
    by_week: dict = {}
    for d in panel.dates:
        key = date.fromisoformat(d).isocalendar()[:2]
        by_week[key] = d
    return sorted(by_week.values())


def monthly_rebalance_dates(panel: Panel) -> list[str]:
    """Last trading day of each calendar month."""
    by_month: dict = {}
    for d in panel.dates:
        by_month[d[:7]] = d          # 'YYYY-MM'
    return sorted(by_month.values())


@dataclass
class BacktestResult:
    strategy: str
    equity: list = field(default_factory=list)       # [(date, equity)]
    period_returns: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    n_periods: int = 0
    n_traded: int = 0
    n_cash: int = 0


def _l1(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def run_backtest(
    strategy, panel: Panel, membership: Membership, cfg: StrategyConfig,
    rebalance_dates: list[str], periods_per_year: float,
    denylist: Denylist | None = None, drift_skip: bool = False,
    sectors: dict | None = None,
) -> BacktestResult:
    res = BacktestResult(strategy=strategy.name)
    equity = 1.0
    res.equity.append((rebalance_dates[0], equity))
    prev_weights: dict = {}
    cost = cfg.cost_bps / 1e4
    missing_fwd = 0

    for i in range(len(rebalance_dates) - 1):
        d, nxt = rebalance_dates[i], rebalance_dates[i + 1]
        members = (membership.members_asof(d)
                   if membership.has_snapshots else set(panel.symbols()))

        basket = strategy.generate(panel, members, d, cfg, denylist=denylist, sectors=sectors)
        decision = vet(basket, cfg)

        if not decision.will_trade:
            weights = {}
        else:
            weights = decision.final_weights

        # optional turnover suppression (momentum spec: skip if drift small)
        if drift_skip and prev_weights and weights and \
                _l1(weights, prev_weights) < cfg.l1_drift_skip:
            weights = prev_weights

        # realized return over the holding period
        port_ret = 0.0
        for sym, w in weights.items():
            fr = panel.forward_return(sym, d, nxt)
            if fr is None:
                missing_fwd += 1
                continue            # treat as exited at entry (0 contribution)
            port_ret += w * fr

        turnover = 0.5 * _l1(weights, prev_weights)
        net_ret = port_ret - turnover * cost

        equity *= (1.0 + net_ret)
        res.equity.append((nxt, round(equity, 6)))
        res.period_returns.append(net_ret)
        res.n_periods += 1
        if weights:
            res.n_traded += 1
        else:
            res.n_cash += 1
        prev_weights = weights

    res.stats = _stats(res.period_returns, res.equity, periods_per_year)
    res.stats["missing_forward_prices"] = missing_fwd
    res.stats["pct_periods_traded"] = (
        round(res.n_traded / res.n_periods, 3) if res.n_periods else 0.0)
    return res


def _stats(rets: list, equity: list, ppy: float) -> dict:
    if not rets:
        return {"note": "no periods"}
    n = len(rets)
    total = equity[-1][1] / equity[0][1] - 1.0
    mean = statistics.mean(rets)
    sd = statistics.pstdev(rets) if n > 1 else 0.0
    sharpe = (mean / sd * math.sqrt(ppy)) if sd > 0 else 0.0
    ann_ret = (equity[-1][1] / equity[0][1]) ** (ppy / n) - 1.0 if n else 0.0
    ann_vol = sd * math.sqrt(ppy)

    peak, mdd = equity[0][1], 0.0
    for _, e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1.0)

    return {
        "n_periods": n,
        "total_return": round(total, 4),
        "ann_return": round(ann_ret, 4),
        "ann_vol": round(ann_vol, 4),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(mdd, 4),
        "hit_rate": round(sum(1 for r in rets if r > 0) / n, 3),
        "avg_period_return": round(mean, 5),
    }
