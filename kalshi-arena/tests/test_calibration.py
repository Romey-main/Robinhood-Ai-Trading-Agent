import random

from arena.calibration import (
    EdgeShrinkage,
    ReliabilityCurve,
    brier,
    brier_skill_vs_market,
    expected_calibration_error,
    logit,
    sigmoid,
)


def test_empty_curve_is_identity_under_correct():
    c = ReliabilityCurve()
    for p in (0.05, 0.3, 0.5, 0.77, 0.95):
        assert abs(c.correct(p) - p) < 1e-6


def test_correct_preserves_within_bin_resolution():
    """`apply` collapses a bin to one value; `correct` must not."""
    c = ReliabilityCurve(n_bins=10, prior_strength=1.0)
    for _ in range(400):
        c.observe(0.75, 1)
    for _ in range(400):
        c.observe(0.75, 0)  # bin truth is 0.5, badly miscalibrated
    assert c.correct(0.71) != c.correct(0.79)
    assert abs(c.apply(0.71) - c.apply(0.79)) < abs(c.correct(0.71) - c.correct(0.79))


def test_correct_moves_forecasts_toward_observed_frequency():
    c = ReliabilityCurve(n_bins=10, prior_strength=1.0)
    for _ in range(500):
        c.observe(0.8, 0)  # "0.8" always loses
    assert c.correct(0.8) < 0.8


def test_uncertainty_shrinks_with_evidence():
    c = ReliabilityCurve(n_bins=10, prior_strength=5.0)
    before = c.uncertainty(0.5)
    for i in range(2000):
        c.observe(0.5, i % 2)
    assert c.uncertainty(0.5) < before


def test_merge_pools_evidence():
    a = ReliabilityCurve(n_bins=10)
    b = ReliabilityCurve(n_bins=10)
    for _ in range(300):
        b.observe(0.7, 0)
    before = a.correct(0.7)
    a.merge(b)
    assert a.correct(0.7) < before


def test_curve_roundtrips_through_dict():
    c = ReliabilityCurve(n_bins=8, prior_strength=12.0)
    for i in range(100):
        c.observe(i / 100.0, i % 2)
    d = ReliabilityCurve.from_dict(c.to_dict())
    assert d.n_bins == c.n_bins
    assert abs(d.correct(0.42) - c.correct(0.42)) < 1e-9


def test_brier_skill_is_zero_against_itself():
    rows = [(0.3, 0.3, 1), (0.8, 0.8, 0), (0.5, 0.5, 1)]
    assert abs(brier_skill_vs_market(rows)) < 1e-12


def test_brier_skill_positive_when_bot_is_better():
    rng = random.Random(0)
    rows = []
    for _ in range(3000):
        q = rng.random()
        o = 1 if rng.random() < q else 0
        rows.append((q, 0.5, o))  # bot knows truth, market always says 0.5
    assert brier_skill_vs_market(rows) > 0.3


def test_ece_zero_for_calibrated_forecasts():
    rng = random.Random(1)
    pairs = []
    for _ in range(20000):
        p = round(rng.random(), 1)
        pairs.append((p, 1 if rng.random() < p else 0))
    assert expected_calibration_error(pairs) < 0.02


# -- EdgeShrinkage ---------------------------------------------------------


def test_edge_shrinkage_starts_sceptical():
    """An untested forecaster must earn the right to believe its own edge."""
    assert EdgeShrinkage().k == 0.0


def test_edge_shrinkage_learns_k_near_one_for_honest_edges():
    e = EdgeShrinkage(ridge=50.0)
    rng = random.Random(2)
    for _ in range(4000):
        claimed = rng.gauss(0, 8)
        # Realised edge equals claimed plus pure noise: an honest forecaster.
        e.observe(claimed, claimed + rng.gauss(0, 30))
    assert 0.8 < e.k < 1.2


def test_edge_shrinkage_learns_k_near_zero_for_noise():
    e = EdgeShrinkage(ridge=50.0)
    rng = random.Random(3)
    for _ in range(4000):
        # Claimed edge is uncorrelated with what happens: pure noise.
        e.observe(rng.gauss(0, 8), rng.gauss(0, 30))
    assert e.k < 0.15


def test_edge_shrinkage_k_toward_pools_a_new_segment():
    empty = EdgeShrinkage(ridge=100.0)
    assert abs(empty.k_toward(0.6) - 0.6) < 1e-9


def test_edge_shrinkage_roundtrips():
    e = EdgeShrinkage(ridge=10.0)
    e.observe(5.0, 4.0)
    f = EdgeShrinkage.from_dict(e.to_dict())
    assert abs(f.k - e.k) < 1e-12
