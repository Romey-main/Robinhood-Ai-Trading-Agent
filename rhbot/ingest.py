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


def _col_index(ref: str) -> int:
    """Spreadsheet cell ref ('AB12') -> 0-based column index."""
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _read_xlsx_rows(path: str) -> list:
    """Read the first worksheet of an .xlsx into rows of cell strings.

    Honors each cell's column reference so omitted/empty cells don't shift
    columns, and resolves shared strings. Std-lib only (zipfile + ElementTree).
    """
    import zipfile
    import xml.etree.ElementTree as ET

    M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sroot.findall(f"{M}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{M}t")))
        sheets = sorted(n for n in z.namelist()
                        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        rows = []
        if sheets:
            root = ET.fromstring(z.read(sheets[0]))
            data = root.find(f"{M}sheetData")
            for row in (data.findall(f"{M}row") if data is not None else []):
                by_col, maxc = {}, -1
                for i, c in enumerate(row.findall(f"{M}c")):
                    ci = _col_index(c.get("r", "")) if c.get("r") else i
                    v = c.find(f"{M}v")
                    if c.get("t") == "s":
                        idx = int(v.text) if v is not None and v.text else -1
                        val = shared[idx] if 0 <= idx < len(shared) else ""
                    elif c.get("t") == "inlineStr":
                        is_el = c.find(f"{M}is")
                        val = "".join(x.text or "" for x in is_el.iter()) if is_el is not None else ""
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    by_col[ci] = val
                    maxc = max(maxc, ci)
                rows.append([by_col.get(i, "") for i in range(maxc + 1)])
    return rows


def _rows_from_file(path: str) -> list:
    """Rows from a holdings file, auto-detecting Excel (.xlsx) vs CSV by magic."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == b"PK":                      # .xlsx is a zip
        return _read_xlsx_rows(path)
    if magic == b"\xd0\xcf":                # legacy .xls (OLE2) — unsupported
        raise ValueError(
            "this is an old-format .xls; open it and 'Save As' .xlsx or .csv first")
    with open(path, newline="") as fh:
        return list(csv.reader(fh))


def membership_from_holdings_dir(
    directory: str, ticker_col: str = "Ticker", glob_pat: str = "*.csv,*.xlsx",
    date_re: str = r"(\d{4}-\d{2}-\d{2})",
) -> dict:
    """One dated holdings file per snapshot; date parsed from the filename.

    Tolerant of vendor preamble rows (e.g. iShares puts ~9 metadata lines above
    the real header): we scan for the first row containing ``ticker_col``.
    """
    files: list = []
    for pat in glob_pat.split(","):
        files.extend(glob.glob(os.path.join(directory, pat.strip())))
    snaps: dict = {}
    for fp in sorted(set(files)):
        m = re.search(date_re, os.path.basename(fp))
        if not m:
            continue
        rows = _rows_from_file(fp)
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
    """Constituents from a single vendor holdings file: .csv or .xlsx."""
    rows = _rows_from_file(path)
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


# --- vendor price clients (one PriceSource interface: fetch(symbol, start)) ---

def _http_get(url: str, headers: dict | None = None) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "rhbot"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def tiingo_fetch(symbol: str, start: str, token: str | None = None, http_get=None):
    """Tiingo daily adjusted closes (split+dividend adjusted). Free tier + key.

    Set TIINGO_API_KEY or pass token. This is the recommended *paid-grade*
    source: adjClose is properly adjusted, unlike most free feeds.
    """
    import json as _json
    import os as _os
    token = token or _os.environ.get("TIINGO_API_KEY")
    if not token:
        raise RuntimeError("Tiingo needs a token: set TIINGO_API_KEY (free at tiingo.com)")
    http_get = http_get or _http_get
    url = (f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
           f"?startDate={start}&token={token}&columns=date,adjClose")
    out = []
    for row in _json.loads(http_get(url, {"Content-Type": "application/json"})):
        px = row.get("adjClose")
        if px is not None:
            out.append((row["date"][:10], float(px)))
    return out


def stooq_fetch(symbol: str, start: str, http_get=None):
    """Stooq daily CSV (free, no key). Note: split-adjusted, NOT dividend-adjusted."""
    import io
    http_get = http_get or _http_get
    text = http_get(f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        d, px = r.get("Date", ""), r.get("Close")
        if d >= start and px not in (None, "", "N/D"):
            out.append((d, float(px)))
    return out


PRICE_SOURCES = {"yahoo": _yahoo_fetch, "tiingo": tiingo_fetch, "stooq": stooq_fetch}


# --- membership reconciliation -----------------------------------------------

def load_membership(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


def two_latest_dates(snaps: dict):
    ds = sorted(snaps)
    if len(ds) >= 2:
        return ds[-2], ds[-1]
    return (None, ds[-1] if ds else None)


def diff_snapshots(snaps: dict, date_a: str, date_b: str) -> dict:
    """Additions/removals between two dated snapshots — auditable index changes."""
    a = set(snaps.get(date_a, []))
    b = set(snaps.get(date_b, []))
    return {
        "from": date_a, "to": date_b,
        "added": sorted(b - a), "removed": sorted(a - b),
        "unchanged": len(a & b),
    }
