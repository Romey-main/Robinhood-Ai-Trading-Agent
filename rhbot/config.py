"""Tunable thresholds and risk parameters.

The defaults encode the real constraints of a tiny *cash* account:
  * one position at a time (you can't diversify meaningfully with $50),
  * a hard stop on every trade,
  * a liquidity floor so you can actually get filled near the quote,
  * a volatility *ceiling* so the screener avoids halt-prone blow-off pumps
    (the kind that trend on social media right before they round-trip).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict


@dataclass
class Config:
    # --- account / risk -------------------------------------------------
    account_buying_power: float = 50.0
    cash_account: bool = True          # cash account => T+1 settlement
    settlement_days: int = 1           # ~1 round-trip/day with full balance
    max_open_positions: int = 1
    max_position_pct: float = 0.95     # fraction of buying power per trade
    stop_loss_pct: float = 0.08        # exit if down 8%
    take_profit_pct: float = 0.12      # exit if up 12%
    min_reward_risk: float = 1.3       # require target/stop edge >= this

    # --- liquidity / price filters -------------------------------------
    min_price: float = 2.0             # avoid sub-$2 penny spreads/halts
    max_price: float = 1000.0
    min_avg_dollar_volume: float = 20_000_000.0   # $20M/day so fills are sane

    # --- volatility band ------------------------------------------------
    # We *want* movement, but >~25% daily realized vol is usually a pump that
    # gaps and halts; the screener treats that as a red flag, not a feature.
    min_volatility: float = 0.03
    max_volatility: float = 0.25
    ideal_momentum: float = 0.03       # reward strength up to here...
    overextended_momentum: float = 0.15  # ...then penalize chasing past here

    # --- scoring weights (auto-normalized) -----------------------------
    w_volatility: float = 0.30
    w_liquidity: float = 0.20
    w_momentum: float = 0.25
    w_sentiment: float = 0.25

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "Config":
        try:
            with open(path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return cls()
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


DEFAULT = Config()
