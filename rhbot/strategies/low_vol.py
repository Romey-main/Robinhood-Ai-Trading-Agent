"""Long-only low-volatility factor.

Buy the names with the lowest trailing realized volatility. The low-vol anomaly:
historically, low-risk stocks have delivered *better* risk-adjusted returns than
high-risk ones — the opposite of what CAPM predicts. This is the defensive,
lower-beta sleeve, and it diversifies a momentum book (which tends to load on
high-vol names — see the semis-heavy momentum basket).
"""

from __future__ import annotations

from .base import Strategy, TargetBasket
from ..data_quality import validate_series
from ..portfolio_construction import construct


class LowVolatility(Strategy):
    name = "low_vol"

    def __init__(self, vol_days: int = 120, top_n: int = 30):
        self.vol_days = vol_days
        self.top_n = top_n

    def required_history(self) -> int:
        return self.vol_days + 5

    def generate(self, panel, members, asof, cfg, denylist=None, sectors=None) -> TargetBasket:
        candidates = sorted(set(members) & set(panel.symbols()))
        b = TargetBasket(asof=asof, strategy=self.name, n_considered=len(candidates))

        ranked = []
        for sym in candidates:
            chk = validate_series(panel, sym, asof, cfg,
                                  required_days=self.required_history(),
                                  scan_days=25, denylist=denylist)
            if not chk.ok:
                b.rejects[sym] = "; ".join(chk.reject_reasons)
                continue
            b.n_dq_pass += 1
            v = panel.trailing_vol(sym, asof, self.vol_days)
            if v is None or v <= 0:
                b.rejects[sym] = "no_vol"
                continue
            ranked.append((sym, v))

        b.n_valid = len(ranked)
        ranked.sort(key=lambda x: (x[1], x[0]))      # ascending vol — lowest first
        b.selected = [s for s, _ in ranked[:self.top_n]]
        b.notes.append(f"low-vol sleeve: {self.vol_days}d realized vol")
        b.weights, cnotes = construct(b.selected, self.top_n, panel, asof, cfg, sectors)
        b.notes.extend(cnotes)
        return b
