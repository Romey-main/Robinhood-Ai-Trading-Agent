from .base import Strategy, TargetBasket
from .mean_reversion import ShortTermReversal
from .momentum import Momentum12_1

REGISTRY = {
    "mean_reversion": ShortTermReversal,
    "momentum": Momentum12_1,
}

__all__ = ["Strategy", "TargetBasket", "ShortTermReversal", "Momentum12_1", "REGISTRY"]
