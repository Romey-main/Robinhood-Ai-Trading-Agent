"""Command-line entry point.

    python -m rhbot screen   --universe data/universe.json --top 5
    python -m rhbot open     --universe data/universe.json --symbol AMD
    python -m rhbot mark     --bars "AMD=141.2:145.0:139.0"
    python -m rhbot report
    python -m rhbot status

Nothing here sends a real order. ``open`` records a *simulated* trade only.
"""

from __future__ import annotations

import argparse
import os

from .config import Config
from .models import Snapshot
from .screener import screen, score_one
from .risk import evaluate_trade
from .paper_engine import PaperLedger
from .providers import JsonProvider

DEFAULT_LEDGER = "ledger/paper_trades.json"
DEFAULT_CONFIG = "config.json"


def _cfg(path: str) -> Config:
    return Config.load(path) if os.path.exists(path) else Config()


def _load_universe(path: str) -> list[Snapshot]:
    return JsonProvider(path).all()


def cmd_screen(args) -> None:
    cfg = _cfg(args.config)
    snaps = _load_universe(args.universe)
    passers, rejected = screen(snaps, cfg, top=args.top)

    print(f"\nScreened {len(snaps)} symbols — top {len(passers)} of "
          f"{len(snaps) - len(rejected)} that passed filters:\n")
    for i, c in enumerate(passers, 1):
        print(f"  {i}. {c.symbol:<6} score {c.score:5.1f}   "
              + " | ".join(c.reasons))
    if rejected:
        print(f"\nFiltered out ({len(rejected)}):")
        for c in rejected:
            print(f"  - {c.symbol:<6} {'; '.join(c.reject_reasons)}")
    print("\nReminder: a high score is a *candidate*, not a buy signal. "
          "Paper-trade it first.\n")


def cmd_open(args) -> None:
    cfg = _cfg(args.config)
    if args.universe:
        snap = JsonProvider(args.universe).get_snapshot(args.symbol)
        if args.price:
            snap.price = args.price
    else:
        snap = Snapshot(
            symbol=args.symbol.upper(),
            price=args.price,
            avg_dollar_volume=cfg.min_avg_dollar_volume,
            volatility=(cfg.min_volatility + cfg.max_volatility) / 2,
            momentum=0.0,
            sentiment=args.sentiment,
        )

    decision = evaluate_trade(snap, cfg, open_positions=len(
        PaperLedger(args.ledger).open_trades), buying_power=args.buying_power)
    print(f"\nRisk check for {snap.symbol} @ ${snap.price:.2f}:")
    for r in decision.reasons:
        print(f"  - {r}")
    if not decision.approved:
        print("\nNot opening (failed risk check).\n")
        return

    ledger = PaperLedger(args.ledger)
    t = ledger.open_trade(
        symbol=snap.symbol, qty=decision.qty, entry_price=decision.entry_price,
        stop_price=decision.stop_price, target_price=decision.target_price,
        rationale=args.rationale, sentiment=snap.sentiment,
        score=score_one(snap, cfg).score,
    )
    ledger.save()
    print(f"\nPAPER trade opened [{t.id}]: {t.qty:g} {t.symbol} @ "
          f"${t.entry_price:.2f}  stop ${t.stop_price:.2f} / "
          f"target ${t.target_price:.2f}  (${t.notional:.2f} notional)\n")


def _parse_bars(spec: str):
    """'AMD=141.2:145:139,SOFI=8.1' -> {sym: (last, high, low)}"""
    out = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        sym, vals = part.split("=")
        nums = [float(x) for x in vals.split(":")]
        last = nums[0]
        high = nums[1] if len(nums) > 1 else None
        low = nums[2] if len(nums) > 2 else None
        out[sym.strip().upper()] = (last, high, low)
    return out


def cmd_mark(args) -> None:
    ledger = PaperLedger(args.ledger)
    bars = _parse_bars(args.bars)
    closed = []
    for sym, (last, high, low) in bars.items():
        closed.extend(ledger.mark(sym, last, high, low))
    ledger.save()
    if not closed:
        print("\nMarked. No stops/targets hit; open trades still running.\n")
    else:
        for t in closed:
            print(f"\nClosed [{t.id}] {t.symbol} via {t.exit_reason} @ "
                  f"${t.exit_price:.2f}  P&L ${t.pnl():+.2f} "
                  f"({(t.pnl_pct() or 0)*100:+.1f}%)")
        print()


def cmd_report(args) -> None:
    s = PaperLedger(args.ledger).stats()
    print("\n=== Paper-trading report ===")
    print(f"  trades: {s['n_closed']} closed, {s['n_open']} open")
    if s["n_closed"]:
        wr = (s["win_rate"] or 0) * 100
        print(f"  win rate:      {wr:.0f}%  ({s['n_wins']}W / {s['n_losses']}L)")
        print(f"  avg win:       {(s['avg_win_pct'] or 0)*100:+.1f}%")
        print(f"  avg loss:      {(s['avg_loss_pct'] or 0)*100:+.1f}%")
        print(f"  expectancy:    {(s['expectancy_pct'] or 0)*100:+.2f}% / trade")
        print(f"  profit factor: {s['profit_factor']}")
        print(f"  total P&L:     ${s['total_pnl']:+.2f}")
        print(f"  max drawdown:  ${s['max_drawdown']:.2f}")
    print(f"  note: {s['note']}\n")


def cmd_status(args) -> None:
    ledger = PaperLedger(args.ledger)
    if not ledger.open_trades:
        print("\nNo open paper positions.\n")
        return
    print("\nOpen paper positions:")
    for t in ledger.open_trades:
        print(f"  [{t.id}] {t.qty:g} {t.symbol} @ ${t.entry_price:.2f}  "
              f"stop ${t.stop_price:.2f} / target ${t.target_price:.2f}  "
              f"— {t.rationale}")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rhbot", description=__doc__)
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    p.add_argument("--config", default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screen", help="rank a universe")
    s.add_argument("--universe", required=True)
    s.add_argument("--top", type=int, default=5)
    s.set_defaults(func=cmd_screen)

    o = sub.add_parser("open", help="record a PAPER trade")
    o.add_argument("--symbol", required=True)
    o.add_argument("--universe", help="pull the snapshot (and sentiment) from here")
    o.add_argument("--price", type=float, default=0.0)
    o.add_argument("--sentiment", type=float, default=0.0)
    o.add_argument("--buying-power", type=float, default=None)
    o.add_argument("--rationale", default="")
    o.set_defaults(func=cmd_open)

    m = sub.add_parser("mark", help="mark open trades against new prices")
    m.add_argument("--bars", required=True,
                   help="'SYM=last:high:low,...' (high/low optional)")
    m.set_defaults(func=cmd_mark)

    sub.add_parser("report", help="performance stats").set_defaults(func=cmd_report)
    sub.add_parser("status", help="open positions").set_defaults(func=cmd_status)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
