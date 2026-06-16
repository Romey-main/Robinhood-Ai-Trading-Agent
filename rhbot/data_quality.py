"""Per-symbol data validation — the "do not fall for invalid information" layer.

A symbol must pass *every* hard check before it is eligible to trade. The
philosophy is fail-closed: if we can't prove the data is clean, we drop the
name. Reject reasons are explicit so nothing is silently trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .panel import Panel
from .strategy_config import StrategyConfig
from .universe import Denylist


@dataclass
class Flag:
    code: str
    severity: str        # "reject" | "warn"
    detail: str = ""


@dataclass
class SeriesCheck:
    symbol: str
    flags: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "reject" for f in self.flags)

    @property
    def reject_reasons(self) -> list:
        return [f"{f.code}: {f.detail}" for f in self.flags if f.severity == "reject"]

    @property
    def warnings(self) -> list:
        return [f"{f.code}: {f.detail}" for f in self.flags if f.severity == "warn"]


def _finite_pos(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def _days_between(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def validate_series(
    panel: Panel, symbol: str, asof: str, cfg: StrategyConfig,
    required_days: int, scan_days: int = 25, denylist: Denylist | None = None,
) -> SeriesCheck:
    chk = SeriesCheck(symbol=symbol)

    if denylist is not None and symbol in denylist:
        chk.flags.append(Flag("denylisted", "reject", "on corruption denylist"))
        return chk

    series = panel.prices.get(symbol)
    if not series:
        chk.flags.append(Flag("no_data", "reject", "symbol absent from panel"))
        return chk

    # trading dates available on or before asof, in order
    avail = [d for d in panel.dates if d <= asof and d in series]
    if len(avail) < required_days:
        chk.flags.append(Flag(
            "insufficient_history", "reject",
            f"{len(avail)} sessions < required {required_days}"))
        return chk

    # Deep history is *required*, but anomaly checks only scan the recent
    # window — otherwise a legitimately volatile momentum name with one big
    # earnings day a year ago would be wrongly rejected.
    window = avail[-min(scan_days, len(avail)):]
    prices = [series[d] for d in window]

    # non-finite / non-positive anywhere in the window == invalid
    bad = [d for d, p in zip(window, prices) if not _finite_pos(p)]
    if bad:
        chk.flags.append(Flag(
            "nonfinite_price", "reject",
            f"{len(bad)} bad price(s), e.g. {bad[0]}"))
        return chk

    # price floor at the most recent point
    last_px = prices[-1]
    if last_px < cfg.min_price:
        chk.flags.append(Flag(
            "below_price_floor", "reject",
            f"${last_px:.2f} < ${cfg.min_price:.2f}"))

    # staleness: last observation must be near asof
    stale = _days_between(window[-1], asof)
    if stale > cfg.max_staleness_days:
        chk.flags.append(Flag(
            "stale", "reject",
            f"last price {window[-1]} is {stale}d before {asof}"))

    # suspicious single-day moves (possible unadjusted split / bad tick / halt)
    for i in range(1, len(prices)):
        r = prices[i] / prices[i - 1] - 1.0
        if abs(r) > cfg.max_daily_move:
            chk.flags.append(Flag(
                "suspicious_jump", "reject",
                f"{r*100:+.0f}% on {window[i]} (>{cfg.max_daily_move*100:.0f}%)"))
            break

    # calendar gaps between consecutive sessions
    for i in range(1, len(window)):
        gap = _days_between(window[i], window[i - 1])
        if gap > cfg.max_gap_days + 4:   # +4 absorbs weekends/holidays
            chk.flags.append(Flag(
                "data_gap", "warn",
                f"{gap}d gap before {window[i]}"))
            break

    return chk
