"""Standalone data via Yahoo Finance (optional dependency: yfinance).

This lets you run the screener yourself without any broker API keys. It is
intentionally an *optional* import so the core engine and tests stay
dependency-free. Install with ``pip install yfinance``.

Sentiment is left at 0.0 here — price/volume can't tell you the news. Fill it
in from your own research (or via the JSON provider) before relying on it.
"""

from __future__ import annotations

import statistics
from datetime import date

from .base import Provider
from ..models import Snapshot


class YahooProvider(Provider):
    def __init__(self, lookback_days: int = 20, momentum_days: int = 5):
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "YahooProvider needs yfinance. Install: pip install yfinance"
            ) from exc
        self.lookback = lookback_days
        self.momentum_days = momentum_days

    def get_snapshot(self, symbol: str) -> Snapshot:
        import yfinance as yf

        hist = yf.Ticker(symbol).history(period=f"{self.lookback + 10}d")
        if hist.empty:
            raise ValueError("no history returned")
        closes = list(hist["Close"].dropna())
        vols = list(hist["Volume"].dropna())
        closes = closes[-(self.lookback + 1):]
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]

        price = float(closes[-1])
        vol = float(statistics.pstdev(rets)) if len(rets) > 1 else 0.0
        dollar_vol = (
            float(statistics.mean(c * v for c, v in
                                  zip(closes[-self.lookback:], vols[-self.lookback:])))
            if vols else 0.0
        )
        mdays = min(self.momentum_days, len(closes) - 1)
        momentum = (closes[-1] / closes[-1 - mdays] - 1.0) if mdays > 0 else 0.0

        return Snapshot(
            symbol=symbol.upper(),
            price=round(price, 4),
            avg_dollar_volume=round(dollar_vol, 2),
            volatility=round(vol, 5),
            momentum=round(momentum, 5),
            sentiment=0.0,
            as_of=date.today().isoformat(),
            notes="yahoo: sentiment unset; add news research",
        )
