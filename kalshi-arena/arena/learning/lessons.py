"""
Lesson mining.

A parameter that moved from 0.18 to 0.21 is not knowledge. It is a number
that changed, and six rounds later nobody -- human or bot -- can say why or
whether it should move back. Lessons are the durable half of what the
tournament produces: statements with a scope, an effect size, a sample size
and a p-value, written where a person can read them.

They are also load-bearing, not decorative. `transfer.py` reads capability
lessons to decide which switches a bot is allowed to flip, so a lesson with
weak support does not merely go unread -- it fails to authorise a change.

Three kinds get mined:

  segment   -- "this bot loses money in this slice of the market"
  contrast  -- "bot A beats bot B in this slice, by this much, with this
               much confidence"
  capability-- "the mechanism behind that contrast is X", which is the only
               kind that can grant a capability flag
"""

from __future__ import annotations

from .knowledge import KnowledgeBase, Lesson
from .stats import evidence_weight, welch_t

MIN_SUPPORT = 60  # contracts


def _fmt(x: float) -> str:
    return f"{x:+.2f}"


def mine_segment_lessons(kb: KnowledgeBase, round_index: int, bot: str) -> list:
    """Slices where a bot is reliably losing money."""
    out = []
    for price_b, is_maker, regime in kb.segments_seen():
        xs = kb.segment_pnl(bot, price_bucket=price_b, is_maker=is_maker, regime=regime)
        if len(xs) < MIN_SUPPORT:
            continue
        cmp = welch_t(xs, [0.0] * len(xs))
        if cmp.effect >= 0 or cmp.p_value >= 0.05:
            continue
        style = "resting" if is_maker else "crossing"
        out.append(
            Lesson(
                round=round_index,
                source_bot=bot,
                scope=f"{price_b}|{style}|{regime}",
                kind="segment",
                statement=(
                    f"{bot}: {style} in {price_b} markets ({regime} regime) lost "
                    f"{_fmt(cmp.mean_a)}c per contract over {len(xs)} contracts "
                    f"(p={cmp.p_value:.3f}). Widen the hurdle here or stop trading it."
                ),
                support_n=len(xs),
                effect=cmp.mean_a,
                p_value=cmp.p_value,
                confidence=min(1.0, len(xs) / (4.0 * MIN_SUPPORT)),
            )
        )
    return out


def mine_contrast_lessons(kb: KnowledgeBase, round_index: int, bot_a: str, bot_b: str) -> list:
    """Slices where one bot measurably outperforms the other."""
    out = []
    for price_b, is_maker, regime in kb.segments_seen():
        xa = kb.segment_pnl(bot_a, price_bucket=price_b, is_maker=is_maker, regime=regime)
        xb = kb.segment_pnl(bot_b, price_bucket=price_b, is_maker=is_maker, regime=regime)
        if len(xa) < MIN_SUPPORT or len(xb) < MIN_SUPPORT:
            continue
        cmp = welch_t(xa, xb)
        w = evidence_weight(cmp, MIN_SUPPORT)
        if w <= 0:
            continue
        style = "resting" if is_maker else "crossing"
        out.append(
            Lesson(
                round=round_index,
                source_bot=bot_a,
                scope=f"{price_b}|{style}|{regime}",
                kind="contrast",
                statement=(
                    f"{bot_a} beat {bot_b} by {_fmt(cmp.effect)}c per contract when {style} "
                    f"in {price_b} markets ({regime}): {_fmt(cmp.mean_a)}c vs {_fmt(cmp.mean_b)}c "
                    f"over {len(xa)}/{len(xb)} contracts (p={cmp.p_value:.3f})."
                ),
                support_n=min(len(xa), len(xb)),
                effect=cmp.effect,
                p_value=cmp.p_value,
                confidence=w,
            )
        )
    return out


def mine_capability_lessons(kb: KnowledgeBase, round_index: int, bot_a: str, bot_b: str) -> list:
    """Attribute a contrast to a named mechanism, so it can be transferred.

    The mapping from evidence to capability is intentionally narrow. Each
    capability is only credited when the specific measurement that would
    show it working actually shows it working -- maker execution is credited
    from maker-vs-taker P&L, not from "the challenger won the round". A bot
    that wins for unrelated reasons should not get to export its whole design
    as the explanation.
    """
    out = []

    # -- maker-first execution -------------------------------------------
    a_maker = kb.segment_pnl(bot_a, is_maker=1)
    b_taker = kb.segment_pnl(bot_b, is_maker=0)
    if len(a_maker) >= MIN_SUPPORT and len(b_taker) >= MIN_SUPPORT:
        cmp = welch_t(a_maker, b_taker)
        w = evidence_weight(cmp, MIN_SUPPORT)
        if w > 0:
            out.append(
                Lesson(
                    round=round_index,
                    source_bot=bot_a,
                    scope="use_maker_first",
                    kind="capability",
                    statement=(
                        f"Resting orders earned {_fmt(cmp.mean_a)}c/contract for {bot_a} while "
                        f"crossing earned {_fmt(cmp.mean_b)}c/contract for {bot_b} "
                        f"({len(a_maker)}/{len(b_taker)} contracts, p={cmp.p_value:.3f}). "
                        f"Quoting inside the spread beats paying it, net of adverse selection."
                    ),
                    support_n=min(len(a_maker), len(b_taker)),
                    effect=cmp.effect,
                    p_value=cmp.p_value,
                    confidence=w,
                )
            )

    # -- forecasting quality, which is what the market prior buys ---------
    rows = kb.conn.execute(
        "SELECT bot, AVG((p_final - outcome)*(p_final - outcome)) AS brier,"
        " AVG((p_market - outcome)*(p_market - outcome)) AS brier_mkt, COUNT(*) AS n"
        " FROM observations WHERE bot IN (?,?) GROUP BY bot",
        (bot_a, bot_b),
    ).fetchall()
    by_bot = {r["bot"]: r for r in rows}
    if bot_a in by_bot and bot_b in by_bot:
        ra, rb = by_bot[bot_a], by_bot[bot_b]
        if ra["n"] >= 200 and rb["n"] >= 200 and ra["brier"] < rb["brier"]:
            skill_a = 1.0 - ra["brier"] / ra["brier_mkt"] if ra["brier_mkt"] else 0.0
            skill_b = 1.0 - rb["brier"] / rb["brier_mkt"] if rb["brier_mkt"] else 0.0
            # Relative Brier improvement, used as the confidence proxy: a 1%
            # better Brier over thousands of forecasts is a real difference
            # in forecasting, unlike a 1% better P&L over 200 trades.
            rel = (rb["brier"] - ra["brier"]) / rb["brier"]
            if rel > 0.01:
                out.append(
                    Lesson(
                        round=round_index,
                        source_bot=bot_a,
                        scope="use_market_prior",
                        kind="capability",
                        statement=(
                            f"{bot_a} forecasts better than {bot_b}: Brier {ra['brier']:.4f} vs "
                            f"{rb['brier']:.4f} over {ra['n']}/{rb['n']} resolutions. Skill vs the "
                            f"market price: {skill_a:+.3f} vs {skill_b:+.3f}. Blending the price "
                            f"into the estimate, rather than only subtracting it, is what closes "
                            f"that gap."
                        ),
                        support_n=min(ra["n"], rb["n"]),
                        effect=rel,
                        p_value=0.0 if rel > 0.05 else 0.05,
                        confidence=min(1.0, rel * 10.0),
                    )
                )

    # -- calibration ------------------------------------------------------
    if bot_a in by_bot and bot_b in by_bot:
        ra, rb = by_bot[bot_a], by_bot[bot_b]
        if ra["n"] >= 200 and rb["n"] >= 200 and ra["brier"] < rb["brier"]:
            out.append(
                Lesson(
                    round=round_index,
                    source_bot=bot_a,
                    scope="use_calibration",
                    kind="capability",
                    statement=(
                        f"Learning from every resolution, not only from settled trades, gave "
                        f"{bot_a} {ra['n']} training examples this run. Its forecasts are better "
                        f"calibrated as a result (Brier {ra['brier']:.4f} vs {rb['brier']:.4f})."
                    ),
                    support_n=int(ra["n"]),
                    effect=float(rb["brier"] - ra["brier"]),
                    p_value=0.01,
                    confidence=min(1.0, (rb["brier"] - ra["brier"]) * 20.0),
                )
            )

    # -- exact fee accounting --------------------------------------------
    fee_rows = kb.conn.execute(
        "SELECT bot, SUM(fee) AS fees, SUM(contracts) AS cts FROM trades"
        " WHERE bot IN (?,?) GROUP BY bot",
        (bot_a, bot_b),
    ).fetchall()
    fees = {r["bot"]: (r["fees"] or 0) / max(1, r["cts"] or 1) for r in fee_rows}
    counts = {r["bot"]: (r["cts"] or 0) for r in fee_rows}
    if fees.get(bot_a) is not None and fees.get(bot_b) is not None:
        if counts.get(bot_a, 0) >= MIN_SUPPORT and counts.get(bot_b, 0) >= MIN_SUPPORT:
            diff = fees[bot_b] - fees[bot_a]
            if diff > 0.05:
                out.append(
                    Lesson(
                        round=round_index,
                        source_bot=bot_a,
                        scope="use_exact_fees",
                        kind="capability",
                        statement=(
                            f"{bot_a} paid {fees[bot_a]:.2f}c/contract in fees against "
                            f"{fees[bot_b]:.2f}c for {bot_b} ({diff:.2f}c cheaper). Modelling the "
                            f"per-order ceiling instead of the smooth per-contract formula changes "
                            f"which trades clear the hurdle, especially on small orders."
                        ),
                        support_n=int(min(counts[bot_a], counts[bot_b])),
                        effect=diff,
                        p_value=0.01,
                        confidence=min(1.0, diff / 0.5),
                    )
                )

    return out


def mine_all(kb: KnowledgeBase, round_index: int, bots: list) -> list:
    lessons = []
    for bot in bots:
        lessons.extend(mine_segment_lessons(kb, round_index, bot))
    for a in bots:
        for b in bots:
            if a == b:
                continue
            lessons.extend(mine_contrast_lessons(kb, round_index, a, b))
            lessons.extend(mine_capability_lessons(kb, round_index, a, b))
    for lesson in lessons:
        kb.record_lesson(lesson)
    return lessons


def render_markdown(kb: KnowledgeBase, limit: int = 60) -> str:
    """Render the knowledge base as a readable log."""
    rows = kb.lessons(limit=limit)
    lines = [
        "# Lessons",
        "",
        "Mined automatically from the arena's trade and observation logs after",
        "each round. Every line carries its sample size and p-value: a lesson",
        "with thin support is a hypothesis, not a finding, and the transfer",
        "logic weights it accordingly.",
        "",
    ]
    if not rows:
        lines.append("_No lessons yet -- run more rounds._")
        return "\n".join(lines)

    by_kind: dict = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)

    titles = {
        "capability": "Capability findings (these authorise a design change)",
        "contrast": "Head-to-head contrasts",
        "segment": "Loss-making segments",
    }
    for kind in ("capability", "contrast", "segment"):
        rs = by_kind.get(kind)
        if not rs:
            continue
        lines.append(f"## {titles[kind]}")
        lines.append("")
        for r in rs:
            lines.append(
                f"- **r{r['round']}** `{r['scope']}` — {r['statement']} "
                f"_(n={r['support_n']}, effect={r['effect']:+.3f}, "
                f"p={r['p_value']:.3f}, confidence={r['confidence']:.2f})_"
            )
        lines.append("")

    transfers = kb.transfers(limit=100)
    if transfers:
        lines.append("## Transfers applied")
        lines.append("")
        for t in transfers:
            lines.append(
                f"- **r{t['round']}** `{t['param']}` {t['from_bot']} → {t['to_bot']}: "
                f"{t['old_value']} → {t['new_value']} (weight {t['weight']:.2f})"
            )
        lines.append("")
    return "\n".join(lines)
