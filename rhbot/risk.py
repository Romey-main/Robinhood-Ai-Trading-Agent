"""Position sizing, stop/target placement, and pre-trade guardrails.

This module turns a candidate into a concrete, *risk-bounded* plan. It never
sizes a trade you can't afford, always attaches a stop, and refuses setups
whose reward-to-risk doesn't clear the configured minimum.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import Config
from .models import Snapshot


def _floor_to(x: float, dp: int) -> float:
    f = 10 ** dp
    return math.floor(x * f) / f


def price_dp(price: float) -> int:
    """Robinhood quotes sub-$1 names to 4dp, the rest to 2dp."""
    return 4 if price < 1.0 else 2


def position_size(buying_power: float, price: float, cfg: Config) -> float:
    """Fractional shares we can afford for one position (6dp, like RH)."""
    if price <= 0:
        return 0.0
    budget = buying_power * cfg.max_position_pct
    return _floor_to(budget / price, 6)


def stop_and_target(entry: float, cfg: Config) -> tuple:
    dp = price_dp(entry)
    stop = round(entry * (1.0 - cfg.stop_loss_pct), dp)
    target = round(entry * (1.0 + cfg.take_profit_pct), dp)
    return stop, target


@dataclass
class RiskDecision:
    symbol: str
    approved: bool
    qty: float = 0.0
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    notional: float = 0.0
    risk_amount: float = 0.0       # $ lost if stop hits
    reward_amount: float = 0.0     # $ gained if target hits
    reward_risk: float = 0.0
    reasons: list = field(default_factory=list)


def evaluate_trade(
    snap: Snapshot,
    cfg: Config,
    open_positions: int = 0,
    buying_power: float | None = None,
) -> RiskDecision:
    bp = cfg.account_buying_power if buying_power is None else buying_power
    reasons: list = []

    if open_positions >= cfg.max_open_positions:
        reasons.append(
            f"already at max_open_positions={cfg.max_open_positions}"
        )
    if cfg.cash_account and open_positions > 0:
        reasons.append(
            "cash account: prior buy's proceeds unsettled "
            f"(T+{cfg.settlement_days}); avoid good-faith violation"
        )
    if bp < cfg.min_price:
        reasons.append(f"buying power ${bp:.2f} below min tradable price")
    if snap.price < cfg.min_price or snap.price > cfg.max_price:
        reasons.append(
            f"price ${snap.price:.2f} outside "
            f"[{cfg.min_price:.0f}, {cfg.max_price:.0f}]"
        )

    entry = snap.price
    qty = position_size(bp, entry, cfg)
    stop, target = stop_and_target(entry, cfg)
    notional = round(qty * entry, 2)
    risk_amt = round((entry - stop) * qty, 2)
    reward_amt = round((target - entry) * qty, 2)
    rr = round(reward_amt / risk_amt, 2) if risk_amt > 0 else 0.0

    if qty <= 0:
        reasons.append("position size rounds to zero shares")
    if rr < cfg.min_reward_risk:
        reasons.append(
            f"reward:risk {rr:.2f} < min {cfg.min_reward_risk:.2f}"
        )

    approved = len(reasons) == 0
    if approved:
        reasons.append(
            f"OK: {qty:g} sh @ ${entry:.2f} (${notional:.2f}), "
            f"stop ${stop:.2f} / target ${target:.2f}, R:R {rr:.2f}"
        )
    return RiskDecision(
        symbol=snap.symbol,
        approved=approved,
        qty=qty,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        notional=notional,
        risk_amount=risk_amt,
        reward_amount=reward_amt,
        reward_risk=rr,
        reasons=reasons,
    )
