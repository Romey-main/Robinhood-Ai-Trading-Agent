"""Core data structures, all JSON-serializable with std-lib only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


def _round(x: Optional[float], dp: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), dp)


@dataclass
class Snapshot:
    """A point-in-time market picture for one symbol, used by the screener.

    All ratios are decimals (0.05 == 5%). ``sentiment`` is a news/social score
    in [-1, 1] (negative == bearish coverage, positive == bullish), filled in
    by the research step; default 0.0 means "no signal / neutral".
    """

    symbol: str
    price: float
    avg_dollar_volume: float        # 20d average of close * volume, in USD
    volatility: float               # stdev of daily returns (realized vol)
    momentum: float                 # recent return, e.g. trailing 5 sessions
    sentiment: float = 0.0          # [-1, 1], from news/social research
    as_of: str = ""                 # ISO8601
    news_count: int = 0             # how many headlines backed the sentiment
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class ScoredCandidate:
    symbol: str
    score: float                    # 0..100 composite
    components: dict = field(default_factory=dict)   # per-factor 0..1 scores
    reasons: list = field(default_factory=list)      # human-readable rationale
    snapshot: Optional[Snapshot] = None
    passed_filters: bool = True
    reject_reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.snapshot is not None:
            d["snapshot"] = self.snapshot.to_dict()
        return d


@dataclass
class PaperTrade:
    """A simulated long position. No real money is involved."""

    symbol: str
    qty: float
    entry_price: float
    stop_price: float
    target_price: float
    entry_time: str
    side: str = "long"
    rationale: str = ""
    sentiment: float = 0.0
    score: float = 0.0
    status: str = "open"            # "open" | "closed"
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None     # "target" | "stop" | "manual" | "expiry"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # --- derived ---------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def notional(self) -> float:
        return _round(self.qty * self.entry_price, 2)

    def pnl(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        return _round((self.exit_price - self.entry_price) * self.qty, 2)

    def pnl_pct(self) -> Optional[float]:
        if self.exit_price is None or self.entry_price == 0:
            return None
        return _round((self.exit_price / self.entry_price) - 1.0, 6)

    def unrealized_pct(self, last_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        return _round((last_price / self.entry_price) - 1.0, 6)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pnl"] = self.pnl()
        d["pnl_pct"] = self.pnl_pct()
        d["notional"] = self.notional
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PaperTrade":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in allowed})
