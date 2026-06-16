# Systematic sleeves — strategies, risk architecture, and honest caveats

Two long-only, rules-based equity sleeves, implemented from the planning specs.
Risk management and data integrity are the first-class concern here, not an
afterthought.

## The two strategies

### 1. Short-term mean reversion (`mean_reversion`)
Rank Russell 1000 by **trailing 5-day total return ascending**, buy the 10
worst, equal-weight (10% each), hold ~1 week, re-rank. Weekly (Fri close
signal, Mon open fill).

> **Why this needs the anti-rug layer most.** Buying the worst recent
> performers is, mechanically, catching falling knives. The `event_move_cap`
> (default ±20%) is the guard that separates an ordinary pullback (which
> mean-reverts) from a structural break — M&A, earnings shock, halt, fraud
> blow-up — which does *not* revert. Those are excluded, never bought.

### 2. 12-1 cross-sectional momentum (`momentum`)
Classical Jegadeesh-Titman. Rank by **total return over a 12-month formation
window ending one month ago** (the 1-month skip avoids short-term-reversal
contamination), buy the top 50 equal-weight (2% each), hold ~1 month, re-rank.
Monthly, with a turnover-suppression skip when portfolio drift is small.

## Risk-first architecture (the pipeline)

Every rebalance flows **strategy → data-quality → portfolio risk**, and it is
**fail-closed**: anything we can't prove is clean is dropped, and if the whole
basket looks untrustworthy the decision is `DO_NOT_TRADE` (hold cash).

```
members (point-in-time)         data_quality.validate_series         portfolio_risk.vet
   minus denylist        ─────►  per-name hard gates         ─────►   circuit breakers +
   (universe.py)                 (the anti-rug layer)                  caps + cash routing
```

### Anti-"invalid information" guarantees (`data_quality.py`)
A name is **rejected** (not traded) if any of these hold:

| check | guards against |
|---|---|
| `denylisted` | known-bad / corrupted tickers |
| `no_data` / `insufficient_history` | trading on a name we can't actually evaluate |
| `nonfinite_price` | NaN/zero/negative ticks fabricating returns |
| `below_price_floor` ($10) | microcap noise and wide spreads |
| `stale` (>4 days) | trading on a frozen/dead data feed |
| `suspicious_jump` (>40%/day) | unadjusted splits, bad ticks, catastrophic events |

Returns are computed only from **split + dividend adjusted closes**, so a 2:1
split never reads as a "-50% crash." Membership is **point-in-time** — you can
only hold what was in the index *as of that date*, which is what stops
survivorship bias from silently deleting the companies that blew up.

### Circuit breakers (`portfolio_risk.py`)
* **Data-feed health** — if <80% of the universe passes validation, the feed is
  presumed broken → `DO_NOT_TRADE`.
* **Concentration floor** — fewer than 5 vetted names → `DO_NOT_TRADE`.
* **Per-name cap** — hard ceiling per position; excess is routed to **cash**, not
  re-concentrated into the survivors.
* **Scale sanity** — warns when $/name falls below a tradeable floor (the $50
  reality: a 50-name basket is ~$1/name, which you can't trade cleanly).

## Commands

```bash
# Vetted target basket for a date (no orders, full risk report)
python -m rhbot rebalance --strategy momentum --panel data/sample_panel.csv \
    --members data/membership.json --account-value 50

# Walk-forward backtest through the SAME risk pipeline (with costs)
python -m rhbot backtest --strategy mean_reversion --panel data/sample_panel.csv --freq weekly
```

## Data you must supply for a real run

| input | format | notes |
|---|---|---|
| price panel | CSV `date,symbol,adj_close` | **split+dividend adjusted**; full R1000 history |
| membership | JSON `{"YYYY-MM-DD": ["AAPL", ...]}` | **point-in-time IWB** holdings; without it you get survivorship bias |
| denylist | `data/denylist.txt` | your real 37 tickers (I won't invent them) |

`data/sample_panel.csv` is a 14-name, 2-year demo fixture — enough to exercise
the pipeline, **not** enough to validate a strategy.

## ⚠️ Read this before trusting any backtest number

The demo backtest prints Sharpes near ~1.0. **Do not believe them as
validation.** They are a *smoke test of the engine*, not a verdict on the
strategies, because the demo fixture is:

1. **14 hand-picked mega-caps, not the Russell 1000** — no breadth, no dispersion.
2. **Survivor-only and static** — these names are survivors that did well; the
   blow-ups that the strategy would have to survive aren't in the sample. This
   *inflates* returns. It is the exact bias the point-in-time machinery exists
   to prevent — and the demo lacks the membership data to prevent it.
3. **Only ~2 years**; momentum needs ~13 months of warmup, so only ~12 real
   rebalances. A Sharpe from 12 points is statistically meaningless.

A credible backtest needs the full point-in-time R1000 panel + membership.
Until then, the mean-reversion sleeve remains **un-validated** (its spec said
"BACKTEST PENDING" for a reason), and real capital beyond a token amount is not
justified. The momentum sleeve's "16-year, Sharpe 0.65" claim lives in the spec
and likewise needs to be reproduced on real data here before it's trusted.

## Scale reality at $50

These are diversified *portfolio* sleeves (10 and 50 names). On $50 that's
$5/name and $1/name respectively — the risk layer will warn, and rightly so.
Diversified factor sleeves want four figures of capital to express cleanly.
Run them in `rebalance`/`backtest` (advisory/paper) until both the data and the
capital are real.
