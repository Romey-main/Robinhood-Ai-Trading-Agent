from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TargetBasket:
    """Raw target weights a strategy wants, before portfolio-level risk vetting.

    ``weights`` are nominal sleeve weights (e.g. 0.10 per name). Counts let the
    risk layer distinguish a *broken data feed* (low data-quality pass rate)
    from *legitimate* filter exclusions (e.g. event-move filter).
    """

    asof: str
    strategy: str
    weights: dict = field(default_factory=dict)     # symbol -> nominal weight
    selected: list = field(default_factory=list)
    n_considered: int = 0
    n_dq_pass: int = 0          # passed data-quality validation
    n_valid: int = 0            # passed data-quality AND strategy filters
    rejects: dict = field(default_factory=dict)     # symbol -> reason
    notes: list = field(default_factory=list)

    @property
    def gross(self) -> float:
        return round(sum(self.weights.values()), 6)

    @property
    def cash_weight(self) -> float:
        return round(max(0.0, 1.0 - self.gross), 6)


class Strategy:
    name = "base"

    def required_history(self) -> int:
        raise NotImplementedError

    def generate(self, panel, members, asof, cfg, denylist=None) -> TargetBasket:
        raise NotImplementedError
