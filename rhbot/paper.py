"""Walk-forward paper ledger — a real, point-in-time track record.

Unlike the backtest (which replays *today's* index and is therefore
survivorship-biased), the ledger records each basket *as you actually run it*,
against the real index membership at that moment. Marking those recorded
baskets forward with realized prices yields an honest, bias-free performance
record — the only free path to knowing whether a sleeve actually works. It just
takes calendar time to accumulate.
"""

from __future__ import annotations

import json
import os
from datetime import date

from .backtest import _stats


class PaperLedger:
    def __init__(self, path: str):
        self.path = path
        self.entries: list = []
        if os.path.exists(path):
            with open(path) as fh:
                self.entries = json.load(fh)

    def record(self, asof: str, strategy: str, weights: dict) -> None:
        self.entries.append({
            "date": asof,
            "strategy": strategy,
            "weights": {s: round(w, 6) for s, w in weights.items()},
        })
        self.save()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(self.entries, fh, indent=2)


def _avg_days(dates: list) -> float:
    ds = [date.fromisoformat(d) for d in dates]
    gaps = [(ds[i + 1] - ds[i]).days for i in range(len(ds) - 1)]
    gaps = [g for g in gaps if g > 0]
    return sum(gaps) / len(gaps) if gaps else 30.0


def mark(entries: list, panel, asof: str | None = None):
    """Mark recorded baskets forward; each is held until the next entry (or asof).

    Returns (per_entry_rows, equity_curve, stats). The last entry is marked to
    ``asof`` (unrealized mark-to-market of the still-open book).
    """
    asof = asof or panel.latest_date()
    es = sorted((e for e in entries if e["date"] <= asof), key=lambda e: e["date"])
    rows: list = []
    equity = 1.0
    eq_curve = [(es[0]["date"], 1.0)] if es else []
    rets: list = []
    missing = 0

    for i, e in enumerate(es):
        start = e["date"]
        end = es[i + 1]["date"] if i + 1 < len(es) else asof
        is_open = (i + 1 == len(es))
        if end <= start:                       # just recorded; nothing to mark yet
            rows.append({"date": start, "strategy": e["strategy"], "end": end,
                         "ret": None, "equity": round(equity, 6), "open": True})
            continue
        pr = 0.0
        for s, w in e["weights"].items():
            fr = panel.forward_return(s, start, end)
            if fr is None:
                missing += 1
                continue
            pr += w * fr
        equity *= (1.0 + pr)
        rets.append(pr)
        eq_curve.append((end, round(equity, 6)))
        rows.append({"date": start, "strategy": e["strategy"], "end": end,
                     "ret": pr, "equity": round(equity, 6), "open": is_open})

    ppy = 365.25 / _avg_days([e["date"] for e in es]) if len(es) > 1 else 12.0
    stats = _stats(rets, eq_curve, ppy) if rets else {"note": "no marked periods yet"}
    stats["missing_forward_prices"] = missing
    stats["ppy"] = round(ppy, 1)
    return rows, eq_curve, stats
