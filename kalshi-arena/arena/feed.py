"""
Market data feeds.

Three of them, for three different jobs:

  SyntheticFeed -- a generative model with known ground truth. This is the
      only way to answer "is this bot's edge real or is it luck", because it
      is the only setting where the true probability is knowable. It runs
      offline, deterministically, in milliseconds.

  ReplayFeed -- replays real Kalshi books recorded by `harvest.py`. Real
      spreads, real liquidity, real market microstructure.

  The recorder itself lives in `harvest.py`, not here.

On why the competition is scored on synthetic data first: real settled Kalshi
markets look like a gold mine (thousands of resolved binaries with results)
and are a trap. Their stored prices are post-resolution -- a market that
settled YES shows a last price of 0.99 -- so any backtest against them
"predicts" outcomes it was handed. The measured skill is entirely leakage.
The only unbiased real-data path is to record books *now* and join the
settlements *later*, which is what `harvest.py` does and why real-data
results accrue over calendar time rather than appearing on demand.

The synthetic generator is built to make the two regimes that matter
distinguishable:

  "efficient"   -- the displayed price already equals the fair probability.
                   No edge exists. The optimal strategy is to not trade, and
                   any bot that trades anyway pays fees for the privilege.
                   This is the regime that catches overtrading.

  "inefficient" -- the price carries a persistent bias and the private signal
                   sees through it. Real edge exists and a good bot should
                   find it. This is the regime that catches timidity.

Most real venues are mostly the first with pockets of the second, which is
what "mixed" produces.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from .calibration import clamp01, logit, sigmoid
from .types import MarketSnapshot, Resolution


@dataclass
class Tick:
    """One instant of the world, as handed to every bot identically."""

    t: int
    snapshots: dict  # ticker -> MarketSnapshot (public view, no ground truth)
    signals: dict  # ticker -> float, the shared private-model estimate
    resolutions: list  # markets that resolved at the end of this tick


@dataclass
class SyntheticConfig:
    n_markets: int = 120
    n_ticks: int = 60
    regime: str = "mixed"  # efficient | inefficient | mixed
    inefficient_fraction: float = 0.35
    # Standard deviation, in log-odds, of the market's persistent pricing bias
    # on an inefficient market. 0.6 logits at a 50c price is roughly 14c.
    bias_sd: float = 0.6
    # Noise on the private signal, in log-odds. Lower = sharper model.
    signal_noise_sd: float = 0.55
    # Per-tick volatility of the latent fair value, in log-odds.
    fair_vol: float = 0.18
    spread_choices: tuple = (1, 1, 2, 2, 2, 3, 3, 4, 6)
    size_lambda: float = 40.0
    volume_lambda: float = 300.0
    n_clusters: int = 20
    # Fraction of a market's life that has elapsed before it can resolve.
    horizon_jitter: float = 0.4


class SyntheticFeed:
    """Deterministic generative feed with ground truth.

    The latent fair value is a martingale in log-odds space, so the price
    process has the defining property of a real prediction market: today's
    price is the best estimate of tomorrow's. Outcomes are then drawn from
    the terminal fair value, which makes the price path honest -- there is no
    way to beat it except by knowing something it does not.
    """

    def __init__(self, cfg: SyntheticConfig | None = None, seed: int = 0):
        self.cfg = cfg or SyntheticConfig()
        self.rng = random.Random(seed)
        self.markets = []
        self._build()

    def _build(self) -> None:
        cfg = self.cfg
        rng = self.rng
        for i in range(cfg.n_markets):
            ticker = f"SIM-{i:04d}"
            cluster = f"EVT-{i % cfg.n_clusters:02d}"
            series = f"SERIES-{i % 5}"

            # Starting fair value: a spread-out prior with mass near the
            # extremes, which is what a real venue's price distribution looks
            # like (most markets are lopsided, few are coin flips).
            q0 = clamp01(rng.betavariate(0.9, 0.9), 0.02)

            # Depth: 0 = thin and wide, 1 = deep and tight. Mispricing is
            # tied to it rather than sprinkled at random, because that is
            # how real venues behave -- a heavily traded market with a 1c
            # spread has been argued over by people with money, and a market
            # nobody has looked at has not. Assigning inefficiency
            # independently of liquidity would make it unlearnable by
            # construction and quietly punish any bot smart enough to look
            # for it in the right place.
            depth = rng.random()
            ineff_odds = cfg.inefficient_fraction * (1.8 - 1.6 * depth)

            if cfg.regime == "efficient":
                inefficient = False
            elif cfg.regime == "inefficient":
                inefficient = True
            else:
                inefficient = rng.random() < min(0.95, ineff_odds)
            # Thin markets are not merely more often wrong, they are wronger.
            bias_scale = cfg.bias_sd * (0.55 + 0.9 * (1.0 - depth))
            bias = rng.gauss(0.0, bias_scale) if inefficient else 0.0

            life = max(4, int(cfg.n_ticks * (1.0 - rng.random() * cfg.horizon_jitter)))

            # Latent fair path: a log-odds random walk whose variance shrinks
            # as the market approaches close (information resolves).
            q = [q0]
            for t in range(1, life + 1):
                remaining = (life - t + 1) / life
                step = rng.gauss(0.0, cfg.fair_vol * math.sqrt(max(0.05, remaining)))
                q.append(clamp01(sigmoid(logit(q[-1]) + step), 0.005))

            outcome = 1 if rng.random() < q[-1] else 0

            # Spread, size and volume all follow from depth, so the book a
            # bot can see is genuinely informative about how much it should
            # trust the price.
            spread_mu = 1.0 + (1.0 - depth) * 5.0
            spreads = [
                max(1, min(9, int(round(rng.gauss(spread_mu, 0.8))))) for _ in range(life + 1)
            ]
            size_mu = cfg.size_lambda * (0.15 + 1.9 * depth)
            sizes = [
                (
                    1 + int(rng.expovariate(1.0 / size_mu)),
                    1 + int(rng.expovariate(1.0 / size_mu)),
                )
                for _ in range(life + 1)
            ]
            vol_mu = cfg.volume_lambda * (0.10 + 1.9 * depth)
            volumes = [int(rng.expovariate(1.0 / vol_mu)) for _ in range(life + 1)]
            sig_noise = [rng.gauss(0.0, cfg.signal_noise_sd) for _ in range(life + 1)]

            self.markets.append(
                {
                    "ticker": ticker,
                    "series": series,
                    "cluster": cluster,
                    "q": q,
                    "bias": bias,
                    "inefficient": inefficient,
                    "life": life,
                    "outcome": outcome,
                    "depth": depth,
                    "spreads": spreads,
                    "sizes": sizes,
                    "volumes": volumes,
                    "sig_noise": sig_noise,
                }
            )

    def _snapshot(self, m: dict, t: int) -> MarketSnapshot:
        q = m["q"][t]
        mid = clamp01(sigmoid(logit(q) + m["bias"]), 0.01)
        mid_cents = mid * 100.0
        spread = m["spreads"][t]
        bid = int(max(1, math.floor(mid_cents - spread / 2.0)))
        ask = int(min(99, bid + spread))
        if ask <= bid:
            ask = min(99, bid + 1)
        bsz, asz = m["sizes"][t]
        return MarketSnapshot(
            ticker=m["ticker"],
            series=m["series"],
            t=t,
            yes_bid=bid,
            yes_ask=ask,
            yes_bid_size=bsz,
            yes_ask_size=asz,
            volume=m["volumes"][t],
            ticks_to_close=m["life"] - t,
            p_true=q,
            outcome=m["outcome"],
            cluster=m["cluster"],
            title=m["ticker"],
        )

    def ticks(self):
        """Yield one Tick per time step until every market has resolved."""
        for t in range(self.cfg.n_ticks + 1):
            snaps = {}
            signals = {}
            resolutions = []
            for m in self.markets:
                if t < m["life"]:
                    snap = self._snapshot(m, t)
                    snaps[m["ticker"]] = snap
                    # Private signal: a noisy read of the *unbiased* fair
                    # value. On an efficient market this is pure noise around
                    # the price and carries no exploitable information.
                    signals[m["ticker"]] = clamp01(
                        sigmoid(logit(m["q"][t]) + m["sig_noise"][t]), 0.01
                    )
                elif t == m["life"]:
                    snaps[m["ticker"]] = self._snapshot(m, t)
                    resolutions.append(
                        Resolution(
                            ticker=m["ticker"],
                            outcome=m["outcome"],
                            p_true=m["q"][t],
                            final_market_price=self._snapshot(m, t).mid / 100.0,
                            cluster=m["cluster"],
                            series=m["series"],
                        )
                    )
            if not snaps:
                break
            yield Tick(t=t, snapshots=snaps, signals=signals, resolutions=resolutions)

    @property
    def truth(self) -> dict:
        """Ground truth for scoring. Never handed to a bot."""
        return {
            m["ticker"]: {
                "outcome": m["outcome"],
                "inefficient": m["inefficient"],
                "bias": m["bias"],
                "depth": m["depth"],
                "q_path": m["q"],
            }
            for m in self.markets
        }


class ReplayFeed:
    """Replays recorded real Kalshi books from a JSONL file.

    Expects the format written by `harvest.py record`: one JSON object per
    line with a `snapshots` list and an ISO timestamp. Resolutions come from
    a separate settlements file, joined by ticker, because they only become
    known later -- which is the whole point of recording forward.
    """

    def __init__(self, path: str | Path, settlements: str | Path | None = None,
                 signal_field: str = "signal"):
        self.path = Path(path)
        self.settlements = {}
        self.signal_field = signal_field
        if settlements and Path(settlements).exists():
            for line in Path(settlements).read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                self.settlements[row["ticker"]] = row

    def ticks(self):
        seen_resolved = set()
        t = 0
        with self.path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                payload = json.loads(line)
                snaps = {}
                signals = {}
                for row in payload.get("snapshots", []):
                    snap = MarketSnapshot(
                        ticker=row["ticker"],
                        series=row.get("series", ""),
                        t=t,
                        yes_bid=int(row["yes_bid"]),
                        yes_ask=int(row["yes_ask"]),
                        yes_bid_size=int(row.get("yes_bid_size", 0)),
                        yes_ask_size=int(row.get("yes_ask_size", 0)),
                        volume=int(row.get("volume", 0)),
                        ticks_to_close=int(row.get("ticks_to_close", 1)),
                        cluster=row.get("event_ticker", ""),
                        title=row.get("title", ""),
                    )
                    snaps[snap.ticker] = snap
                    if self.signal_field in row:
                        signals[snap.ticker] = float(row[self.signal_field])

                resolutions = []
                for ticker in snaps:
                    s = self.settlements.get(ticker)
                    if s and ticker not in seen_resolved and s.get("resolved_at_tick", -1) == t:
                        seen_resolved.add(ticker)
                        resolutions.append(
                            Resolution(
                                ticker=ticker,
                                outcome=int(s["outcome"]),
                                final_market_price=s.get("final_price"),
                                cluster=snaps[ticker].cluster,
                                series=snaps[ticker].series,
                            )
                        )
                yield Tick(t=t, snapshots=snaps, signals=signals, resolutions=resolutions)
                t += 1

        # Anything that settled but never got a resolution tick gets one at
        # the end, so positions are not left dangling.
        tail = [
            Resolution(ticker=tk, outcome=int(s["outcome"]), final_market_price=s.get("final_price"))
            for tk, s in self.settlements.items()
            if tk not in seen_resolved
        ]
        if tail:
            yield Tick(t=t, snapshots={}, signals={}, resolutions=tail)


def public_view(snap: MarketSnapshot) -> MarketSnapshot:
    """Strip ground truth before a snapshot reaches a bot.

    Enforced rather than documented: `p_true` and `outcome` live on the same
    object the simulator uses, and a bot that read them would post a perfect
    score. Making the tournament hand out stripped copies means no bot *can*
    cheat, accidentally or otherwise.
    """
    if snap.p_true is None and snap.outcome is None:
        return snap
    return MarketSnapshot(
        ticker=snap.ticker,
        series=snap.series,
        t=snap.t,
        yes_bid=snap.yes_bid,
        yes_ask=snap.yes_ask,
        yes_bid_size=snap.yes_bid_size,
        yes_ask_size=snap.yes_ask_size,
        volume=snap.volume,
        ticks_to_close=snap.ticks_to_close,
        p_true=None,
        outcome=None,
        cluster=snap.cluster,
        title=snap.title,
    )
