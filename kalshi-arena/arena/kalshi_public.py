"""
Read-only Kalshi market-data client.

Deliberately read-only. There is no order-placement path anywhere in this
package -- not disabled by a flag, not gated behind an env var, simply absent.
A competition harness that can also send live orders is one config mistake
away from an expensive afternoon, and the arena does not need the capability
to do its job.

Standard library only (urllib), so the arena has no install step. The one
thing it does carefully is rate limiting: the incumbent bot's comments record
a 429 storm from eight concurrent workers and a retreat to three, which is
the symptom of retrying without a limiter. A token bucket in front of every
request is the fix, and it belongs in the client rather than in each caller.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE = "https://api.elections.kalshi.com/trade-api/v2"


class RateLimiter:
    """Token bucket. Blocks the caller rather than letting it get a 429."""

    def __init__(self, rate_per_sec: float = 5.0, burst: int = 10):
        self.rate = rate_per_sec
        self.burst = burst
        self.tokens = float(burst)
        self.last = time.monotonic()

    def acquire(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1.0:
            time.sleep((1.0 - self.tokens) / self.rate)
            self.tokens = 0.0
            self.last = time.monotonic()
        else:
            self.tokens -= 1.0


@dataclass
class KalshiPublicClient:
    base_url: str = DEFAULT_BASE
    timeout: float = 20.0
    max_retries: int = 4
    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(5.0, 10))
    user_agent: str = "kalshi-arena/1.0 (read-only market data)"

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url.rstrip("/") + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url += "?" + urllib.parse.urlencode(clean)

        last_err = None
        for attempt in range(self.max_retries):
            self.limiter.acquire()
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(2.0**attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ssl.SSLError) as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    time.sleep(2.0**attempt)
                    continue
                raise
        raise RuntimeError(f"GET {path} failed after {self.max_retries} attempts: {last_err}")

    # -- normalisation ----------------------------------------------------
    # Kalshi migrated integer-cent fields to dollar strings
    # ("yes_bid_dollars": "0.6100"). Convert once, here, so nothing
    # downstream has to know or care which shape the API returned.

    _PRICE_FIELDS = (
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "last_price",
        "previous_price",
    )
    _FP_FIELDS = {
        "volume": "volume_fp",
        "volume_24h": "volume_24h_fp",
        "open_interest": "open_interest_fp",
        "yes_bid_size": "yes_bid_size_fp",
        "yes_ask_size": "yes_ask_size_fp",
    }

    @classmethod
    def normalize(cls, m: dict) -> dict:
        out = dict(m)
        for f in cls._PRICE_FIELDS:
            if out.get(f) in (None, 0):
                d = out.get(f + "_dollars")
                if d not in (None, ""):
                    try:
                        out[f] = int(round(float(d) * 100))
                    except (TypeError, ValueError):
                        pass
        for tgt, src in cls._FP_FIELDS.items():
            if out.get(tgt) in (None, 0):
                v = out.get(src)
                if v not in (None, ""):
                    try:
                        out[tgt] = int(round(float(v)))
                    except (TypeError, ValueError):
                        pass
        return out

    # -- endpoints --------------------------------------------------------

    def markets(
        self,
        status: str = "open",
        limit: int = 200,
        series_ticker: str | None = None,
        max_close_ts: int | None = None,
        cursor: str | None = None,
    ) -> tuple:
        data = self._get(
            "/markets",
            {
                "status": status,
                "limit": limit,
                "series_ticker": series_ticker,
                "max_close_ts": max_close_ts,
                "cursor": cursor,
            },
        )
        return [self.normalize(m) for m in data.get("markets", [])], data.get("cursor")

    def iter_markets(
        self,
        status: str = "open",
        series_ticker: str | None = None,
        max_close_ts: int | None = None,
        max_pages: int = 10,
        limit: int = 200,
    ):
        cursor = None
        for _ in range(max_pages):
            page, cursor = self.markets(
                status=status,
                limit=limit,
                series_ticker=series_ticker,
                max_close_ts=max_close_ts,
                cursor=cursor,
            )
            if not page:
                return
            yield from page
            if not cursor:
                return

    def market(self, ticker: str) -> dict:
        data = self._get(f"/markets/{ticker}")
        return self.normalize(data.get("market", data))
