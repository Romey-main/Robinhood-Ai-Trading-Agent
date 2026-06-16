"""Long-only short-term cross-sectional mean reversion (5-day reversal).

Spec: rank Russell 1000 by trailing 5-day total return ascending, buy the 10
worst, equal-weight, hold ~1 week, re-rank.

Risk note: buying the worst recent performers is, mechanically, catching
falling knives. The ``event_move_cap`` is the guard that separates an ordinary
pullback (mean-reverts) from a structural break — M&A, an earnings shock, a
halt, a fraud blow-up (does *not* revert; it keeps falling). Anything that
moved more than the cap is excluded, not bought.
"""

from __future__ import annotations

from .base import Strategy, TargetBasket
from ..data_quality import validate_series


class ShortTermReversal(Strategy):
    name = "mean_reversion"

    def __init__(self, lookback_days: int = 5, top_n: int = 10):
        self.lookback_days = lookback_days
        self.top_n = top_n

    def required_history(self) -> int:
        return self.lookback_days + 2

    def generate(self, panel, members, asof, cfg, denylist=None) -> TargetBasket:
        candidates = sorted(set(members) & set(panel.symbols()))
        b = TargetBasket(asof=asof, strategy=self.name, n_considered=len(candidates))

        ranked = []
        for sym in candidates:
            chk = validate_series(panel, sym, asof, cfg,
                                  required_days=self.required_history(),
                                  scan_days=max(25, self.required_history()),
                                  denylist=denylist)
            if not chk.ok:
                b.rejects[sym] = "; ".join(chk.reject_reasons)
                continue
            b.n_dq_pass += 1

            r = panel.trailing_return(sym, asof, self.lookback_days)
            if r is None:
                b.rejects[sym] = "no_return"
                continue
            if abs(r) > cfg.event_move_cap:
                b.rejects[sym] = (f"event_filter |{r*100:+.0f}%| > "
                                  f"{cfg.event_move_cap*100:.0f}% (likely event-driven)")
                continue
            ranked.append((sym, r))

        b.n_valid = len(ranked)
        # ascending by return (most negative first), alphabetical tie-break
        ranked.sort(key=lambda x: (x[1], x[0]))
        b.selected = [s for s, _ in ranked[:self.top_n]]

        # Risk-first weighting: nominal 1/top_n per name; if fewer than top_n
        # qualify, the remainder stays in CASH rather than concentrating.
        w = 1.0 / self.top_n
        b.weights = {s: round(w, 6) for s in b.selected}
        if len(b.selected) < self.top_n:
            b.notes.append(
                f"only {len(b.selected)}/{self.top_n} names qualified -> "
                f"{(1 - len(b.selected) * w) * 100:.0f}% held in cash")
        return b
