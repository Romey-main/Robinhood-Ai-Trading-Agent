"""Price panel: a date x symbol grid of split/dividend-adjusted closes.

Std-lib only (no pandas) so the core stays dependency-free and testable. All
returns are computed from ``adj_close`` so corporate actions (splits,
dividends) never masquerade as price moves — reading an unadjusted 2:1 split as
a "-50% crash" is a classic way to fall for invalid information.
"""

from __future__ import annotations

import csv
import math
from datetime import date


def add_months(d: date, n: int) -> date:
    """Shift a date by n calendar months, clamping day-of-month."""
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    last_day = [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last_day))


def _finite_pos(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x) and x > 0


class Panel:
    def __init__(self, prices: dict[str, dict[str, float]], dates: list[str]):
        # prices[symbol][iso_date] = adj_close
        self.prices = prices
        self.dates = sorted(dates)
        self._idx = {d: i for i, d in enumerate(self.dates)}

    def symbols(self) -> list[str]:
        return sorted(self.prices)

    def latest_date(self) -> str:
        return self.dates[-1] if self.dates else ""

    def _date_on_or_before(self, asof: str) -> str | None:
        # global trading date <= asof
        lo, hi, ans = 0, len(self.dates) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.dates[mid] <= asof:
                ans = self.dates[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return ans

    def price_asof(self, symbol: str, asof: str) -> float | None:
        """Most recent adjusted close for ``symbol`` on or before ``asof``."""
        series = self.prices.get(symbol)
        if not series:
            return None
        d = self._date_on_or_before(asof)
        while d is not None:
            p = series.get(d)
            if p is not None:
                return p
            i = self._idx[d]
            d = self.dates[i - 1] if i > 0 else None
        return None

    def trailing_return(self, symbol: str, asof: str, lookback_days: int):
        """Total return over the trailing ``lookback_days`` trading days."""
        end = self._date_on_or_before(asof)
        if end is None:
            return None
        i = self._idx[end]
        if i - lookback_days < 0:
            return None
        start = self.dates[i - lookback_days]
        p0 = self.price_asof(symbol, start)
        p1 = self.price_asof(symbol, end)
        if not (_finite_pos(p0) and _finite_pos(p1)):
            return None
        return p1 / p0 - 1.0

    def trailing_vol(self, symbol: str, asof: str, lookback_days: int):
        """Std-dev of daily returns over the trailing window (per-day, not annualized).

        Used for inverse-volatility weighting and vol targeting. Returns None if
        the window has a gap or non-positive price (fail-closed, like the rest).
        """
        end = self._date_on_or_before(asof)
        if end is None:
            return None
        i = self._idx[end]
        if i - lookback_days < 0:
            return None
        px = []
        for j in range(i - lookback_days, i + 1):
            p = self.price_asof(symbol, self.dates[j])
            if not _finite_pos(p):
                return None
            px.append(p)
        rets = [px[k] / px[k - 1] - 1.0 for k in range(1, len(px))]
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var)

    def window_return(self, symbol: str, start_iso: str, end_iso: str):
        """Total return between two calendar dates (uses on-or-before prices)."""
        p0 = self.price_asof(symbol, start_iso)
        p1 = self.price_asof(symbol, end_iso)
        if not (_finite_pos(p0) and _finite_pos(p1)):
            return None
        return p1 / p0 - 1.0

    def forward_return(self, symbol: str, start_iso: str, end_iso: str):
        """Realized return from start->end (for backtest holding periods)."""
        return self.window_return(symbol, start_iso, end_iso)

    def dates_between(self, start_iso: str, end_iso: str) -> list[str]:
        return [d for d in self.dates if start_iso <= d <= end_iso]

    # --- io -------------------------------------------------------------
    @classmethod
    def from_csv(cls, path: str) -> "Panel":
        """CSV columns: date,symbol,adj_close (header required)."""
        prices: dict[str, dict[str, float]] = {}
        dates: set[str] = set()
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                sym = row["symbol"].strip().upper()
                d = row["date"].strip()
                try:
                    px = float(row["adj_close"])
                except (TypeError, ValueError):
                    px = float("nan")          # keep it; validation will flag
                prices.setdefault(sym, {})[d] = px
                dates.add(d)
        return cls(prices, sorted(dates))

    def to_csv(self, path: str) -> None:
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "symbol", "adj_close"])
            for sym in self.symbols():
                for d in sorted(self.prices[sym]):
                    w.writerow([d, sym, self.prices[sym][d]])
