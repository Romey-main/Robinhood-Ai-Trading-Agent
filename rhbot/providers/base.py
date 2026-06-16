from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..models import Snapshot


class Provider(ABC):
    @abstractmethod
    def get_snapshot(self, symbol: str) -> Snapshot:
        ...

    def get_snapshots(self, symbols: Iterable[str]) -> list[Snapshot]:
        out = []
        for s in symbols:
            try:
                out.append(self.get_snapshot(s))
            except Exception as exc:  # noqa: BLE001 - keep going on bad symbols
                print(f"[warn] {s}: {exc}")
        return out
