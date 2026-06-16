# Robinhood AI Trading Agent

A **paper-first** research, screening, and trade-journaling toolkit for a small
account. It helps you find liquid, moving stocks, attach news/sentiment, size a
risk-bounded trade, and — most importantly — **measure whether the idea
actually works before you risk real money.**

> ⚠️ **Read this first.** This repo does **not** place real orders. That is a
> deliberate design choice. The strategy has to earn a track record on paper
> first. See "Honest expectations" below.

## Honest expectations

This was built around a specific request: *"find the most volatile/hyped stock
on social media and trade it with $50 for a high win rate and solid returns."*
Here is the unvarnished truth about that:

1. **Chasing socially-hyped volatile stocks is the worst-documented strategy
   for small retail accounts.** By the time a ticker is trending, the move has
   usually already happened — the hype is the *exit liquidity* for people who
   were early. This toolkit deliberately puts a **volatility ceiling** and an
   **anti-chasing momentum penalty** in the screener so it leans away from
   blow-off pumps rather than into them.
2. **At $50, friction dominates.** A great day is +10% = **$5**, but the
   bid/ask spread + slippage on a hyped micro-cap is routinely 2–5% *per round
   trip*. Small + volatile is the most expensive thing to trade.
3. **It is a cash account.** The Pattern-Day-Trader rule does *not* apply, but
   stock sales settle T+1 and reusing unsettled cash is a good-faith
   violation — so you realistically get **~1 round-trip per day** with the full
   balance. "Lots of trades" is not on the table at this size.
4. **No edge is assumed.** That's the entire point of the paper engine: prove a
   positive *expectancy* over a real sample (aim for 20–30+ trades) **before**
   risking a cent. A high win rate alone is a trap — you can win 90% of the
   time and still lose money if the losers are big.

If after a few weeks of paper trading the expectancy is positive and the
drawdown is tolerable, *then* it's rational to graduate to tiny, human-confirmed
real trades. Treat the $50 as tuition, not seed capital.

## What's in here

```
rhbot/
  # --- v1: discretionary single-name screener (the $50 paper journal) ---
  config.py          tunable thresholds + risk params (the constraints live here)
  models.py          Snapshot / ScoredCandidate / PaperTrade dataclasses
  screener.py        liquidity + volatility + momentum + sentiment scoring
  risk.py            position sizing, stop/target, pre-trade guardrails
  paper_engine.py    the measurement core: ledger, win rate, expectancy, drawdown

  # --- v2: systematic, risk-first portfolio sleeves ---
  data_quality.py    per-name validation — the anti-rug / "no bad data" layer
  universe.py        point-in-time index membership + corruption denylist
  panel.py           date x symbol grid of split/dividend-adjusted closes
  strategies/        mean_reversion (5-day) and momentum (12-1) sleeves
  portfolio_risk.py  fail-closed vetting: circuit breakers, caps, cash routing
  backtest.py        walk-forward backtest through the SAME risk pipeline

  cli.py             `python -m rhbot screen|open|mark|report|rebalance|backtest`
  providers/         standalone Yahoo (optional) + JSON ingest
tests/               39 unit tests (screener, risk, paper, data-quality,
                     strategies, portfolio-risk, backtest)
data/                example universe, sample price panel, denylist
ledger/              your paper-trade journal (JSON)
```

## Systematic sleeves (v2) — risk and data integrity first

Two long-only factor sleeves built from the planning specs: **5-day mean
reversion** and **12-1 momentum**. Every rebalance runs
`strategy → data-quality → portfolio risk` and is **fail-closed** — stale,
non-finite, gapped, denylisted, or suspicious-jump data gets a name dropped,
and a broken-looking feed or an over-concentrated basket returns
`DO_NOT_TRADE`. Full design, the anti-"invalid information" guarantees, data
requirements, and **honest backtest caveats** are in **[STRATEGIES.md](STRATEGIES.md)**.

```bash
python -m rhbot rebalance --strategy momentum --panel data/sample_panel.csv --account-value 50
python -m rhbot backtest  --strategy mean_reversion --panel data/sample_panel.csv --freq weekly
```

> The demo `sample_panel.csv` is 14 mega-caps over 2 years — enough to exercise
> the pipeline, **not** to validate a strategy. Real use needs the full
> point-in-time Russell 1000 panel + membership. See STRATEGIES.md.

## Quickstart

```bash
# 1. Rank a universe of candidates (no orders, just analysis)
python -m rhbot screen --universe data/universe.example.json --top 5

# 2. Record a PAPER trade on a candidate (sizing/stop/target from config)
python -m rhbot open --universe data/universe.example.json --symbol AMD

# 3. Each day, mark open trades against new prices (last:high:low)
python -m rhbot mark --bars "AMD=146.0:147.2:144.1"

# 4. See how the strategy is actually doing
python -m rhbot report
```

To pull live price/volume data yourself without any broker keys:

```bash
pip install yfinance        # optional, only for the Yahoo provider
```

…then build snapshots with `rhbot.providers.yahoo.YahooProvider`. Sentiment is
left at 0 by the price feed — fill it in from your own news research.

## The workflow

1. **Screen** a universe (e.g. the day's movers) → ranked shortlist + the
   *reasons* each name ranked where it did.
2. **Research** the top 1–3 names: real catalyst or just noise? Set a sentiment
   score in the snapshot JSON.
3. **Paper-open** the best setup. The risk module sizes it, attaches a stop and
   target, and refuses anything whose reward:risk is too low.
4. **Mark** daily until the stop or target is hit.
5. **Report** weekly. Only consider real money once expectancy is positive over
   a meaningful sample.

## Risk rules (defaults, all in `config.py`)

| rule | default | why |
|------|---------|-----|
| max open positions | 1 | can't diversify $50 |
| position size | 95% of buying power | fractional shares |
| stop loss | −8% | cap the downside every time |
| take profit | +12% | reward:risk = 1.5 |
| min reward:risk | 1.3 | skip lopsided setups |
| liquidity floor | $20M/day | so fills land near the quote |
| price floor | $2 | avoid penny-stock spreads/halts |
| volatility band | 3%–25% daily | want movement, avoid halt-prone pumps |

## Tests

```bash
python -m unittest discover -s tests -v
```
