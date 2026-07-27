"""
Calibration: turning a model's number into a probability you can bet.

The incumbent bot feeds `p_est` from a calculator straight into Kelly. That is
the single most expensive unexamined assumption in the whole design, because
Kelly is *brutally* sensitive to overconfidence: staking as if you know p=0.75
when the truth is p=0.60 does not merely shrink your edge, it can make a
positive-edge strategy lose money outright.

The incumbent's only feedback loop is the performance ledger, which mutes a
whole category after 10+ settled trades with negative ROI. That is a very
coarse, very slow signal -- it needs real money and real losses to learn one
bit of information ("this category is bad").

The cheaper source of the same information is sitting in plain sight: **every
market that resolves teaches you something, whether or not you traded it.**
Observing 500 markets settle in a day gives orders of magnitude more
calibration signal than 10 settled trades, at zero risk. That is what this
module consumes.

Two pieces:

  ReliabilityCurve -- bins forecasts by predicted probability and tracks the
      realised frequency in each bin, with a Beta posterior so a bin with 3
      observations is shrunk hard toward the diagonal instead of pretending
      to know that "0.7 really means 0.2".

  Metrics -- Brier, log loss, ECE, and Brier *skill* against the market
      price. Skill-vs-market is the number that matters: beating a coin flip
      is trivial, beating the price you would have paid is the whole game.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional


def clamp01(p: float, eps: float = 1e-6) -> float:
    return min(1.0 - eps, max(eps, p))


def logit(p: float) -> float:
    p = clamp01(p)
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


@dataclass
class Bin:
    lo: float
    hi: float
    # Beta(alpha, beta) posterior over the true frequency in this bin.
    # The prior is centred on the bin midpoint, so an empty bin says
    # "predicted 0.7 means 0.7" and only moves off that with evidence.
    alpha: float = 0.0
    beta: float = 0.0
    n: int = 0

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2.0

    def posterior(self, prior_strength: float) -> float:
        a = self.alpha + prior_strength * self.mid
        b = self.beta + prior_strength * (1.0 - self.mid)
        return a / (a + b) if (a + b) > 0 else self.mid

    def posterior_sd(self, prior_strength: float) -> float:
        a = self.alpha + prior_strength * self.mid
        b = self.beta + prior_strength * (1.0 - self.mid)
        tot = a + b
        if tot <= 0:
            return 0.5
        return math.sqrt(a * b / (tot * tot * (tot + 1.0)))


@dataclass
class ReliabilityCurve:
    """Maps a raw forecast to a calibrated one, learned from resolutions.

    `prior_strength` is the number of pseudo-observations pinning each bin to
    the diagonal. Higher = more conservative = slower to trust a small sample.
    """

    n_bins: int = 10
    prior_strength: float = 25.0
    bins: list = field(default_factory=list)

    def __post_init__(self):
        if not self.bins:
            w = 1.0 / self.n_bins
            self.bins = [Bin(i * w, (i + 1) * w) for i in range(self.n_bins)]

    def _bin_for(self, p: float) -> Bin:
        idx = min(self.n_bins - 1, max(0, int(p * self.n_bins)))
        return self.bins[idx]

    def observe(self, p_pred: float, outcome: int, weight: float = 1.0) -> None:
        b = self._bin_for(clamp01(p_pred))
        if outcome:
            b.alpha += weight
        else:
            b.beta += weight
        b.n += 1

    def observe_many(self, pairs: Iterable) -> None:
        for p, o in pairs:
            self.observe(p, o)

    def apply(self, p: float) -> float:
        """Calibrated probability, linearly interpolated between bin posteriors.

        Interpolation matters: a step function would make sizing jump
        discontinuously as a forecast drifts across a bin edge, and Kelly
        would happily double a position because a number moved by 0.001.
        """
        p = clamp01(p)
        pos = p * self.n_bins - 0.5
        i = int(math.floor(pos))
        frac = pos - i
        lo_i = min(self.n_bins - 1, max(0, i))
        hi_i = min(self.n_bins - 1, max(0, i + 1))
        lo = self.bins[lo_i].posterior(self.prior_strength)
        hi = self.bins[hi_i].posterior(self.prior_strength)
        if lo_i == hi_i:
            return clamp01(lo)
        return clamp01(lo + (hi - lo) * frac)

    def correct(self, p: float) -> float:
        """Apply the curve as a *bias correction*, preserving resolution.

        `apply` replaces a forecast with its bin's average, which is the
        textbook presentation and is wrong for this job. Binning throws away
        every distinction inside a bin: 0.71 and 0.79 both come back as
        whatever that bin resolved at, so a forecaster that had genuinely
        separated those two markets is stripped of the separation. Measured
        here, running an already-well-calibrated pooled forecast through
        `apply` turned +0.0050 Brier skill against the market into -0.0026 --
        the calibration step destroyed more information than the miscalibration
        it removed.

        The correction form keeps the forecast and moves it by only what the
        bin proved was wrong:

            logit(p_out) = logit(p_in) + [logit(posterior_b) - logit(mid_b)]

        An unobserved bin has posterior == midpoint, so the offset is zero
        and the forecast passes through untouched. Evidence of bias -- "when
        this bot says 0.7, it happens 0.6 of the time" -- shifts the whole
        bin without flattening it.
        """
        p = clamp01(p)
        pos = p * self.n_bins - 0.5
        i = int(math.floor(pos))
        frac = pos - i
        lo_i = min(self.n_bins - 1, max(0, i))
        hi_i = min(self.n_bins - 1, max(0, i + 1))

        def offset(b: Bin) -> float:
            return logit(clamp01(b.posterior(self.prior_strength))) - logit(clamp01(b.mid))

        off_lo = offset(self.bins[lo_i])
        off_hi = offset(self.bins[hi_i])
        off = off_lo if lo_i == hi_i else off_lo + (off_hi - off_lo) * frac
        return clamp01(sigmoid(logit(p) + off))

    def uncertainty(self, p: float) -> float:
        """Posterior SD of the calibrated estimate. Feeds Kelly shrinkage."""
        return self._bin_for(clamp01(p)).posterior_sd(self.prior_strength)

    @property
    def total_observations(self) -> int:
        return sum(b.n for b in self.bins)

    def merge(self, other: "ReliabilityCurve", weight: float = 1.0) -> None:
        """Pool another curve's evidence into this one.

        This is the mechanism by which two bots learn from each other's
        *forecasting* rather than only from their own: a bot that never
        looked at a market still gets to learn what happened there.
        """
        if other.n_bins != self.n_bins:
            raise ValueError("cannot merge curves with different binning")
        for mine, theirs in zip(self.bins, other.bins):
            mine.alpha += theirs.alpha * weight
            mine.beta += theirs.beta * weight
            mine.n += int(theirs.n * weight)

    def to_dict(self) -> dict:
        return {
            "n_bins": self.n_bins,
            "prior_strength": self.prior_strength,
            "bins": [
                {"lo": b.lo, "hi": b.hi, "alpha": b.alpha, "beta": b.beta, "n": b.n}
                for b in self.bins
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReliabilityCurve":
        c = cls(n_bins=d["n_bins"], prior_strength=d["prior_strength"], bins=[])
        c.bins = [Bin(b["lo"], b["hi"], b["alpha"], b["beta"], b["n"]) for b in d["bins"]]
        c.n_bins = len(c.bins)
        return c


@dataclass
class EdgeShrinkage:
    """Learns how much of a claimed edge is real, by measuring it.

    A bot screens hundreds of markets and trades the handful where it
    disagrees with the price most. Ranking on a noisy estimate does not
    select the best opportunities -- it selects the ones whose *errors*
    happened to point the same way as their edge. The winners of that
    competition are systematically overestimated. This is the optimizer's
    curse, and being a better forecaster does not fix it: measured in this
    arena, the challenger beat the incumbent on Brier score and still lost
    money, because every trade it picked was picked partly for being wrong
    in a flattering direction.

    The obvious correction is to shrink by `tau^2 / (tau^2 + sigma^2)` and
    estimate the two variances. That was tried here and it is fragile,
    because the `sigma` a calibration curve reports is the uncertainty of
    the *calibration mapping*, not the uncertainty of the gap -- so in an
    efficient market the estimator concluded there was plenty of real edge
    and the bot happily traded noise.

    Measuring the shrinkage directly is both simpler and correct. For every
    resolved market, record what the bot claimed the edge was and what the
    edge turned out to be:

        claimed  = (p_final - p_market) * 100
        realised = (outcome - p_market) * 100

    then fit the single slope through the origin that maps one to the other,
    ridged toward zero:

        k = sum(claimed * realised) / (sum(claimed^2) + lambda)

    k is the fraction of claimed edge that has historically been real. A
    perfectly honest forecaster gets k = 1. A forecaster whose disagreements
    are pure noise gets k = 0, its edges shrink to nothing, and it stops
    trading -- exactly the behaviour an efficient market should induce, now
    arrived at by measurement rather than by assumption. The ridge means an
    untested bot starts near zero and has to earn its way up.
    """

    sxx: float = 0.0
    sxy: float = 0.0
    syy: float = 0.0
    wn: float = 0.0  # decayed observation count
    n: int = 0
    # Prior standard deviation of k. This, not a raw ridge constant, is the
    # honest knob: it says how much slope a sceptic would entertain before
    # seeing data. The ridge itself is derived from it and from the measured
    # residual noise, so it stays correctly scaled as data accumulates.
    prior_k_sd: float = 0.35
    ridge: float = 400.0  # floor, used before any residual variance is known
    k_bounds: tuple = (0.0, 1.25)
    # Exponential forgetting, so a regime change is not outvoted forever by
    # ancient history.
    decay: float = 0.9995

    def observe(self, claimed_cents: float, realised_cents: float) -> None:
        self.sxx = self.sxx * self.decay + claimed_cents * claimed_cents
        self.sxy = self.sxy * self.decay + claimed_cents * realised_cents
        self.syy = self.syy * self.decay + realised_cents * realised_cents
        self.wn = self.wn * self.decay + 1.0
        self.n += 1

    @property
    def effective_ridge(self) -> float:
        """sigma^2 / prior_k_sd^2 -- the Bayesian ridge for a known-noise fit.

        A fixed ridge is wrong at scale, and wrong in the dangerous
        direction. Binary outcomes are enormously noisy relative to a
        forecast edge: `realised` has a standard deviation around 45 cents
        while `claimed` is a few cents, so the regression is fitting a
        whisper inside a shout. With a constant ridge of a few hundred, a
        single round of a couple of thousand observations swamps the prior
        and k is free to wander to 0.2 on noise alone -- which in an
        efficient market means the bot starts trading, confidently, on
        nothing. Scaling the ridge by the residual variance keeps the
        sceptic's prior worth a fixed amount of *evidence* rather than a
        fixed amount of *data*.
        """
        if self.wn < 2:
            return self.ridge
        resid_var = max(1.0, self.syy / self.wn)
        return max(self.ridge, resid_var / max(1e-6, self.prior_k_sd**2))

    @property
    def k(self) -> float:
        return self.k_toward(0.0)

    def k_toward(self, prior_k: float) -> float:
        """Slope, ridged toward `prior_k` instead of toward zero.

        This is what makes a per-segment hierarchy work. A freshly created
        segment has no evidence, so a ridge toward zero would give it k=0 and
        it would never trade -- and never gather the evidence that would let
        it. Ridging toward the pooled estimate instead means a new segment
        starts out behaving like the average and earns its own slope as data
        arrives, which is the standard partial-pooling answer.
        """
        r = self.effective_ridge
        raw = (self.sxy + r * prior_k) / (self.sxx + r)
        return min(self.k_bounds[1], max(self.k_bounds[0], raw))

    def shrink(self, claimed_cents: float, prior_k: float = 0.0) -> float:
        return claimed_cents * self.k_toward(prior_k)

    def to_dict(self) -> dict:
        return {"sxx": self.sxx, "sxy": self.sxy, "syy": self.syy, "wn": self.wn,
                "n": self.n, "ridge": self.ridge, "prior_k_sd": self.prior_k_sd,
                "decay": self.decay}

    @classmethod
    def from_dict(cls, d: dict) -> "EdgeShrinkage":
        return cls(
            sxx=d.get("sxx", 0.0), sxy=d.get("sxy", 0.0), syy=d.get("syy", 0.0),
            wn=d.get("wn", 0.0), n=d.get("n", 0),
            prior_k_sd=d.get("prior_k_sd", 0.35), ridge=d.get("ridge", 400.0),
            decay=d.get("decay", 0.9995),
        )

    def merge(self, other: "EdgeShrinkage", weight: float = 1.0) -> None:
        self.sxx += other.sxx * weight
        self.sxy += other.sxy * weight
        self.syy += other.syy * weight
        self.wn += other.wn * weight
        self.n += int(other.n * weight)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def brier(forecasts: Iterable) -> float:
    """Mean squared error of probabilistic forecasts. Lower is better."""
    n = 0
    tot = 0.0
    for p, o in forecasts:
        tot += (p - o) ** 2
        n += 1
    return tot / n if n else float("nan")


def log_loss(forecasts: Iterable) -> float:
    n = 0
    tot = 0.0
    for p, o in forecasts:
        p = clamp01(p)
        tot += -(o * math.log(p) + (1 - o) * math.log(1 - p))
        n += 1
    return tot / n if n else float("nan")


def brier_skill_vs_market(triples: Iterable) -> float:
    """Brier skill score against the market price as the reference forecast.

    (p_bot, p_market, outcome) -> 1 - BS_bot / BS_market

    Positive means the bot forecasts better than the price it would have
    traded against. Zero or negative means the bot has no informational edge
    and every cent it makes is execution or luck. This is the honest headline
    number for a prediction-market bot and the incumbent computes nothing
    like it.
    """
    num = 0.0
    den = 0.0
    n = 0
    for p_bot, p_mkt, o in triples:
        num += (p_bot - o) ** 2
        den += (p_mkt - o) ** 2
        n += 1
    if not n or den == 0:
        return float("nan")
    return 1.0 - num / den


def expected_calibration_error(forecasts: Iterable, n_bins: int = 10) -> float:
    """Average |predicted - realised| across bins, weighted by bin count."""
    buckets = [[0.0, 0.0, 0] for _ in range(n_bins)]
    total = 0
    for p, o in forecasts:
        i = min(n_bins - 1, max(0, int(clamp01(p) * n_bins)))
        buckets[i][0] += p
        buckets[i][1] += o
        buckets[i][2] += 1
        total += 1
    if not total:
        return float("nan")
    err = 0.0
    for sp, so, n in buckets:
        if n:
            err += (n / total) * abs(sp / n - so / n)
    return err


def reliability_table(forecasts: Iterable, n_bins: int = 10) -> list:
    """Rows of (lo, hi, n, mean_predicted, realised_frequency) for reporting."""
    buckets = [[0.0, 0.0, 0] for _ in range(n_bins)]
    for p, o in forecasts:
        i = min(n_bins - 1, max(0, int(clamp01(p) * n_bins)))
        buckets[i][0] += p
        buckets[i][1] += o
        buckets[i][2] += 1
    rows = []
    w = 1.0 / n_bins
    for i, (sp, so, n) in enumerate(buckets):
        rows.append(
            (
                i * w,
                (i + 1) * w,
                n,
                (sp / n) if n else None,
                (so / n) if n else None,
            )
        )
    return rows
