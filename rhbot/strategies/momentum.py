"""Long-only 12-1 cross-sectional momentum (Jegadeesh-Titman).

Spec: rank Russell 1000 by total return over a 12-month formation window ending
*one month ago* (the 1-month skip avoids short-term-reversal contamination),
buy the top 50, equal-weight, hold ~1 month, re-rank.

Anti-"invalid information" guards: both formation endpoints must be positive
and finite (a missing/zero endpoint silently fabricates a return), the name
must be a point-in-time index member (no survivorship leak), and it must clear
the denylist + recent data-quality scan.
"""

from __future__ import annotations

from datetime import date

from .base import Strategy, TargetBasket
from ..data_quality import validate_series
from ..panel import add_months


class Momentum12_1(Strategy):
    name = "momentum"

    def __init__(self, formation_months: int = 13, skip_months: int = 1, top_n: int = 50):
        # formation window = [asof - formation_months, asof - skip_months]
        self.formation_months = formation_months
        self.skip_months = skip_months
        self.top_n = top_n

    def required_history(self) -> int:
        # ~21 trading days per month, plus a small buffer
        return self.formation_months * 21 + 5

    def generate(self, panel, members, asof, cfg, denylist=None) -> TargetBasket:
        asof_d = date.fromisoformat(asof)
        start_iso = add_months(asof_d, -self.formation_months).isoformat()
        end_iso = add_months(asof_d, -self.skip_months).isoformat()

        candidates = sorted(set(members) & set(panel.symbols()))
        b = TargetBasket(asof=asof, strategy=self.name, n_considered=len(candidates))
        b.notes.append(f"formation window {start_iso} -> {end_iso}")

        ranked = []
        for sym in candidates:
            chk = validate_series(panel, sym, asof, cfg,
                                  required_days=self.required_history(),
                                  scan_days=25, denylist=denylist)
            if not chk.ok:
                b.rejects[sym] = "; ".join(chk.reject_reasons)
                continue
            b.n_dq_pass += 1

            r = panel.window_return(sym, start_iso, end_iso)
            if r is None:
                b.rejects[sym] = "missing/invalid formation endpoint price"
                continue
            ranked.append((sym, r))

        b.n_valid = len(ranked)
        # descending by formation return, alphabetical tie-break
        ranked.sort(key=lambda x: (-x[1], x[0]))
        b.selected = [s for s, _ in ranked[:self.top_n]]

        w = 1.0 / self.top_n
        b.weights = {s: round(w, 6) for s in b.selected}
        if len(b.selected) < self.top_n:
            b.notes.append(
                f"only {len(b.selected)}/{self.top_n} names qualified -> "
                f"{(1 - len(b.selected) * w) * 100:.0f}% held in cash")
        return b
