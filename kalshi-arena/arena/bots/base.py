"""Bot interface, shared context, and the parameter vector used for transfer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..types import MarketSnapshot, Order, Resolution, Signal


@dataclass
class BotContext:
    """What a bot is allowed to know about its own state at decision time."""

    cash_cents: int
    equity_cents: int
    high_water_cents: int
    start_cents: int
    positions: dict  # (ticker, Side) -> Position
    cluster_exposure: dict  # cluster -> cents
    tick: int
    killed: bool = False


@dataclass
class BotAction:
    """A bot's output for one tick.

    Orders and forecasts are returned separately on purpose. Scoring a bot on
    P&L alone cannot tell a good forecaster with bad execution from a bad
    forecaster who got lucky, and those two failures need opposite fixes.
    """

    orders: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)  # ticker -> Signal


class Params:
    """A flat, typed parameter vector -- the unit of cross-bot transfer.

    Parameters are kept in a plain dict rather than as attributes so that
    transfer, diffing and persistence are mechanical. `locked` names the
    parameters that define a bot's identity and may never be imported from a
    peer; without it, two bots that learn from each other converge to the
    same bot and the competition stops producing information.
    """

    def __init__(self, values: dict, locked: set | None = None, bounds: dict | None = None):
        self.values = dict(values)
        self.locked = set(locked or ())
        self.bounds = dict(bounds or {})

    def __getitem__(self, k):
        return self.values[k]

    def __setitem__(self, k, v):
        self.values[k] = self._clip(k, v)

    def get(self, k, default=None):
        return self.values.get(k, default)

    def _clip(self, k, v):
        if k in self.bounds and isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = self.bounds[k]
            return min(hi, max(lo, v))
        return v

    def transferable(self) -> dict:
        return {k: v for k, v in self.values.items() if k not in self.locked}

    def copy(self) -> "Params":
        return Params(self.values, self.locked, self.bounds)

    def to_dict(self) -> dict:
        return dict(self.values)

    def distance(self, other: "Params") -> float:
        """Normalised L1 distance over shared numeric params, for the
        diversity floor. Booleans count as a full unit of difference."""
        keys = set(self.values) & set(other.values)
        if not keys:
            return 0.0
        total = 0.0
        n = 0
        for k in keys:
            a, b = self.values[k], other.values[k]
            if isinstance(a, bool) or isinstance(b, bool):
                total += 0.0 if bool(a) == bool(b) else 1.0
                n += 1
            elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
                lo, hi = self.bounds.get(k, (min(a, b), max(a, b)))
                rng = (hi - lo) or 1.0
                total += abs(a - b) / rng
                n += 1
        return total / n if n else 0.0


class Bot:
    """Base class. Subclasses implement `decide` and optionally `learn`."""

    name = "bot"

    def __init__(self, params: Params, seed: int = 0):
        self.params = params
        self.seed = seed
        self.traded_tickers: set = set()
        self.notes: dict = {}

    # -- required ---------------------------------------------------------

    def decide(self, snapshots: dict, signals: dict, ctx: BotContext) -> BotAction:
        raise NotImplementedError

    # -- optional ---------------------------------------------------------

    def learn(self, res: Resolution, p_market: Optional[float], p_signal: Optional[float],
              p_final: Optional[float]) -> None:
        """Called once per resolved market the bot observed, traded or not."""

    def on_fill(self, fill) -> None:
        """Called after every fill, so a bot can track its own inventory."""

    def start_round(self, round_index: int) -> None:
        self.traded_tickers.clear()

    def end_round(self, round_index: int) -> None:
        pass

    # -- knowledge exchange ----------------------------------------------

    def export_knowledge(self) -> dict:
        """Learned state that can be pooled with a peer (calibration, weights)."""
        return {}

    def import_knowledge(self, blob: dict, weight: float = 1.0) -> None:
        """Merge a peer's learned state. Default: ignore."""

    def describe(self) -> str:
        return self.name
