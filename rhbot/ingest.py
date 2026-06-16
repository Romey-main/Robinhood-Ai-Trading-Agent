"""Bring-your-own-data adapters.

These convert whatever a data source hands you into the two formats the engine
consumes:
  * a price PANEL CSV  -> date,symbol,adj_close   (Panel.from_csv)
  * a MEMBERSHIP JSON  -> {date: [tickers]}        (Membership.from_json)

The point of keeping these generic is that *any* vendor works: iShares holdings
exports, a CRSP/Sharadar/Norgate dump, or a CSV you maintain by hand. Column
names are parameters, so you map your file's headers to ours without editing
code. Std-lib only.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re

_NON_TICKERS = {"CASH", "USD", "-", "", "USD CASH"}
_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")


def _looks_like_ticker(t: str) -> bool:
    t = t.strip().upper()
    return t not in _NON_TICKERS and bool(_TICKER_RE.match(t))


def membership_from_holdings_dir(
    directory: str, ticker_col: str = "Ticker", glob_pat: str = "*.csv",
    date_re: str = r"(\d{4}-\d{2}-\d{2})",
) -> dict:
    """One dated holdings file per snapshot; date parsed from the filename.

    Tolerant of vendor preamble rows (e.g. iShares puts ~9 metadata lines above
    the real header): we scan for the first row containing ``ticker_col``.
    """
    snaps: dict = {}
    for fp in sorted(glob.glob(os.path.join(directory, glob_pat))):
        m = re.search(date_re, os.path.basename(fp))
        if not m:
            continue
        with open(fp, newline="") as fh:
            rows = list(csv.reader(fh))
        hdr = next((i for i, r in enumerate(rows) if ticker_col in r), None)
        if hdr is None:
            continue
        col = rows[hdr].index(ticker_col)
        tickers = {r[col].strip().upper() for r in rows[hdr + 1:]
                   if len(r) > col and _looks_like_ticker(r[col])}
        if tickers:
            snaps[m.group(1)] = tickers
    return snaps


def membership_from_long_csv(path: str, date_col: str = "date",
                             ticker_col: str = "ticker") -> dict:
    """One long CSV with a date column and a ticker column."""
    snaps: dict = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            d, t = r[date_col].strip(), r[ticker_col].strip().upper()
            if d and _looks_like_ticker(t):
                snaps.setdefault(d, set()).add(t)
    return snaps


def write_membership_json(snaps: dict, out: str) -> int:
    payload = {d: sorted(v) for d, v in sorted(snaps.items())}
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    return len(payload)


def panel_from_long_csv(path: str, out: str, date_col: str = "date",
                        symbol_col: str = "symbol",
                        price_col: str = "adj_close") -> int:
    """Remap an arbitrary long price CSV to the engine's panel format."""
    n = 0
    with open(path, newline="") as fin, open(out, "w", newline="") as fout:
        w = csv.writer(fout)
        w.writerow(["date", "symbol", "adj_close"])
        for r in csv.DictReader(fin):
            d, s, p = r[date_col].strip(), r[symbol_col].strip().upper(), r[price_col].strip()
            if d and s and p != "":
                w.writerow([d, s, p])
                n += 1
    return n


def tickers_from_holdings_file(path: str, ticker_col: str = "Ticker") -> set:
    """Constituents from a single vendor holdings file (e.g. an iShares export)."""
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    hdr = next((i for i, r in enumerate(rows) if ticker_col in r), None)
    if hdr is None:
        return set()
    col = rows[hdr].index(ticker_col)
    return {r[col].strip().upper() for r in rows[hdr + 1:]
            if len(r) > col and _looks_like_ticker(r[col])}


def append_membership_snapshot(path: str, date: str, tickers) -> int:
    """Record one dated constituent snapshot, building point-in-time history.

    This is the *free* path to an eventually bias-free backtest: run it on a
    schedule and you accumulate genuine point-in-time membership going forward,
    no paid vendor required (it just takes time to build history).
    """
    snaps: dict = {}
    if os.path.exists(path):
        with open(path) as fh:
            snaps = json.load(fh)
    snaps[date] = sorted({str(t).upper() for t in tickers if _looks_like_ticker(str(t))})
    with open(path, "w") as fh:
        json.dump({d: snaps[d] for d in sorted(snaps)}, fh, indent=2)
    return len(snaps)


def _yahoo_fetch(symbol: str, start: str):
    """Default price fetcher for build_panel_csv (optional dep: yfinance)."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ImportError("build-panel-yahoo needs yfinance: pip install yfinance") from exc
    h = yf.Ticker(symbol).history(start=start, auto_adjust=True)
    return [(idx.date().isoformat(), float(row["Close"]))
            for idx, row in h.iterrows() if row["Close"] == row["Close"]]


def build_panel_csv(symbols, start: str, out: str, fetch=None) -> int:
    """Build a panel CSV by fetching adjusted closes per symbol.

    ``fetch(symbol, start) -> [(iso_date, adj_close)]`` is injectable so the
    transform is testable offline; the default uses Yahoo Finance.
    """
    fetch = fetch or _yahoo_fetch
    rows = []
    for s in symbols:
        try:
            for d, px in fetch(s, start):
                rows.append((d, s.upper(), px))
        except Exception as exc:  # noqa: BLE001 - skip bad symbols, keep going
            print(f"[warn] {s}: {exc}")
    rows.sort()
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "symbol", "adj_close"])
        w.writerows(rows)
    return len(rows)
