"""Config for the systematic sleeves (separate from the $50 screener Config).

Defaults encode the spec's risk rules plus extra fail-closed guards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict


@dataclass
class StrategyConfig:
    # --- data-quality gates (the anti-rug layer) -----------------------
    min_price: float = 10.0            # spec: price >= $10 at lookback end
    max_staleness_days: int = 4        # refuse to trade on a stale feed
    max_daily_move: float = 0.40       # 1-day move bigger than this == suspect
    max_gap_days: int = 5              # consecutive missing sessions tolerated
    min_history_days: int = 30         # global floor; strategies may need more

    # --- event filter (mean-reversion) ---------------------------------
    event_move_cap: float = 0.20       # exclude |trailing return| > 20%

    # --- portfolio construction (weighting + concentration) ------------
    weight_scheme: str = "equal"       # "equal" or "inverse_vol" (risk-balanced)
    vol_lookback_days: int = 60        # window for vol weighting / targeting
    max_sector_weight: float = 0.30    # cap any one sector (needs a sector map)
    target_annual_vol: float = 0.0     # >0 scales gross exposure to this vol

    # --- portfolio risk / circuit breakers -----------------------------
    min_valid_fraction: float = 0.80   # <80% of basket valid => DO NOT TRADE
    min_names_to_trade: int = 5        # need at least this many vetted names
    max_name_weight: float = 0.12      # hard per-name cap
    on_reject: str = "cash"            # "cash" (safe) or "renormalize"
    per_name_notional_floor: float = 1.0   # warn if $/name below this
    cost_bps: float = 5.0              # round-trip cost assumption (backtests)

    # --- rebalance control ---------------------------------------------
    l1_drift_skip: float = 0.05        # momentum: skip rebalance if drift < 5%

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "StrategyConfig":
        try:
            with open(path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return cls()
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})
