"""
Real-market recorder.

Why this exists, and why it is slower than it looks like it should be.

Kalshi's API will happily hand you thousands of settled binary markets with
their results. It is tempting to treat that as a free backtest set. It is
not: the prices stored on a settled market are post-resolution. A market that
resolved YES reports a last price near $0.99 and a previous price near $0.99,
because that is what it traded at once the answer was known. Bucketing those
prices against outcomes produces a beautiful, perfectly calibrated, entirely
fake result -- measured on real data from this API:

    prev_price 0.0-0.1: n=85  realised YES = 0.00
    prev_price 0.9-1.0: n=85  realised YES = 1.00

That is not forecasting skill. That is reading the answer off the back of the
page. Any bot evaluated this way looks brilliant and loses money live.

The only unbiased path is to record books *before* the outcome is known and
join the settlements *after*:

    python -m arena.harvest record --minutes 90 --every 60
    ...wait for the markets to actually resolve...
    python -m arena.harvest settle

`record` writes one JSON line per poll to `data/snapshots-<date>.jsonl`.
`settle` looks up every recorded ticker, keeps the ones that have finalised,
and writes `data/settlements-<date>.jsonl`. The pair is a genuine, leak-free
dataset that `ReplayFeed` can run the arena over.

The cost is calendar time: a dataset covering markets that resolve tomorrow
exists tomorrow. There is no way around that which is also honest.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .kalshi_public import KalshiPublicClient

DATA_DIR = Path("data")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_row(m: dict, close_horizon_s: int) -> dict | None:
    """Reduce a raw Kalshi market to the fields the arena needs."""
    yes_bid = int(m.get("yes_bid") or 0)
    yes_ask = int(m.get("yes_ask") or 0)
    if yes_bid <= 0 or yes_ask <= 0 or yes_ask <= yes_bid:
        return None  # one-sided or crossed: nothing to trade against
    close = m.get("close_time")
    ticks_to_close = 1
    if close:
        try:
            close_dt = datetime.fromisoformat(close.replace("Z", "+00:00"))
            secs = (close_dt - datetime.now(timezone.utc)).total_seconds()
            ticks_to_close = max(0, int(secs // max(1, close_horizon_s)))
        except ValueError:
            pass
    return {
        "ticker": m.get("ticker", ""),
        "event_ticker": m.get("event_ticker", ""),
        "series": (m.get("ticker", "").split("-")[0]),
        "title": (m.get("title") or "")[:120],
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_bid_size": int(m.get("yes_bid_size") or 0),
        "yes_ask_size": int(m.get("yes_ask_size") or 0),
        "volume": int(m.get("volume") or 0),
        "open_interest": int(m.get("open_interest") or 0),
        "ticks_to_close": ticks_to_close,
        "close_time": close,
    }


def record(
    minutes: int = 60,
    every: int = 60,
    max_close_hours: int = 48,
    out: Path | None = None,
    series: list | None = None,
    max_pages: int = 6,
) -> Path:
    """Poll open markets and append a snapshot line per poll."""
    client = KalshiPublicClient()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = out or DATA_DIR / f"snapshots-{_today()}.jsonl"
    deadline = time.time() + minutes * 60
    polls = 0

    while time.time() < deadline:
        max_close_ts = int(time.time()) + max_close_hours * 3600
        rows = []
        sources = series or [None]
        for s in sources:
            for m in client.iter_markets(
                status="open", series_ticker=s, max_close_ts=max_close_ts, max_pages=max_pages
            ):
                row = snapshot_row(m, close_horizon_s=every)
                if row:
                    rows.append(row)

        payload = {"ts": _iso(), "poll": polls, "snapshots": rows}
        with out.open("a") as fh:
            fh.write(json.dumps(payload) + "\n")
        polls += 1
        print(f"[record] poll {polls}: {len(rows)} quoted markets -> {out}")

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(every, max(1, remaining)))
    return out


def settle(snapshots: Path | None = None, out: Path | None = None, limit: int = 5000) -> Path:
    """Join recorded tickers to their settled outcomes, once they exist."""
    client = KalshiPublicClient()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = snapshots or DATA_DIR / f"snapshots-{_today()}.jsonl"
    out = out or DATA_DIR / f"settlements-{_today()}.jsonl"
    if not snapshots.exists():
        raise SystemExit(f"no snapshot file at {snapshots} -- run `record` first")

    tickers: dict = {}
    last_tick: dict = {}
    for i, line in enumerate(snapshots.read_text().splitlines()):
        if not line.strip():
            continue
        payload = json.loads(line)
        for row in payload.get("snapshots", []):
            tickers[row["ticker"]] = row
            last_tick[row["ticker"]] = i

    done = 0
    pending = 0
    with out.open("w") as fh:
        for ticker in list(tickers)[:limit]:
            try:
                m = client.market(ticker)
            except Exception as e:  # noqa: BLE001 - one bad ticker must not stop the join
                print(f"[settle] {ticker}: {e}")
                continue
            result = str(m.get("result", "")).lower()
            status = str(m.get("status", "")).lower()
            if result not in ("yes", "no") or status not in ("settled", "finalized", "closed"):
                pending += 1
                continue
            fh.write(
                json.dumps(
                    {
                        "ticker": ticker,
                        "outcome": 1 if result == "yes" else 0,
                        "status": status,
                        "resolved_at_tick": last_tick[ticker],
                        "final_price": None,
                        "settled_ts": _iso(),
                    }
                )
                + "\n"
            )
            done += 1
    print(f"[settle] {done} resolved, {pending} still open -> {out}")
    print("[settle] re-run later to pick up the ones still open.")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Record real Kalshi books, join settlements later.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="poll open markets and append snapshots")
    r.add_argument("--minutes", type=int, default=60)
    r.add_argument("--every", type=int, default=60, help="seconds between polls")
    r.add_argument("--max-close-hours", type=int, default=48)
    r.add_argument("--series", default="", help="comma-separated series tickers, blank = all")
    r.add_argument("--out", default="")

    s = sub.add_parser("settle", help="look up outcomes for recorded tickers")
    s.add_argument("--snapshots", default="")
    s.add_argument("--out", default="")

    args = ap.parse_args(argv)
    if args.cmd == "record":
        record(
            minutes=args.minutes,
            every=args.every,
            max_close_hours=args.max_close_hours,
            out=Path(args.out) if args.out else None,
            series=[x.strip() for x in args.series.split(",") if x.strip()] or None,
        )
    else:
        settle(
            snapshots=Path(args.snapshots) if args.snapshots else None,
            out=Path(args.out) if args.out else None,
        )


if __name__ == "__main__":
    main()
