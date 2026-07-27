"""
Cross-bot learning.

Two bots that watch each other and copy the winner do not learn -- they
collapse. After a few rounds they are the same bot, the comparison stops
producing information, and whatever made the loser interesting is gone. That
failure mode is the main thing this module is built to avoid.

Three channels, in increasing order of how much evidence they demand:

  **Pooled knowledge (cheap, always on).** Calibration evidence is merged
  across bots. A market that one bot watched resolve is a fact about the
  world, and facts are not competitive advantages -- both bots should have
  them. This makes both better without making either more similar.

  **Parameter transfer (needs a significant contrast).** Numeric thresholds
  move toward the peer's value, by an amount proportional to how strong the
  evidence is, capped per round. A parameter only moves when the *segment
  that parameter governs* shows a real difference.

  **Capability transfer (needs an attributed capability lesson).** Flipping
  a design switch is the largest possible change, so it needs the strongest
  evidence: a lesson that names the mechanism and measures it directly.

And two brakes:

  **Locked parameters.** Each bot keeps an identity it never trades away.
  The incumbent stays signal-primary; the challenger keeps its market prior.
  Without this the arena converges to one bot and stops being an experiment.

  **Diversity floor.** If a proposed set of changes would pull the two
  parameter vectors closer than `min_divergence`, the changes are scaled
  back until they would not. Learning is allowed to make the bots better;
  it is not allowed to make them the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from .knowledge import KnowledgeBase
from .stats import evidence_weight, welch_t

# Which segments' evidence justifies moving which parameter. A threshold
# should only move when the slice of the market it actually controls shows a
# difference -- otherwise "the challenger won" would drag every number it
# owns along with it, which is cargo-culting with extra steps.
PARAM_EVIDENCE: dict = {
    "min_net_edge_cents": {"kind": "all"},
    "min_net_edge_a": {"kind": "all"},
    "min_net_edge_b": {"kind": "all"},
    "kelly_fraction": {"kind": "all"},
    "max_pct_per_market": {"kind": "all"},
    "max_spread_cents": {"kind": "all"},
    "max_single_order": {"kind": "all"},
    "adverse_selection_cents": {"kind": "maker"},
    "maker_min_spread": {"kind": "maker"},
    "maker_ttl_ticks": {"kind": "maker"},
    "exit_profit_cents": {"kind": "all"},
    "exit_stop_cents": {"kind": "all"},
    "edge_sigma_multiple": {"kind": "all"},
    "kelly_uncertainty_z": {"kind": "all"},
    "max_pct_per_cluster": {"kind": "all"},
    "disagreement_cap_points": {"kind": "all"},
    "min_ticks_to_open": {"kind": "all"},
    "cash_reserve_pct": {"kind": "all"},
}

CAPABILITY_PARAMS = (
    "use_market_prior",
    "use_calibration",
    "use_exact_fees",
    "use_maker_first",
    "use_exits",
    "use_cluster_caps",
)


@dataclass
class TransferConfig:
    # Largest fraction of the gap to a peer's value that one round may close.
    max_step_fraction: float = 0.34
    # Parameter vectors are never allowed closer than this normalised L1
    # distance. This is what stops the two bots merging.
    min_divergence: float = 0.12
    # Minimum contracts on both sides before any contrast counts.
    min_support: int = 60
    # Confidence a capability lesson needs before a switch is flipped.
    capability_confidence: float = 0.5
    # Weight applied when pooling a peer's calibration evidence.
    knowledge_pool_weight: float = 1.0


def _segment_samples(kb: KnowledgeBase, bot: str, kind: str) -> list:
    if kind == "maker":
        return kb.segment_pnl(bot, is_maker=1)
    if kind == "taker":
        return kb.segment_pnl(bot, is_maker=0)
    return kb.segment_pnl(bot)


def propose_parameter_transfers(
    kb: KnowledgeBase, learner, teacher, cfg: TransferConfig
) -> list:
    """Return [(param, old, new, weight, evidence)] without applying them."""
    proposals = []
    for param, spec in PARAM_EVIDENCE.items():
        if param not in learner.params.values or param not in teacher.params.values:
            continue
        if param in learner.params.locked:
            continue
        old = learner.params[param]
        new_target = teacher.params[param]
        if not isinstance(old, (int, float)) or isinstance(old, bool):
            continue
        if abs(new_target - old) < 1e-9:
            continue

        xs_t = _segment_samples(kb, teacher.name, spec["kind"])
        xs_l = _segment_samples(kb, learner.name, spec["kind"])
        cmp = welch_t(xs_t, xs_l)
        w = evidence_weight(cmp, cfg.min_support)
        if w <= 0:
            continue

        step = cfg.max_step_fraction * w
        proposed = old + (new_target - old) * step
        proposals.append((param, old, proposed, w, cmp.to_dict()))
    return proposals


def propose_capability_transfers(
    kb: KnowledgeBase, learner, teacher, cfg: TransferConfig, lessons: list
) -> list:
    """Capabilities the learner may switch on, each justified by a lesson."""
    out = []
    for lesson in lessons:
        if lesson.kind != "capability" or lesson.source_bot != teacher.name:
            continue
        cap = lesson.scope
        if cap not in CAPABILITY_PARAMS:
            continue
        if cap in learner.params.locked:
            continue
        if learner.params.get(cap) is True:
            continue
        if teacher.params.get(cap) is not True:
            continue
        if lesson.confidence < cfg.capability_confidence:
            continue
        out.append((cap, lesson))
    return out


def _distance_after(learner, teacher, changes: dict) -> float:
    trial = learner.params.copy()
    for k, v in changes.items():
        trial[k] = v
    return trial.distance(teacher.params)


def apply_transfers(
    kb: KnowledgeBase,
    round_index: int,
    learner,
    teacher,
    lessons: list,
    cfg: TransferConfig | None = None,
) -> dict:
    """Move `learner` toward `teacher` where the evidence supports it.

    Returns a report of what changed and, just as importantly, what was
    proposed and then blocked -- a transfer that the diversity floor refuses
    is a fact about the run worth recording, not a silent no-op.
    """
    cfg = cfg or TransferConfig()
    report = {
        "learner": learner.name,
        "teacher": teacher.name,
        "params": [],
        "capabilities": [],
        "blocked": [],
        "pooled_knowledge": False,
    }

    # 1. Pool knowledge. Always -- shared facts are not a competitive edge.
    blob = teacher.export_knowledge()
    if blob:
        learner.import_knowledge(blob, cfg.knowledge_pool_weight)
        report["pooled_knowledge"] = True

    # 2. Capabilities, gated on an attributed lesson.
    for cap, lesson in propose_capability_transfers(kb, learner, teacher, cfg, lessons):
        trial = {cap: True}
        if _distance_after(learner, teacher, trial) < cfg.min_divergence:
            report["blocked"].append({"param": cap, "reason": "diversity floor"})
            continue
        if hasattr(learner, "grant_capability"):
            learner.grant_capability(cap)
        else:
            learner.params[cap] = True
        # A newly granted capability needs its peer's learned state to be
        # useful immediately -- switching on calibration with an empty curve
        # would make the bot worse for a round before it made it better.
        if blob:
            learner.import_knowledge(blob, cfg.knowledge_pool_weight)
        kb.record_transfer(
            round_index, teacher.name, learner.name, cap, False, True, lesson.confidence,
            {"lesson": lesson.statement, "n": lesson.support_n, "p": lesson.p_value},
        )
        report["capabilities"].append({"param": cap, "why": lesson.statement})

    # 3. Numeric parameters, gated on a significant contrast and then on the
    #    diversity floor applied to the batch as a whole.
    proposals = propose_parameter_transfers(kb, learner, teacher, cfg)
    changes = {p: new for p, _old, new, _w, _e in proposals}
    if changes:
        dist = _distance_after(learner, teacher, changes)
        scale = 1.0
        if dist < cfg.min_divergence:
            # Scale the whole batch back until the floor is respected, rather
            # than dropping individual parameters, which would silently bias
            # the transfer toward whichever ones happened to be enumerated
            # first.
            for candidate in (0.75, 0.5, 0.25, 0.0):
                trial = {
                    p: old + (new - old) * candidate for p, old, new, _w, _e in proposals
                }
                if _distance_after(learner, teacher, trial) >= cfg.min_divergence:
                    scale = candidate
                    break
            else:
                scale = 0.0
            if scale == 0.0:
                report["blocked"].append(
                    {"param": "all numeric", "reason": "diversity floor", "distance": round(dist, 4)}
                )

        for param, old, new, w, ev in proposals:
            applied = old + (new - old) * scale
            if abs(applied - old) < 1e-9:
                continue
            learner.params[param] = applied
            kb.record_transfer(
                round_index, teacher.name, learner.name, param, round(old, 5),
                round(learner.params[param], 5), w * scale, ev,
            )
            report["params"].append(
                {
                    "param": param,
                    "old": round(old, 5),
                    "new": round(learner.params[param], 5),
                    "weight": round(w * scale, 3),
                    "evidence": ev,
                }
            )

    return report


def cross_learn(kb: KnowledgeBase, round_index: int, bots: list, lessons: list,
                cfg: TransferConfig | None = None) -> list:
    """Every bot learns from every other. Symmetric on purpose.

    The loser learning from the winner is obvious. The winner learning from
    the loser is the part people skip, and it is where the interesting
    transfers live: a bot can be behind on P&L overall while being genuinely
    better at one slice of the market, and that slice is exactly what the
    segment-gated evidence is designed to find.
    """
    cfg = cfg or TransferConfig()
    reports = []
    for learner in bots:
        for teacher in bots:
            if learner is teacher:
                continue
            reports.append(apply_transfers(kb, round_index, learner, teacher, lessons, cfg))
    return reports
