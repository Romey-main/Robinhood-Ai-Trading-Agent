"""End-to-end behaviour: the feed, the bots, and the competition itself."""

import statistics
import tempfile
from pathlib import Path

from arena.bots.base import Params
from arena.bots.challenger import CHALLENGER_DEFAULTS, ChallengerBot
from arena.bots.incumbent import IncumbentBot
from arena.feed import SyntheticConfig, SyntheticFeed, public_view
from arena.learning.knowledge import KnowledgeBase
from arena.learning.stats import evidence_weight, welch_t
from arena.learning.tournament import TournamentConfig, ViewLog, run_round, run_tournament
from arena.learning.transfer import TransferConfig, cross_learn


# -- feed ------------------------------------------------------------------


def test_feed_is_deterministic():
    a = SyntheticFeed(SyntheticConfig(n_markets=30, n_ticks=20), seed=5)
    b = SyntheticFeed(SyntheticConfig(n_markets=30, n_ticks=20), seed=5)
    assert [m["outcome"] for m in a.markets] == [m["outcome"] for m in b.markets]


def test_every_market_resolves_exactly_once():
    feed = SyntheticFeed(SyntheticConfig(n_markets=40, n_ticks=30), seed=1)
    seen = []
    for tick in feed.ticks():
        seen.extend(r.ticker for r in tick.resolutions)
    assert sorted(seen) == sorted(m["ticker"] for m in feed.markets)


def test_public_view_strips_ground_truth():
    feed = SyntheticFeed(SyntheticConfig(n_markets=5, n_ticks=10), seed=1)
    tick = next(iter(feed.ticks()))
    for snap in tick.snapshots.values():
        assert snap.p_true is not None  # simulator sees truth
        pub = public_view(snap)
        assert pub.p_true is None and pub.outcome is None
        assert pub.yes_bid == snap.yes_bid and pub.yes_ask == snap.yes_ask


def test_efficient_regime_has_no_bias():
    feed = SyntheticFeed(SyntheticConfig(n_markets=100, regime="efficient"), seed=3)
    assert all(m["bias"] == 0.0 for m in feed.markets)


def test_mispricing_concentrates_in_thin_markets():
    feed = SyntheticFeed(SyntheticConfig(n_markets=600, regime="mixed"), seed=5)
    thin = [m for m in feed.markets if m["depth"] < 0.33]
    deep = [m for m in feed.markets if m["depth"] > 0.66]
    p_thin = sum(m["inefficient"] for m in thin) / len(thin)
    p_deep = sum(m["inefficient"] for m in deep) / len(deep)
    assert p_thin > p_deep * 1.5


def test_prices_are_a_martingale_on_average():
    """If prices drifted predictably, beating them would be trivial."""
    feed = SyntheticFeed(SyntheticConfig(n_markets=400, n_ticks=50, regime="efficient"), seed=9)
    steps = []
    for m in feed.markets:
        steps.extend(b - a for a, b in zip(m["q"], m["q"][1:]))
    assert abs(statistics.mean(steps)) < 0.01


# -- view log --------------------------------------------------------------


def test_viewlog_keeps_first_and_last_and_stays_bounded():
    log = ViewLog(cap=8)
    for i in range(200):
        log.add(i)
    assert len(log.views) <= 8
    assert log.first == 0
    assert log.last == max(log.views)


# -- bots ------------------------------------------------------------------


def test_incumbent_defaults_reproduce_the_original_behaviour():
    bot = IncumbentBot()
    assert bot.params["use_market_prior"] is False
    assert bot.params["use_exits"] is False
    assert bot.params["use_maker_first"] is False


def test_bots_never_see_ground_truth_during_a_round():
    """Guards the whole result: a bot reading `p_true` would score perfectly."""
    seen = []

    class Spy(ChallengerBot):
        name = "spy"

        def decide(self, snapshots, signals, ctx):
            seen.extend(s.p_true for s in snapshots.values())
            seen.extend(s.outcome for s in snapshots.values())
            return super().decide(snapshots, signals, ctx)

    run_round([Spy(seed=1)], TournamentConfig(n_markets=20, n_ticks=15, seed=2), 0)
    assert seen and all(v is None for v in seen)


def test_challenger_stops_trading_when_its_signal_is_worthless():
    """The behaviour the market prior exists to produce."""
    bots = [ChallengerBot(seed=1)]
    cfg = TournamentConfig(n_markets=200, n_ticks=40, regime="efficient", seed=4)
    early = late = 0
    for r in range(8):
        results, _t, _f, _res = run_round(bots, cfg, r)
        n = len(results["challenger"].fills)
        if r < 2:
            early += n
        elif r >= 6:
            late += n
    assert bots[0].edge_shrink.k < 0.3
    assert late <= early


def test_challenger_beats_incumbent_on_transaction_costs_in_efficient_markets():
    """No edge exists, so the scoreboard is a pure overtrading measure.

    Averaged over several seeds on purpose. A single seed of eight rounds is
    not enough to separate these two -- the per-round standard deviation is
    larger than the per-round difference -- and a test that passes or fails
    on one draw of the dice is measuring the dice.
    """
    inc_pnl = chal_pnl = 0
    inc_fills = chal_fills = 0
    for seed in (11, 22, 33):
        inc, chal = IncumbentBot(seed=seed), ChallengerBot(seed=seed)
        cfg = TournamentConfig(n_markets=150, n_ticks=40, regime="efficient", seed=seed)
        for r in range(6):
            results, _t, _f, _res = run_round([inc, chal], cfg, r)
            inc_pnl += results["incumbent"].pnl_cents
            chal_pnl += results["challenger"].pnl_cents
            inc_fills += len(results["incumbent"].fills)
            chal_fills += len(results["challenger"].fills)
    assert chal_pnl > inc_pnl
    # The mechanism, not just the outcome: it wins by not trading.
    assert chal_fills < inc_fills / 3


def test_incumbent_loses_roughly_its_transaction_costs_in_efficient_markets():
    """Sanity check on the simulator itself, not on the bot."""
    inc = IncumbentBot(seed=1)
    cfg = TournamentConfig(n_markets=150, n_ticks=40, regime="efficient", seed=12)
    pnl = fees = 0
    for r in range(5):
        results, _t, _f, _res = run_round([inc], cfg, r)
        pnl += results["incumbent"].pnl_cents
        fees += sum(f.fee_cents for f in results["incumbent"].fills)
    assert pnl < 0
    # Losses should be the same order of magnitude as costs paid, not 10x.
    assert abs(pnl) < 12 * fees


def test_challenger_finds_edge_when_edge_exists():
    inc, chal = IncumbentBot(seed=1), ChallengerBot(seed=1)
    cfg = TournamentConfig(n_markets=150, n_ticks=40, regime="inefficient", seed=13)
    chal_pnl = 0
    for r in range(8):
        results, _t, _f, _res = run_round([inc, chal], cfg, r)
        chal_pnl += results["challenger"].pnl_cents
    assert chal_pnl > 0


def test_ablating_the_market_prior_removes_all_discipline():
    """The market prior is the mechanism that carries the measured shrinkage.

    With it off, the challenger believes its raw signal outright -- exactly
    the incumbent's stance -- and trades like it. This is what makes the
    ablation meaningful: the flag is not decorative, it is the difference
    between anchoring on the price and anchoring on your own model.
    """
    vals = dict(CHALLENGER_DEFAULTS)
    vals["use_market_prior"] = False
    off = ChallengerBot(Params(vals, locked=set(), bounds={}), seed=1)
    off.name = "off"
    on = ChallengerBot(seed=1)
    cfg = TournamentConfig(n_markets=150, n_ticks=40, regime="efficient", seed=14)
    pnl = {"off": 0, "challenger": 0}
    fills = {"off": 0, "challenger": 0}
    for r in range(6):
        results, _t, _f, _res = run_round([off, on], cfg, r)
        for n in pnl:
            pnl[n] += results[n].pnl_cents
            fills[n] += len(results[n].fills)
    assert fills["off"] > fills["challenger"] * 3
    assert pnl["off"] < pnl["challenger"]


# -- statistics ------------------------------------------------------------


def test_welch_t_finds_no_difference_between_identical_samples():
    xs = [1.0, 2.0, 3.0, 4.0] * 30
    assert welch_t(xs, xs).p_value > 0.9


def test_evidence_weight_requires_sample_and_significance():
    small = welch_t([1.0] * 5, [0.0] * 5)
    assert evidence_weight(small, min_n=60) == 0.0
    noise = welch_t([0.5, -0.5] * 200, [0.4, -0.4] * 200)
    assert evidence_weight(noise, min_n=60) < 0.5


# -- tournament and cross-learning ----------------------------------------


def test_tournament_runs_and_writes_its_artifacts():
    with tempfile.TemporaryDirectory() as d:
        cfg = TournamentConfig(
            rounds=3, n_markets=60, n_ticks=25, seed=6,
            kb_path=f"{d}/kb.sqlite", lessons_path=f"{d}/L.md", results_path=f"{d}/r.json",
        )
        report = run_tournament([IncumbentBot(seed=1), ChallengerBot(seed=1)], cfg)
        assert len(report["history"]) == 3
        assert set(report["totals"]) == {"incumbent", "challenger"}
        assert Path(f"{d}/L.md").exists()
        assert Path(f"{d}/r.json").exists()


def test_bots_stay_distinct_under_cross_learning():
    """The diversity floor is what keeps the competition informative."""
    with tempfile.TemporaryDirectory() as d:
        cfg = TournamentConfig(
            rounds=6, n_markets=80, n_ticks=25, seed=8,
            kb_path=f"{d}/kb.sqlite", lessons_path=f"{d}/L.md", results_path=f"{d}/r.json",
            transfer=TransferConfig(min_divergence=0.15),
        )
        report = run_tournament([IncumbentBot(seed=1), ChallengerBot(seed=1)], cfg)
        for row in report["history"]:
            for dist in row["divergence"].values():
                assert dist >= 0.10, "bots collapsed into each other"


def test_locked_parameters_are_never_transferred():
    inc, chal = IncumbentBot(seed=1), ChallengerBot(seed=1)
    assert inc.params["use_market_prior"] is False
    with tempfile.TemporaryDirectory() as d:
        kb = KnowledgeBase(f"{d}/kb.sqlite")
        for r in range(4):
            results, _t, _f, _res = run_round([inc, chal], TournamentConfig(
                n_markets=80, n_ticks=25, regime="inefficient", seed=9), r)
        cross_learn(kb, 0, [inc, chal], [], TransferConfig())
        kb.close()
    assert inc.params["use_market_prior"] is False  # its identity, untouchable


def test_knowledge_pooling_transfers_calibration_evidence():
    inc, chal = IncumbentBot(seed=1), ChallengerBot(seed=1)
    inc.grant_capability("use_calibration")
    for _ in range(400):
        chal.signal_curve.observe(0.8, 0)
    before = inc._curve.correct(0.8)
    inc.import_knowledge(chal.export_knowledge(), 1.0)
    assert inc._curve.correct(0.8) < before


def test_learning_is_reproducible():
    def go():
        cfg = TournamentConfig(n_markets=60, n_ticks=20, seed=3)
        bots = [IncumbentBot(seed=1), ChallengerBot(seed=1)]
        out = []
        for r in range(3):
            results, _t, _f, _res = run_round(bots, cfg, r)
            out.append((results["incumbent"].pnl_cents, results["challenger"].pnl_cents))
        return out

    assert go() == go()
