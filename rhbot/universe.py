"""Point-in-time universe membership and the corruption denylist.

Two anti-"invalid information" guards live here:

* **Point-in-time membership** — you may only hold a name that was actually in
  the index *as of the evaluation date*. Using today's index membership to pick
  trades in the past is survivorship/look-ahead bias: it silently deletes the
  companies that blew up, which is precisely the risk you care about.
* **Denylist** — tickers with known-bad data or corporate-action corruption are
  excluded outright, no exceptions.
"""

from __future__ import annotations

import json
import os


class Membership:
    """Snapshots of index constituents keyed by ISO date."""

    def __init__(self, snapshots: dict[str, set[str]] | None = None,
                 fallback_symbols: set[str] | None = None):
        self.snapshots = {d: {s.upper() for s in syms}
                          for d, syms in (snapshots or {}).items()}
        self._dates = sorted(self.snapshots)
        self.fallback = {s.upper() for s in (fallback_symbols or set())}

    @property
    def has_snapshots(self) -> bool:
        return bool(self._dates)

    def members_asof(self, asof: str) -> set[str]:
        """Most recent constituent snapshot at or before ``asof``.

        Falls back to ``fallback_symbols`` only when no snapshots exist — and
        callers are expected to surface that as a survivorship-bias warning.
        """
        chosen = None
        for d in self._dates:
            if d <= asof:
                chosen = d
            else:
                break
        if chosen is None:
            return set(self.fallback)
        return set(self.snapshots[chosen])

    @classmethod
    def from_json(cls, path: str, fallback_symbols=None) -> "Membership":
        """JSON: {"2026-01-31": ["AAPL", ...], "2026-02-28": [...]}"""
        if not os.path.exists(path):
            return cls(None, fallback_symbols)
        with open(path) as fh:
            data = json.load(fh)
        return cls({d: set(v) for d, v in data.items()}, fallback_symbols)


class Denylist:
    def __init__(self, tickers: set[str] | None = None):
        self.tickers = {t.upper() for t in (tickers or set())}

    def __contains__(self, symbol: str) -> bool:
        return symbol.upper() in self.tickers

    def __len__(self) -> int:
        return len(self.tickers)

    @classmethod
    def from_file(cls, path: str) -> "Denylist":
        """One ticker per line; blank lines and #comments ignored."""
        if not os.path.exists(path):
            return cls(set())
        out = set()
        with open(path) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    out.add(line.upper())
        return cls(out)
