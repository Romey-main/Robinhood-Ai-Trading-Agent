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
