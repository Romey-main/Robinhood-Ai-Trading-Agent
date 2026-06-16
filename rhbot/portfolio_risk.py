"""Portfolio-level risk vetting. Risk management is the #1 priority here.

This is the last gate before any basket becomes orders. It is deliberately
*fail-closed*: if the data feed looks broken, too few names survive, or the
account is too small to trade the basket sensibly, the decision is
DO_NOT_TRADE. A strategy can only ever *propose*; this module disposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .strategies.base import TargetBasket
from .strategy_config import StrategyConfig


@dataclass
class TradeDecision:
    action: str                      # "TRADE" | "DO_NOT_TRADE"
    asof: str
    strategy: str
    final_weights: dict = field(default_factory=dict)
    cash_weight: float = 1.0
    blockers: list = field(default_factory=list)     # why DO_NOT_TRADE
    warnings: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)

    @property
    def will_trade(self) -> bool:
        return self.action == "TRADE"


def vet(basket: TargetBasket, cfg: StrategyConfig,
        account_value: float | None = None) -> TradeDecision:
    blockers: list = []
    warnings: list = []

    considered = basket.n_considered
    dq_rate = (basket.n_dq_pass / considered) if considered else 0.0

    # --- circuit breaker 1: data-feed health -------------------------------
    # Low data-quality pass rate means the FEED is suspect (stale/corrupt),
    # which is different from legitimate filter exclusions. Fail closed.
    if considered > 0 and dq_rate < cfg.min_valid_fraction:
        blockers.append(
            f"data-feed health {dq_rate*100:.0f}% < {cfg.min_valid_fraction*100:.0f}% "
            f"({basket.n_dq_pass}/{considered} names passed validation) — feed suspect")

    # --- circuit breaker 2: too few names ----------------------------------
    if len(basket.selected) < cfg.min_names_to_trade:
        blockers.append(
            f"only {len(basket.selected)} vetted name(s) < "
            f"min {cfg.min_names_to_trade} — too concentrated to trade")

    # --- per-name caps + cash routing --------------------------------------
    weights = dict(basket.weights)
    for s, w in list(weights.items()):
        if w > cfg.max_name_weight:
            warnings.append(f"{s} weight {w:.3f} capped to {cfg.max_name_weight:.3f}")
            weights[s] = cfg.max_name_weight

    if cfg.on_reject == "renormalize" and weights:
        tot = sum(weights.values())
        if tot > 0:
            weights = {s: round(w / tot, 6) for s, w in weights.items()}
            warnings.append("weights renormalized to 100% (on_reject=renormalize)")
    # default on_reject="cash": leave gross < 1, remainder is cash

    gross = round(sum(weights.values()), 6)
    cash = round(max(0.0, 1.0 - gross), 6)

    # --- scale sanity (the $50 reality) ------------------------------------
    if account_value is not None and weights:
        smallest = min(weights.values()) * account_value
        if smallest < cfg.per_name_notional_floor:
            warnings.append(
                f"~${smallest:.2f}/name at ${account_value:.0f} is below the "
                f"${cfg.per_name_notional_floor:.2f} floor — too small to trade "
                f"this basket cleanly; size up the sleeve or use fewer names")

    action = "DO_NOT_TRADE" if blockers else "TRADE"
    return TradeDecision(
        action=action, asof=basket.asof, strategy=basket.strategy,
        final_weights=weights if action == "TRADE" else {},
        cash_weight=cash if action == "TRADE" else 1.0,
        blockers=blockers, warnings=warnings,
        diagnostics={
            "considered": considered,
            "dq_pass": basket.n_dq_pass,
            "dq_rate": round(dq_rate, 3),
            "valid_after_filters": basket.n_valid,
            "selected": len(basket.selected),
            "gross_exposure": gross,
            "n_rejected": len(basket.rejects),
        },
    )
