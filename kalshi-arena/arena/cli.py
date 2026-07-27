"""Command line entry point for the arena."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bots.challenger import CHALLENGER_DEFAULTS, ChallengerBot
from .bots.incumbent import IncumbentBot
from .bots.base import Params
from .learning.knowledge import KnowledgeBase
from .learning.lessons import render_markdown
from .learning.tournament import TournamentConfig, run_tournament
from .learning.transfer import TransferConfig


def _fmt_table(rows: list, cols: list) -> str:
    if not rows:
        return "(no rows)"
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    out = [" | ".join(c.ljust(widths[c]) for c in cols)]
    out.append("-|-".join("-" * widths[c] for c in cols))
    for r in rows:
        out.append(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    return "\n".join(out)


SCORE_COLS = [
    "bot",
    "round",
    "pnl_$",
    "return_%",
    "maxDD_%",
    "sharpe",
    "trades",
    "contracts",
    "fees_$",
    "fee_per_ct_c",
    "maker_%",
    "brier",
    "brier_skill_vs_mkt",
    "win_%",
]


def cmd_compete(args) -> int:
    bots = [IncumbentBot(seed=args.seed), ChallengerBot(seed=args.seed)]
    cfg = TournamentConfig(
        rounds=args.rounds,
        bankroll_dollars=args.bankroll,
        n_markets=args.markets,
        n_ticks=args.ticks,
        regime=args.regime,
        seed=args.seed,
        learning=not args.no_learning,
        kb_path=args.kb,
        lessons_path=args.lessons,
        results_path=args.results,
        transfer=TransferConfig(min_divergence=args.min_divergence),
    )
    report = run_tournament(bots, cfg)

    print(f"\n=== KALSHI ARENA — {cfg.rounds} rounds, regime={cfg.regime}, "
          f"{cfg.n_markets} markets/round, ${cfg.bankroll_dollars:.0f} bankroll ===")
    print(f"    learning: {'ON' if cfg.learning else 'OFF'}   seed: {cfg.seed}\n")

    rows = []
    for h in report["history"]:
        for name, sc in sorted(h["scores"].items()):
            rows.append(sc)
    print(_fmt_table(rows, SCORE_COLS))

    print("\n--- Totals across all rounds ---")
    tot_rows = []
    for name, t in sorted(report["totals"].items()):
        tot_rows.append({"bot": name, **t})
    print(_fmt_table(tot_rows, ["bot", "pnl_$", "rounds_won", "trades", "contracts",
                                "fees_$", "mean_brier", "mean_brier_skill"]))

    lessons = [ls for h in report["history"] for ls in h["lessons"]]
    if lessons:
        print(f"\n--- Lessons mined ({len(lessons)}) ---")
        for ls in lessons[: args.show_lessons]:
            print(f"  [{ls['kind']:10s}] {ls['statement']}")

    n_transfers = sum(
        len(t.get("params", [])) + len(t.get("capabilities", []))
        for h in report["history"]
        for t in h["transfers"]
    )
    print(f"\n--- Cross-learning: {n_transfers} transfers applied ---")
    for h in report["history"]:
        for t in h["transfers"]:
            for cap in t.get("capabilities", []):
                print(f"  r{h['round']} {t['teacher']} -> {t['learner']}: "
                      f"GRANTED {cap['param']}")
            for p in t.get("params", []):
                print(f"  r{h['round']} {t['teacher']} -> {t['learner']}: "
                      f"{p['param']} {p['old']} -> {p['new']} (w={p['weight']})")
    print("\n--- Parameter divergence per round (0 = identical bots) ---")
    for h in report["history"]:
        print(f"  r{h['round']}: " + ", ".join(f"{k} = {v}" for k, v in h["divergence"].items()))

    print(f"\nWrote {cfg.results_path} and {cfg.lessons_path}")
    return 0


def cmd_ablate(args) -> int:
    """Turn the challenger's improvements off one at a time.

    A bot that wins is not an explanation. This measures how much each
    individual change is worth by disabling it and re-running the same
    worlds, which is the only way to tell a real improvement from one that
    happens to ride along with a lucky seed.
    """
    features = [
        "use_market_prior",
        "use_calibration",
        "use_exact_fees",
        "use_maker_first",
        "use_exits",
        "use_cluster_caps",
    ]
    results = []

    for disabled in [None] + features:
        vals = dict(CHALLENGER_DEFAULTS)
        if disabled:
            vals[disabled] = False
        bots = [
            IncumbentBot(seed=args.seed),
            ChallengerBot(Params(vals, locked=set(), bounds={}), seed=args.seed),
        ]
        cfg = TournamentConfig(
            rounds=args.rounds,
            bankroll_dollars=args.bankroll,
            n_markets=args.markets,
            n_ticks=args.ticks,
            regime=args.regime,
            seed=args.seed,
            learning=False,  # ablation measures the design, not the learning
            kb_path=f"knowledge/ablate-{disabled or 'full'}.sqlite",
            lessons_path=f"knowledge/ablate-{disabled or 'full'}.md",
            results_path=f"knowledge/ablate-{disabled or 'full'}.json",
        )
        report = run_tournament(bots, cfg)
        t = report["totals"]
        results.append(
            {
                "disabled": disabled or "(nothing — full challenger)",
                "challenger_pnl_$": t["challenger"]["pnl_$"],
                "incumbent_pnl_$": t["incumbent"]["pnl_$"],
                "edge_$": round(t["challenger"]["pnl_$"] - t["incumbent"]["pnl_$"], 2),
                "brier": t["challenger"]["mean_brier"],
                "trades": t["challenger"]["trades"],
                "fees_$": t["challenger"]["fees_$"],
            }
        )

    baseline = results[0]["edge_$"]
    for r in results:
        r["cost_of_removing_$"] = round(baseline - r["edge_$"], 2)

    print(f"\n=== ABLATION — {args.rounds} rounds, regime={args.regime}, seed={args.seed} ===")
    print("Each row disables one challenger feature. `cost_of_removing_$` is how")
    print("much edge over the incumbent is lost when that feature is switched off.\n")
    print(
        _fmt_table(
            results,
            ["disabled", "challenger_pnl_$", "incumbent_pnl_$", "edge_$",
             "cost_of_removing_$", "trades", "fees_$", "brier"],
        )
    )
    Path("knowledge/ablation.json").parent.mkdir(parents=True, exist_ok=True)
    Path("knowledge/ablation.json").write_text(json.dumps(results, indent=2))
    print("\nWrote knowledge/ablation.json")
    return 0


def cmd_lessons(args) -> int:
    kb = KnowledgeBase(args.kb)
    print(render_markdown(kb, limit=args.limit))
    kb.close()
    return 0


def cmd_record(args) -> int:
    from .harvest import record

    record(minutes=args.minutes, every=args.every, max_close_hours=args.max_close_hours)
    return 0


def cmd_settle(args) -> int:
    from .harvest import settle

    settle()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="arena",
        description="Two Kalshi bots compete on a paper exchange and learn from each other.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compete", help="run the tournament")
    c.add_argument("--rounds", type=int, default=8)
    c.add_argument("--markets", type=int, default=150)
    c.add_argument("--ticks", type=int, default=60)
    c.add_argument("--bankroll", type=float, default=1000.0)
    c.add_argument("--regime", default="mixed", choices=["mixed", "efficient", "inefficient"])
    c.add_argument("--seed", type=int, default=7)
    c.add_argument("--no-learning", action="store_true", help="disable cross-learning")
    c.add_argument("--min-divergence", type=float, default=0.12)
    c.add_argument("--kb", default="knowledge/kb.sqlite")
    c.add_argument("--lessons", default="knowledge/LESSONS.md")
    c.add_argument("--results", default="knowledge/results.json")
    c.add_argument("--show-lessons", type=int, default=12)
    c.set_defaults(func=cmd_compete)

    a = sub.add_parser("ablate", help="measure what each challenger feature is worth")
    a.add_argument("--rounds", type=int, default=6)
    a.add_argument("--markets", type=int, default=150)
    a.add_argument("--ticks", type=int, default=60)
    a.add_argument("--bankroll", type=float, default=1000.0)
    a.add_argument("--regime", default="mixed", choices=["mixed", "efficient", "inefficient"])
    a.add_argument("--seed", type=int, default=7)
    a.set_defaults(func=cmd_ablate)

    ls = sub.add_parser("lessons", help="print the knowledge base as markdown")
    ls.add_argument("--kb", default="knowledge/kb.sqlite")
    ls.add_argument("--limit", type=int, default=60)
    ls.set_defaults(func=cmd_lessons)

    rec = sub.add_parser("record", help="record real Kalshi books (read-only)")
    rec.add_argument("--minutes", type=int, default=60)
    rec.add_argument("--every", type=int, default=60)
    rec.add_argument("--max-close-hours", type=int, default=48)
    rec.set_defaults(func=cmd_record)

    st = sub.add_parser("settle", help="join recorded tickers to their outcomes")
    st.set_defaults(func=cmd_settle)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
