"""
Small statistics helpers used to gate learning.

The whole point of gating is to stop the tournament from teaching one bot a
lesson that is really just noise. Both bots trade a few hundred contracts a
round; a difference of a cent per contract over 200 contracts is well inside
what luck produces. Without a significance gate, cross-learning degenerates
into copying whoever won the last coin flip -- which is worse than not
learning at all, because it also destroys the diversity that made the
comparison informative.

Standard library only, so: Welch's t for unequal variances, a normal
approximation for the tail, and a bootstrap for the cases where the
per-contract P&L distribution is too lumpy for a t to be trusted (it usually
is -- binary payoffs are bimodal).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class Comparison:
    mean_a: float
    mean_b: float
    n_a: int
    n_b: int
    t_stat: float
    p_value: float
    effect: float  # mean_a - mean_b

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05

    def to_dict(self) -> dict:
        return {
            "mean_a": round(self.mean_a, 4),
            "mean_b": round(self.mean_b, 4),
            "n_a": self.n_a,
            "n_b": self.n_b,
            "t": round(self.t_stat, 3),
            "p": round(self.p_value, 4),
            "effect": round(self.effect, 4),
        }


def _mean_var(xs: list) -> tuple:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mu = sum(xs) / n
    if n < 2:
        return mu, 0.0
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return mu, var


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def welch_t(a: list, b: list) -> Comparison:
    """Two-sample Welch t-test with a normal tail approximation.

    The normal approximation is fine here because the gate only ever fires
    with samples in the hundreds, where t and z agree to more precision than
    the decision needs.
    """
    na, nb = len(a), len(b)
    mu_a, var_a = _mean_var(a)
    mu_b, var_b = _mean_var(b)
    if na < 2 or nb < 2:
        return Comparison(mu_a, mu_b, na, nb, 0.0, 1.0, mu_a - mu_b)
    se = math.sqrt(var_a / na + var_b / nb)
    if se <= 0:
        return Comparison(mu_a, mu_b, na, nb, 0.0, 1.0, mu_a - mu_b)
    t = (mu_a - mu_b) / se
    p = 2.0 * (1.0 - normal_cdf(abs(t)))
    return Comparison(mu_a, mu_b, na, nb, t, p, mu_a - mu_b)


def bootstrap_mean_ci(xs: list, iters: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple:
    """Percentile bootstrap CI for the mean. Robust to bimodal payoffs."""
    if not xs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(xs)
    means = []
    for _ in range(iters):
        means.append(sum(xs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(alpha / 2 * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)


def evidence_weight(cmp: Comparison, min_n: int = 60) -> float:
    """0..1 confidence that `a` genuinely beats `b`.

    Combines sample size and significance, so a transfer moves a parameter a
    little on thin-but-suggestive evidence and a lot on thick-and-clear
    evidence, rather than flipping on a single threshold.
    """
    if cmp.n_a < min_n or cmp.n_b < min_n:
        return 0.0
    if cmp.effect <= 0:
        return 0.0
    if cmp.p_value >= 0.20:
        return 0.0
    sig = max(0.0, min(1.0, (0.20 - cmp.p_value) / 0.20))
    size = min(1.0, math.sqrt(min(cmp.n_a, cmp.n_b) / (4.0 * min_n)))
    return sig * size
