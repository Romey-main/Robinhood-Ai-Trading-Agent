"""Load Snapshots straight from a JSON file.

This is the bridge for data sourced outside the standalone script — e.g. the
real Robinhood quotes/historicals an agent pulls and writes to disk. Format is
a JSON list of objects matching ``Snapshot`` fields::

    [{"symbol": "AMD", "price": 142.1, "avg_dollar_volume": 4.2e9,
      "volatility": 0.045, "momentum": 0.021, "sentiment": 0.3,
      "as_of": "2026-06-16", "news_count": 4, "notes": "..."}]
"""

from __future__ import annotations

import json

from .base import Provider
from ..models import Snapshot


class JsonProvider(Provider):
    def __init__(self, path: str):
        with open(path) as fh:
            self._rows = {r["symbol"].upper(): r for r in json.load(fh)}

    def get_snapshot(self, symbol: str) -> Snapshot:
        row = self._rows[symbol.upper()]
        return Snapshot.from_dict(row)

    def all(self) -> list[Snapshot]:
        return [Snapshot.from_dict(r) for r in self._rows.values()]
