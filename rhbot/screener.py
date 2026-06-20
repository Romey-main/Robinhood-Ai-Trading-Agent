"""Rank candidates by a transparent, tunable composite score.

Every factor is normalized to [0, 1] and combined with the weights from the
config. The scoring is intentionally simple and inspectable — you should be
able to read *why* a name ranked where it did, because a black-box score you
can't audit is how you end up bag-holding a pump.
"""

from __future__ import annotations

import math
from typing import Iterable

from .config import Config
from .models import Snapshot, ScoredCandidate


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def volatility_score(vol: float, cfg: Config) -> float:
    """Reward movement up to a sane ceiling; 0 below the floor."""
    if vol <= cfg.min_volatility:
        return 0.0
    span = max(cfg.max_volatility - cfg.min_volatility, 1e-9)
    return _clamp((vol - cfg.min_volatility) / span)


def liquidity_score(dollar_vol: float, cfg: Config) -> float:
    """Log-scaled from the floor ($20M) up to ~$2B (very liquid)."""
    if dollar_vol <= cfg.min_avg_dollar_volume:
        return 0.0
    lo = math.log10(cfg.min_avg_dollar_volume)
    hi = math.log10(2_000_000_000.0)
    return _clamp((math.log10(dollar_vol) - lo) / (hi - lo))


def momentum_score(mom: float, cfg: Config) -> float:
    """Tent function: reward strength, penalize blow-off / chasing.

    <=0 returns scale 0..0.5; 0..ideal scales 0.5..1.0; past ``ideal`` the
    score decays back down so a parabolic +30% day is *not* rewarded.
    """
    ideal = cfg.ideal_momentum
    over = cfg.overextended_momentum
    if mom <= 0:
        # -over -> 0.0, 0 -> 0.5
        return _clamp(0.5 + (mom / (2 * over)))
    if mom <= ideal:
        return _clamp(0.5 + 0.5 * (mom / max(ideal, 1e-9)))
    if mom <= over:
        # ideal -> 1.0, over -> 0.3
        frac = (mom - ideal) / max(over - ideal, 1e-9)
        return _clamp(1.0 - 0.7 * frac)
    return 0.2  # past overextended: clearly chasing


def sentiment_score(sent: float) -> float:
    return _clamp((sent + 1.0) / 2.0)


def _weights(cfg: Config) -> dict:
    raw = {
        "volatility": cfg.w_volatility,
        "liquidity": cfg.w_liquidity,
        "momentum": cfg.w_momentum,
        "sentiment": cfg.w_sentiment,
    }
    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


def _filter(snap: Snapshot, cfg: Config) -> list:
    reasons = []
    if snap.price < cfg.min_price:
        reasons.append(f"price ${snap.price:.2f} < min ${cfg.min_price:.2f}")
    if snap.price > cfg.max_price:
        reasons.append(f"price ${snap.price:.2f} > max ${cfg.max_price:.2f}")
    if snap.avg_dollar_volume < cfg.min_avg_dollar_volume:
        reasons.append(
            f"liquidity ${snap.avg_dollar_volume/1e6:.1f}M/day "
            f"< min ${cfg.min_avg_dollar_volume/1e6:.0f}M"
        )
    if snap.volatility < cfg.min_volatility:
        reasons.append(
            f"vol {snap.volatility*100:.1f}% < floor {cfg.min_volatility*100:.0f}% "
            f"(too quiet)"
        )
    if snap.volatility > cfg.max_volatility:
        reasons.append(
            f"vol {snap.volatility*100:.1f}% > ceiling {cfg.max_volatility*100:.0f}% "
            f"(halt/pump risk)"
        )
    return reasons


def score_one(snap: Snapshot, cfg: Config) -> ScoredCandidate:
    w = _weights(cfg)
    comp = {
        "volatility": volatility_score(snap.volatility, cfg),
        "liquidity": liquidity_score(snap.avg_dollar_volume, cfg),
        "momentum": momentum_score(snap.momentum, cfg),
        "sentiment": sentiment_score(snap.sentiment),
    }
    composite = sum(comp[k] * w[k] for k in comp) * 100.0

    reject = _filter(snap, cfg)
    reasons = [
        f"vol {snap.volatility*100:.1f}% (score {comp['volatility']:.2f})",
        f"liq ${snap.avg_dollar_volume/1e6:.0f}M/d (score {comp['liquidity']:.2f})",
        f"mom {snap.momentum*100:+.1f}% (score {comp['momentum']:.2f})",
        f"sentiment {snap.sentiment:+.2f} from {snap.news_count} headlines",
    ]
    return ScoredCandidate(
        symbol=snap.symbol,
        score=round(composite, 1),
        components={k: round(v, 3) for k, v in comp.items()},
        reasons=reasons,
        snapshot=snap,
        passed_filters=(len(reject) == 0),
        reject_reasons=reject,
    )


def screen(snaps: Iterable[Snapshot], cfg: Config, top: int = 5):
    """Return (ranked_passers, rejected). Passers are sorted best-first."""
    scored = [score_one(s, cfg) for s in snaps]
    passers = [c for c in scored if c.passed_filters]
    rejected = [c for c in scored if not c.passed_filters]
    passers.sort(key=lambda c: c.score, reverse=True)
    return passers[:top], rejected
