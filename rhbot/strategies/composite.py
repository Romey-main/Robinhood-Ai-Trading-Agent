"""A composite of several sleeves blended into one book.

Runs each sub-sleeve, blends their target weights by sleeve weight, then
re-applies the portfolio caps (sector/name) to the blend as a *single*
portfolio. Because it implements the ``Strategy`` interface, it drops straight
into the same rebalance and walk-forward backtest pipeline as any single sleeve
— so a momentum+low-vol blend can be measured on Sharpe/drawdown/turnover the
same way, head to head.
"""

from __future__ import annotations

from .base import Strategy, TargetBasket
from ..portfolio_construction import _sector_totals, apply_caps, blend


class CompositeStrategy(Strategy):
    name = "combo"

    def __init__(self, sleeves):
        # sleeves: list[(Strategy instance, sleeve_weight)]
        self.sleeves = sleeves

    def required_history(self) -> int:
        return max((s.required_history() for s, _ in self.sleeves), default=30)

    def generate(self, panel, members, asof, cfg, denylist=None, sectors=None) -> TargetBasket:
        subs = [(s.generate(panel, members, asof, cfg, denylist=denylist, sectors=sectors), w)
                for s, w in self.sleeves]
        tot_w = sum(w for _, w in self.sleeves) or 1.0

        blended = blend([b.weights for b, _ in subs], [w for _, w in subs])
        capped = apply_caps(blended, min(1.0, sum(blended.values())), cfg, sectors)

        cb = TargetBasket(asof=asof, strategy=self.name)
        cb.weights = capped
        cb.selected = sorted(capped, key=lambda s: -capped[s])
        # feed-health is sleeve-independent (same panel); take the most lenient read
        cb.n_considered = max((b.n_considered for b, _ in subs), default=0)
        cb.n_dq_pass = max((b.n_dq_pass for b, _ in subs), default=0)
        cb.n_valid = len(capped)
        for (b, _), (s, w) in zip(subs, self.sleeves):
            cb.notes.append(f"sleeve {s.name}: {w/tot_w*100:.0f}% ({len(b.selected)} names)")
        if sectors and capped:
            sec = max(_sector_totals(capped, sectors).items(), key=lambda x: x[1])
            cb.notes.append(f"top sector: {sec[0]} {sec[1]*100:.0f}%")
        for b, _ in subs:
            cb.rejects.update(b.rejects)
        return cb
