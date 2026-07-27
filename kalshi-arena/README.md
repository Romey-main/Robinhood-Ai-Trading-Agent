# kalshi-arena

Two Kalshi trading bots, a paper exchange that charges honestly for bad
execution, and a tournament in which the bots compete round after round and
learn from each other.

**Paper only.** There is no order-placement code in this package — not
disabled behind a flag, simply absent. The only network path is a read-only
market-data client.

```bash
cd kalshi-arena
python3 -m arena.cli compete --rounds 10 --markets 200   # run the competition
python3 -m arena.cli ablate                              # what is each feature worth
python3 -m arena.cli lessons                             # what the bots have learned
python3 -m pytest -q                                     # 85 tests
```

No dependencies. Standard library only (`pytest` for the tests).

---

## The two bots

**Bot A — `incumbent`** (`arena/bots/incumbent.py`) is a faithful port of the
existing `kalshi-bot` from `Romey-main/experimental1@claude/kalshi-bot-ipIYv`.
It is not a strawman. It keeps everything that bot gets right: fees checked
before entry, fractional Kelly, a liquidity filter, a drawdown ladder, a
kill switch, a sanity backstop on implausible edges, and a performance gate
that mutes a category once it has proven unprofitable.

It also faithfully keeps the six decisions the challenger is built to test:

| | Incumbent |
|---|---|
| Belief | `p_est` straight from the calculator; the price is the thing to beat |
| Fees | smooth `0.07·P·(1−P)` per contract, no per-order rounding |
| Learning | only from its own settled trades (10+ before it learns anything) |
| Execution | crosses the spread on every entry |
| Exits | none — buy and hold to settlement |
| Risk | per-market caps, no portfolio-gross cap |

**Bot B — `challenger`** (`arena/bots/challenger.py`) changes those and
nothing else. The central one:

```
belief = price + k · (calibrated_signal − price)
```

where `k` is fitted by regressing **realised** edge on **claimed** edge over
every market that has ever resolved. That single line does three jobs:

- **It makes the price the anchor.** The bot can only move off the market as
  far as its track record justifies.
- **It corrects the optimizer's curse.** A bot that screens hundreds of
  markets and trades its largest apparent edges is selecting for estimation
  error, not opportunity. Fitting `k` on outcomes measures that bias directly
  and removes it, with no tuned constants.
- **It turns itself off.** When the signal is worthless, `k → 0`, belief
  collapses onto the price, and the bot stops trading. Measured: `k` lands at
  0.00–0.13 in efficient markets and 0.35–0.75 in mispriced ones, with
  nobody telling it which is which.

Plus: exact per-order fees, calibration learned from every observed
resolution rather than only from its own trades, maker-first execution priced
on the belief that survives *being filled*, and portfolio-level exposure
budgets.

---

## Results

Eight seeds × eight rounds per cell, $1,000 bankroll, 200 markets/round.
`±` is standard error of the per-round mean.

| Regime | Bot | $/round | Sharpe | Trades | Fees | Brier skill vs market |
|---|---|---:|---:|---:|---:|---:|
| **efficient** — price *is* truth, no edge exists | incumbent | −20.04 ± 3.44 | −0.73 | 9,845 | $620 | −0.0494 |
| | **challenger** | **−4.91 ± 2.73** | −0.23 | **667** | $124 | −0.0162 |
| **mixed** — 35% of markets mispriced | incumbent | +1.56 ± 3.16 | 0.06 | 11,136 | $696 | −0.0216 |
| | **challenger** | **+76.57 ± 14.53** | **0.66** | 5,248 | $1,333 | −0.0083 |
| **inefficient** — every price carries a bias | incumbent | +30.83 ± 3.40 | 1.13 | 10,753 | $649 | +0.0041 |
| | **challenger** | **+225.58 ± 21.65** | **1.30** | 6,818 | $1,729 | +0.0117 |

The efficient row is the one worth staring at. No edge exists there, so the
correct P&L is zero and every trade is a donation. The incumbent loses
\$20/round, which is almost exactly its transaction costs — the simulator
reproducing a known result is a good sign that the simulator is honest. The
challenger loses \$4.91 because it works out there is nothing to do and
places 93% fewer trades.

A single 10-round tournament with cross-learning on (`--seed 7`, mixed):
**challenger +\$1,126.99, incumbent −\$82.25, rounds won 9–1.**

### What each feature is actually worth

Ablation, 8 seeds × 8 rounds, mixed regime. Each row toggles one feature.

| Toggled | $/round | ± | Δ vs baseline |
|---|---:|---:|---:|
| *(baseline)* | 70.88 | 14.42 | — |
| `use_maker_first` off | 37.84 | 8.41 | **−33.04** |
| `use_calibration` off | 66.17 | 14.44 | −4.71 |
| `use_exact_fees` off | 72.18 | 15.15 | +1.30 |
| `use_market_prior` off | 70.52 | 15.26 | −0.36 |
| `use_cluster_caps` off | 70.88 | 14.42 | +0.00 |
| `use_tier_shrinkage` on | 74.77 | 14.64 | +3.89 |
| `use_exits` on | −3.97 | 12.20 | **−74.85** |

Read honestly, that table says:

- **Maker-first execution is the single biggest contributor**, worth \$33/round.
- **Exits, as implemented, are a disaster** — they cost \$75/round, so they
  ship **off by default**. Every exit pays a second taker fee and hands back
  the remaining edge. The code is kept and tested because a maker-side exit
  might flip the sign, but nothing here supports turning it on.
- **`use_cluster_caps` is inert** in this configuration — it never binds. It
  is not doing the work its name implies, and the table says so.
- **`use_exact_fees` and `use_market_prior` are within noise on P&L** in the
  mixed regime. Exact fees are still strictly more correct, and the market
  prior earns its place in the *efficient* regime rather than this one:
  ablating it there triples the trade count and loses money (there is a test
  for exactly this). It is insurance, and insurance does not pay out in the
  good case.
- `use_tier_shrinkage` (+3.89 ± 14.64) is **not** established. Left off.

---

## The competition and the cross-learning

`arena/learning/tournament.py` runs the rounds. The rules exist to stop the
scoreboard measuring the wrong thing:

- **Identical worlds.** Same markets, books and signals, same ticks.
- **Independent books.** Each bot trades its own copy, so the winner is not
  decided by which bot the loop polls first.
- **No ground truth reaches a bot.** Snapshots are stripped of `p_true` and
  `outcome` by copy, not by convention. There is a test that a bot reading
  those fields sees `None`.
- **Bankroll resets each round; knowledge does not.** Rounds stay comparable
  samples. What compounds is what was learned.
- **Learning happens between rounds, never inside one.**

Between rounds, three channels move knowledge (`arena/learning/transfer.py`):

1. **Pooled evidence, always.** Calibration data is merged across bots. A
   market resolving is a fact about the world, not a competitive advantage.
2. **Parameter transfer, gated on a significant contrast.** A threshold moves
   toward the peer's value only when the market segment that parameter
   governs shows a real difference (Welch t, minimum 60 contracts a side).
3. **Capability transfer, gated on an attributed lesson.** Flipping a design
   switch needs a lesson that names the mechanism and measures it — maker
   execution is credited from maker-vs-taker P&L, not from "the challenger
   won the round".

Two brakes stop the obvious failure mode, which is two bots copying each
other until they are the same bot and the comparison stops producing
information:

- **Locked parameters.** Each bot keeps an identity it never trades away.
- **A diversity floor.** Any transfer that would pull the parameter vectors
  closer than `min_divergence` is scaled back. Measured across the 10-round
  run: divergence went 0.381 → 0.343, never near the 0.12 floor.

**The learning is genuinely two-way.** In the shipped run the challenger
taught the incumbent maker-first execution (granted in round 0 on a lesson
with p < 0.001) — the incumbent's maker share went from 0% to 53–74% — while
the incumbent taught the challenger about position sizing. Neither of those
is the loser copying the winner; they are two designs each being right about
something different.

Everything is written to `knowledge/`: `kb.sqlite` (observations, trades,
lessons, transfers), `LESSONS.md` (human-readable, each line carrying its
sample size and p-value), and `results.json`.

---

## Honesty about the data

Real settled Kalshi markets look like a free backtest set and are a trap.
Their stored prices are post-resolution. Measured against the live API:

```
previous_price 0.0–0.1: n=85  realised YES = 0.00
previous_price 0.9–1.0: n=85  realised YES = 1.00
```

That is not forecasting skill, it is reading the answer off the back of the
page. Any bot evaluated that way looks brilliant and loses money live.

So the competition is scored on a generative simulator where the true
probability is knowable, and the only unbiased real-data path is to record
books *before* outcomes are known and join settlements *after*:

```bash
python3 -m arena.harvest record --minutes 90 --every 60   # 835 live markets/poll
# ...wait for the markets to actually resolve...
python3 -m arena.harvest settle
```

`ReplayFeed` then runs the arena over the recorded books. The cost is
calendar time — a dataset covering markets that resolve tomorrow exists
tomorrow — and there is no way around that which is also honest. A sample
recording of 835 real quoted markets is in `data/`.

### What the simulator models, and why

The part that has to be right or the whole thing is a lie is **passive
execution**. Resting an order is free money in a naive simulator: you always
fill, always at your price, never at a bad time. Here:

- a resting buy fills **for certain** when the market trades down through it;
- at the touch it fills probabilistically, driven by volume and queue;
- behind the touch it does not fill.

So quoting inside the spread earns the spread and pays for it in adverse
selection — the real trade-off. The challenger prices maker orders on the
belief conditional on *being filled*, which is lower than the belief that
made the order look good. An earlier version skipped that and lost 25c per
contract resting orders in 85c+ markets.

Mispricing in the generator is tied to liquidity rather than sprinkled at
random (thin markets are 48% mispriced, deep ones 13%), because that is how
real venues behave and because assigning it independently of the order book
would make it unlearnable by construction.

---

## Things that went wrong, kept on the record

These are in the source comments too, because a number that changed without
a reason is not knowledge.

- **Unconstrained blend weights.** Fitting two free coefficients on price and
  signal let them sum past 1, so *perfect agreement* between model and market
  produced a phantom edge toward the favourite. Cost \$112 in one round.
  Fixed by constraining the pool to be convex.
- **Regularising toward the starting value.** Shrinking the signal weight
  toward its initial 0.30 made "my signal is worth something" unfalsifiable.
  Shrinking toward **zero** lets a worthless signal decay to zero. Worth
  ~\$18/round in efficient markets.
- **Binned calibration destroying resolution.** Replacing a forecast with its
  bin's average strips the distinction between 0.71 and 0.79. It turned
  +0.0050 Brier skill into −0.0026 — the calibration step destroyed more
  information than the miscalibration it removed. Fixed by applying the curve
  as an additive log-odds *correction*.
- **Training only on each market's final snapshot.** The last tick before
  resolution is where the price is already nearly right, so training there
  taught the bot the market is perfect and suppressed its own signal weight.
  That was a bug in the harness masquerading as a finding about the bot.
- **Double-shrinking.** The pool weight `a` and the shrinkage slope `k` are
  two estimates of the same quantity. Pooling and *then* shrinking applied
  the correction twice, leaving the bot believing about a fifth of its own
  signal. Removing it was worth ~\$59/round in the mixed regime and is why
  the pool is now used for reported forecasts but not for sizing.
- **Per-tier shrinkage.** Splitting the sample three ways made each slope too
  noisy; it turned −\$1.49/round into −\$17.58. Off by default.

---

## Adding your own bot

Implement `arena.bots.base.Bot` — `decide(snapshots, signals, ctx) ->
BotAction` plus optional `learn(...)` — and pass it to `run_tournament`. If it
exposes a `Params` vector it joins the cross-learning automatically. Three or
more competitors work; the tournament is not hardcoded to two.

## Layout

```
arena/
  fees.py          exact Kalshi fee model (per-order ceiling, maker/taker)
  calibration.py   reliability curves, EdgeShrinkage, Brier/ECE/skill
  blend.py         logarithmic opinion pool, fitted online
  kelly.py         fee-aware, uncertainty-adjusted sizing + exposure budgets
  exchange_sim.py  paper matching engine with honest adverse selection
  portfolio.py     cash, positions, exposure
  feed.py          synthetic generator (ground truth) + replay
  kalshi_public.py read-only market data (urllib, rate-limited)
  harvest.py       record-now / settle-later real data pipeline
  scoring.py       money, forecasting, execution, discipline
  bots/            incumbent, challenger
  learning/        knowledge base, lesson mining, transfer, tournament
tests/             85 tests
```
