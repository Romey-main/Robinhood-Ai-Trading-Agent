"""Helpers to build synthetic panels for tests (no network, deterministic)."""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rhbot.panel import Panel


def business_dates(n, start=date(2025, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def panel_from_paths(paths, dates):
    """paths: {symbol: [price aligned to dates]}"""
    prices = {sym: {dates[i]: vals[i] for i in range(len(vals))}
              for sym, vals in paths.items()}
    return Panel(prices, list(dates))


def ramp(n, start, end, flat_until=0):
    """Flat at `start` for `flat_until` points, then linear to `end`."""
    out = []
    for i in range(n):
        if i < flat_until:
            out.append(float(start))
        else:
            t = (i - flat_until) / max(1, (n - 1 - flat_until))
            out.append(float(start) + (float(end) - float(start)) * t)
    return out


def geometric(n, start, daily):
    out = [float(start)]
    for _ in range(n - 1):
        out.append(out[-1] * (1.0 + daily))
    return out
