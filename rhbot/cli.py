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
from .strategy_config import StrategyConfig
from .panel import Panel
from .universe import Membership, Denylist
from .strategies import REGISTRY
from .portfolio_risk import vet
from .backtest import (run_backtest, weekly_rebalance_dates,
                       monthly_rebalance_dates)
from . import ingest

DEFAULT_LEDGER = "ledger/paper_trades.json"
DEFAULT_CONFIG = "config.json"
DEFAULT_STRATEGY_CONFIG = "strategy_config.json"
DEFAULT_DENYLIST = "data/denylist.txt"


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


def _load_sectors(path):
    import json
    import os
    path = path or "data/sectors.json"
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return None


def _load_strategy_inputs(args):
    scfg = StrategyConfig.load(args.strategy_config)
    panel = Panel.from_csv(args.panel)
    members_path = getattr(args, "members", None)
    membership = Membership.from_json(members_path, fallback_symbols=set(panel.symbols())) \
        if members_path else Membership(None, set(panel.symbols()))
    denylist = Denylist.from_file(args.denylist)
    strat_cls = REGISTRY[args.strategy]
    top_n = getattr(args, "top_n", None)
    strat = strat_cls(top_n=top_n) if top_n else strat_cls()
    sectors = _load_sectors(getattr(args, "sectors", None))
    return scfg, panel, membership, denylist, strat, sectors


def cmd_rebalance(args) -> None:
    scfg, panel, membership, denylist, strat, sectors = _load_strategy_inputs(args)
    asof = args.asof or panel.latest_date()
    if not membership.has_snapshots:
        print("\n[!] No point-in-time membership file given — falling back to the "
              "panel's symbols. For a real run, supply --members to avoid "
              "survivorship bias.")
    members = membership.members_asof(asof) if membership.has_snapshots else set(panel.symbols())

    # global feed-freshness gate before we even rank
    from .data_quality import _days_between
    stale = _days_between(panel.latest_date(), asof)
    print(f"\n=== {strat.name} rebalance @ {asof} ===")
    print(f"feed latest date {panel.latest_date()} ({stale}d from asof); "
          f"denylist={len(denylist)} names; universe={len(members)}")

    basket = strat.generate(panel, members, asof, scfg, denylist=denylist, sectors=sectors)
    decision = vet(basket, scfg, account_value=args.account_value)

    d = decision.diagnostics
    print(f"data-quality pass {d['dq_pass']}/{d['considered']} "
          f"({d['dq_rate']*100:.0f}%); after filters {d['valid_after_filters']}; "
          f"selected {d['selected']}")
    if basket.notes:
        for n in basket.notes:
            print(f"  note: {n}")

    print(f"\n>>> DECISION: {decision.action} <<<")
    for b in decision.blockers:
        print(f"  BLOCKER: {b}")
    for w in decision.warnings:
        print(f"  warning: {w}")

    if decision.will_trade:
        print(f"\nTarget basket ({d['gross_exposure']*100:.0f}% invested, "
              f"{decision.cash_weight*100:.0f}% cash):")
        for s in basket.selected:
            print(f"  {s:<6} {decision.final_weights.get(s, 0)*100:5.2f}%")
    # show a few representative rejects for transparency
    if basket.rejects:
        sample = list(basket.rejects.items())[:6]
        print(f"\nRejected (showing {len(sample)}/{len(basket.rejects)}):")
        for s, why in sample:
            print(f"  - {s:<6} {why}")
    print("\nPAPER/ADVISORY only — this command places no orders.\n")


def cmd_backtest(args) -> None:
    scfg, panel, membership, denylist, strat, sectors = _load_strategy_inputs(args)
    if args.freq == "weekly":
        dates, ppy = weekly_rebalance_dates(panel), 52.0
    else:
        dates, ppy = monthly_rebalance_dates(panel), 12.0
    if len(dates) < 3:
        print("\nNot enough rebalance dates in the panel to backtest.\n")
        return
    res = run_backtest(strat, panel, membership, scfg, dates, ppy,
                       denylist=denylist, drift_skip=args.drift_skip, sectors=sectors)
    s = res.stats
    print(f"\n=== Backtest: {strat.name} ({args.freq}, {s.get('n_periods')} periods) ===")
    print(f"  Sharpe:        {s.get('sharpe')}")
    print(f"  ann. return:   {s.get('ann_return', 0)*100:+.1f}%")
    print(f"  ann. vol:      {s.get('ann_vol', 0)*100:.1f}%")
    print(f"  total return:  {s.get('total_return', 0)*100:+.1f}%")
    print(f"  max drawdown:  {s.get('max_drawdown', 0)*100:.1f}%")
    print(f"  hit rate:      {s.get('hit_rate', 0)*100:.0f}%")
    print(f"  traded {res.n_traded}/{res.n_periods} periods, "
          f"{res.n_cash} in cash; cost {scfg.cost_bps:.0f}bps/turnover")
    if s.get("missing_forward_prices"):
        print(f"  note: {s['missing_forward_prices']} missing forward prices "
              f"(treated as exited at entry)")
    print()


def cmd_build_membership(args) -> None:
    if args.holdings_dir:
        snaps = ingest.membership_from_holdings_dir(
            args.holdings_dir, ticker_col=args.ticker_col, glob_pat=args.glob)
    elif args.long_csv:
        snaps = ingest.membership_from_long_csv(
            args.long_csv, date_col=args.date_col, ticker_col=args.ticker_col_long)
    else:
        print("\nProvide --holdings-dir or --long-csv.\n")
        return
    n = ingest.write_membership_json(snaps, args.out)
    sizes = sorted(len(v) for v in snaps.values())
    print(f"\nWrote {args.out}: {n} dated snapshots; constituents per snapshot "
          f"min/median/max = {sizes[0] if sizes else 0}/"
          f"{sizes[len(sizes)//2] if sizes else 0}/{sizes[-1] if sizes else 0}")
    print("Validate this against a known date before trusting it.\n")


def cmd_build_panel(args) -> None:
    n = ingest.panel_from_long_csv(
        args.long_csv, args.out, date_col=args.date_col,
        symbol_col=args.symbol_col, price_col=args.price_col)
    print(f"\nWrote {args.out}: {n} rows. Prices MUST be split+dividend adjusted "
          f"(adj_close) — unadjusted prices will read splits as crashes.\n")


def cmd_snapshot_membership(args) -> None:
    if args.holdings_csv:
        tickers = ingest.tickers_from_holdings_file(args.holdings_csv, args.ticker_col)
    elif args.tickers_file:
        with open(args.tickers_file) as fh:
            tickers = {ln.split("#", 1)[0].strip() for ln in fh if ln.strip()}
    elif args.from_panel:
        tickers = set(Panel.from_csv(args.from_panel).symbols())
    else:
        print("\nProvide --holdings-csv, --tickers-file, or --from-panel.\n")
        return
    import datetime
    date = args.date or datetime.date.today().isoformat()
    total = ingest.append_membership_snapshot(args.out, date, tickers)
    print(f"\nRecorded {len(tickers)} constituents for {date} in {args.out} "
          f"({total} snapshot(s) total).")
    print("Run this on a schedule to accumulate point-in-time history (the free "
          "path to a bias-free backtest).\n")


def cmd_build_panel_yahoo(args) -> None:
    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else
            [ln.split("#", 1)[0].strip().upper()
             for ln in open(args.symbols_file) if ln.strip()])
    n = ingest.build_panel_csv(syms, args.start, args.out)
    print(f"\nWrote {args.out}: {n} rows for {len(syms)} symbols (Yahoo, "
          f"auto-adjusted).\n")


def cmd_fetch_prices(args) -> None:
    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols else
            [ln.split("#", 1)[0].strip().upper()
             for ln in open(args.symbols_file) if ln.strip()])
    base = ingest.PRICE_SOURCES[args.source]
    if args.source == "tiingo":
        token = args.tiingo_token
        fetch = lambda s, start: ingest.tiingo_fetch(s, start, token=token)  # noqa: E731
    else:
        fetch = base
    n = ingest.build_panel_csv(syms, args.start, args.out, fetch=fetch)
    note = "" if args.source != "stooq" else " (stooq: split- not dividend-adjusted)"
    print(f"\nWrote {args.out}: {n} rows for {len(syms)} symbols via {args.source}{note}.\n")


def cmd_membership_diff(args) -> None:
    snaps = ingest.load_membership(args.members)
    a, b = (args.date_from, args.date_to) if args.date_from and args.date_to \
        else ingest.two_latest_dates(snaps)
    if not a or not b:
        print("\nNeed at least two snapshots (or pass --from/--to) to diff.\n")
        return
    d = ingest.diff_snapshots(snaps, a, b)
    print(f"\n=== Membership change {d['from']} -> {d['to']} ===")
    print(f"  unchanged: {d['unchanged']}")
    print(f"  added ({len(d['added'])}): {', '.join(d['added']) or '-'}")
    print(f"  removed ({len(d['removed'])}): {', '.join(d['removed']) or '-'}")
    if len(d['added']) > 50 or len(d['removed']) > 50:
        print("  [!] large churn — verify the new snapshot before trading it")
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

    def _add_strategy_args(sp):
        sp.add_argument("--strategy", required=True, choices=sorted(REGISTRY))
        sp.add_argument("--panel", required=True, help="CSV: date,symbol,adj_close")
        sp.add_argument("--members", help="JSON of point-in-time index membership")
        sp.add_argument("--denylist", default=DEFAULT_DENYLIST)
        sp.add_argument("--strategy-config", default=DEFAULT_STRATEGY_CONFIG)
        sp.add_argument("--sectors", default=None,
                        help="JSON {ticker: sector} for sector caps (default: data/sectors.json)")
        sp.add_argument("--top-n", type=int, default=None,
                        help="override basket size (default: spec value)")

    rb = sub.add_parser("rebalance", help="vetted target basket for a strategy (no orders)")
    _add_strategy_args(rb)
    rb.add_argument("--asof", help="evaluation date (default: panel's latest)")
    rb.add_argument("--account-value", type=float, default=None)
    rb.set_defaults(func=cmd_rebalance)

    bt = sub.add_parser("backtest", help="walk-forward backtest through the risk pipeline")
    _add_strategy_args(bt)
    bt.add_argument("--freq", choices=["weekly", "monthly"], default="weekly")
    bt.add_argument("--drift-skip", action="store_true",
                    help="skip rebalance when L1 drift < threshold (momentum)")
    bt.set_defaults(func=cmd_backtest)

    bm = sub.add_parser("build-membership",
                        help="vendor holdings -> point-in-time membership.json")
    bm.add_argument("--holdings-dir", help="dir of dated holdings CSVs (date in filename)")
    bm.add_argument("--long-csv", help="single CSV with date+ticker columns")
    bm.add_argument("--ticker-col", default="Ticker", help="ticker column in holdings files")
    bm.add_argument("--glob", default="*.csv")
    bm.add_argument("--date-col", default="date", help="date column (--long-csv)")
    bm.add_argument("--ticker-col-long", default="ticker", help="ticker column (--long-csv)")
    bm.add_argument("--out", default="data/membership.json")
    bm.set_defaults(func=cmd_build_membership)

    bp = sub.add_parser("build-panel",
                        help="vendor price CSV -> engine panel CSV")
    bp.add_argument("--long-csv", required=True)
    bp.add_argument("--date-col", default="date")
    bp.add_argument("--symbol-col", default="symbol")
    bp.add_argument("--price-col", default="adj_close")
    bp.add_argument("--out", default="data/panel.csv")
    bp.set_defaults(func=cmd_build_panel)

    sm = sub.add_parser("snapshot-membership",
                        help="record today's constituents -> point-in-time history")
    sm.add_argument("--holdings-csv", help="a vendor holdings file (e.g. iShares IWB export)")
    sm.add_argument("--tickers-file", help="one ticker per line")
    sm.add_argument("--from-panel", help="use the symbols present in a panel CSV")
    sm.add_argument("--ticker-col", default="Ticker")
    sm.add_argument("--date", help="snapshot date (default: today)")
    sm.add_argument("--out", default="data/membership.json")
    sm.set_defaults(func=cmd_snapshot_membership)

    py = sub.add_parser("build-panel-yahoo",
                        help="self-serve adjusted-price panel via yfinance (no keys)")
    py.add_argument("--symbols", help="comma-separated tickers")
    py.add_argument("--symbols-file", help="one ticker per line")
    py.add_argument("--start", default="2010-01-01")
    py.add_argument("--out", default="data/panel.csv")
    py.set_defaults(func=cmd_build_panel_yahoo)

    fp = sub.add_parser("fetch-prices",
                        help="adjusted-price panel from a vendor (yahoo/tiingo/stooq)")
    fp.add_argument("--source", choices=sorted(ingest.PRICE_SOURCES), default="tiingo")
    fp.add_argument("--symbols", help="comma-separated tickers")
    fp.add_argument("--symbols-file", help="one ticker per line")
    fp.add_argument("--start", default="2010-01-01")
    fp.add_argument("--tiingo-token", default=None, help="overrides TIINGO_API_KEY")
    fp.add_argument("--out", default="data/panel.csv")
    fp.set_defaults(func=cmd_fetch_prices)

    md = sub.add_parser("membership-diff",
                        help="audit constituent changes between two snapshots")
    md.add_argument("--members", default="data/membership.json")
    md.add_argument("--from", dest="date_from", help="from-date (default: 2nd latest)")
    md.add_argument("--to", dest="date_to", help="to-date (default: latest)")
    md.set_defaults(func=cmd_membership_diff)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
