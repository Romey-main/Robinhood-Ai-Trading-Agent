"""
Blending a private signal with the market price.

The deepest flaw in the incumbent's design is philosophical, not numerical:
it treats the Kalshi price as *the thing to be beaten* and its own calculator
as *the truth*. So `p_est` comes from a sportsbook or a vol model, the market
price is subtracted from it, and whatever is left over is called "edge".

But the market price is itself a forecast -- usually a well-informed one,
produced by people with money at stake. Ignoring it throws away the single
best free signal available. The right question is never "what does my model
say", it is "what should I believe given my model *and* the price".

The answer is a **logarithmic opinion pool**: a weighted average in log-odds
space, with the weights constrained to sum to one.

    logit(p_pool) = s * [ (1 - a) * logit(p_market) + a * logit(p_signal) ] + b

The convexity constraint is not decoration, and getting it wrong is
expensive. An unconstrained fit -- two free coefficients on two highly
correlated features -- drifts to weights summing to more than one, and then
*even when the signal agrees exactly with the market* the blend returns a
more extreme probability than either input. At a 20c market price, weights
summing to 1.22 turn perfect agreement into a belief of 15.6c and a phantom
4.4c "edge". Every extreme market suddenly looks mispriced in the direction
of the favourite. That failure was not hypothetical here: the first version
of this file fit the weights freely and the bot lost $112 in one round
buying favourites at 80c and up, in a regime where it should have been
roughly flat.

With `a` constrained to [0, 1], agreement is a fixed point: if the signal
says exactly what the price says, the pool says it too. Edge can then only
come from genuine disagreement, which is the only place it can honestly come
from. `s` (sharpness) and `b` (bias) stay free so the pool can still learn
that prices in this universe are systematically over- or under-confident,
but both start at the calibrated identity and are pulled back toward it.

Three parameters, fit online from every resolution -- traded or not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .calibration import clamp01, logit, sigmoid


@dataclass
class PoolWeights:
    """a = weight on the private signal, s = sharpness, b = log-odds bias."""

    a: float = 0.30
    s: float = 1.0
    b: float = 0.0

    def to_dict(self) -> dict:
        return {"a": self.a, "s": self.s, "b": self.b}

    @classmethod
    def from_dict(cls, d: dict) -> "PoolWeights":
        return cls(d.get("a", 0.30), d.get("s", 1.0), d.get("b", 0.0))


@dataclass
class MarketPriorBlender:
    """Online-fitted logarithmic opinion pool of market price and signal.

    **The starting value and the shrinkage target are deliberately
    different, and that distinction is the whole design.** `a` *starts* at
    0.30 so the bot has an opinion, trades, and generates the evidence it
    needs to learn. But `l2` shrinks `a` toward **zero** -- toward "the
    signal is worthless, believe the price" -- because that is the correct
    prior for anyone claiming to beat a liquid market. Evidence can pull `a`
    up and keep it there; absence of evidence lets it decay to zero, and a
    bot with a=0 never trades.

    Shrinking toward the starting value instead is a subtle and costly error:
    it makes "my signal is worth 30%" unfalsifiable, so a bot in a perfectly
    efficient market keeps disagreeing with the price forever and pays fees
    for the privilege. Measured on the efficient regime here, that mistake
    cost roughly $22 per round against a correct answer of zero.

    `s` and `b` shrink toward the calibrated identity (s=1, b=0), so a
    hundred noisy examples cannot talk the pool into systematic
    overconfidence.
    """

    w: PoolWeights = field(default_factory=PoolWeights)
    lr: float = 0.05
    l2: float = 0.004
    lr_halflife: float = 400.0
    # Note the a=0.0: the regulariser's target, not the starting point.
    prior: PoolWeights = field(default_factory=lambda: PoolWeights(a=0.0, s=1.0, b=0.0))
    n_updates: int = 0
    a_bounds: tuple = (0.0, 0.75)
    s_bounds: tuple = (0.5, 1.3)
    b_bounds: tuple = (-1.0, 1.0)

    # -- inference --------------------------------------------------------

    def _z(self, p_market: float, p_signal: Optional[float]) -> tuple:
        lm = logit(clamp01(p_market))
        ls = logit(clamp01(p_signal)) if p_signal is not None else lm
        pooled = (1.0 - self.w.a) * lm + self.w.a * ls
        return self.w.s * pooled + self.w.b, lm, ls, pooled

    def blend(self, p_market: float, p_signal: Optional[float]) -> float:
        z, _lm, _ls, _pooled = self._z(p_market, p_signal)
        return clamp01(sigmoid(z))

    # -- fitting ----------------------------------------------------------

    def update(self, p_market: float, p_signal: Optional[float], outcome: int) -> None:
        """One SGD step on log loss for a single resolved market.

        The step size decays as 1/sqrt(1 + n/lr_halflife) -- Robbins-Monro.
        A constant step never converges: the iterate keeps bouncing around
        the optimum in proportion to the gradient noise, and with binary
        outcomes that noise is large. The wandering is not harmless, because
        every wander is a live trading parameter.
        """
        z, lm, ls, pooled = self._z(p_market, p_signal)
        err = sigmoid(z) - outcome  # d(logloss)/dz
        lr = self.lr / math.sqrt(1.0 + self.n_updates / self.lr_halflife)

        if p_signal is not None:
            d_a = self.w.s * (ls - lm)
            self.w.a -= lr * (err * d_a + self.l2 * (self.w.a - self.prior.a))
        self.w.s -= lr * (err * pooled + self.l2 * (self.w.s - self.prior.s))
        self.w.b -= lr * (err + self.l2 * (self.w.b - self.prior.b))

        self.w.a = min(self.a_bounds[1], max(self.a_bounds[0], self.w.a))
        self.w.s = min(self.s_bounds[1], max(self.s_bounds[0], self.w.s))
        self.w.b = min(self.b_bounds[1], max(self.b_bounds[0], self.w.b))
        self.n_updates += 1

    def update_many(self, rows: Iterable) -> None:
        for p_market, p_signal, outcome in rows:
            self.update(p_market, p_signal, outcome)

    def signal_trust(self) -> float:
        """0..1: how much of the belief the fit currently draws from the
        private signal. Falls toward zero when the signal proves worthless,
        which is how the bot stops trading instead of merely trading worse."""
        return max(0.0, min(1.0, self.w.a))

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "w": self.w.to_dict(),
            "lr": self.lr,
            "l2": self.l2,
            "lr_halflife": self.lr_halflife,
            "prior": self.prior.to_dict(),
            "n_updates": self.n_updates,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MarketPriorBlender":
        b = cls(
            w=PoolWeights.from_dict(d["w"]),
            lr=d.get("lr", 0.05),
            l2=d.get("l2", 0.004),
            lr_halflife=d.get("lr_halflife", 400.0),
            prior=PoolWeights.from_dict(d.get("prior", {})),
        )
        b.n_updates = d.get("n_updates", 0)
        return b


def disagreement_penalty(p_market: float, p_signal: float, cap_points: float = 0.25) -> float:
    """How much of the raw disagreement to keep, as a multiplier in (0, 1].

    The incumbent has a hard backstop -- `MAX_SANE_EDGE_CENTS = 25` -- added
    after a mismatched-game bug fired fake 70c edges. That backstop is right
    in spirit but binary: a 24c claimed edge sails through untouched and a
    26c one is dropped entirely.

    A smooth version is strictly better. Large disagreements between a model
    and a liquid market are far more often a *matching bug* than a genuine
    mispricing, so the further apart they are, the more of the gap should be
    treated as error. A gap of exactly `cap_points` keeps half.
    """
    gap = abs(p_market - p_signal)
    if gap <= 0:
        return 1.0
    return 1.0 / (1.0 + (gap / max(1e-9, cap_points)) ** 2)
