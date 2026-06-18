# Systematic sleeves — strategies, risk architecture, and honest caveats

Two long-only, rules-based equity sleeves, implemented from the planning specs.
Risk management and data integrity are the first-class concern here, not an
afterthought.

## The strategies (three sleeves)

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

### 3. Low volatility (`low_vol`)
Rank by **trailing realized volatility ascending**, buy the 30 lowest-vol names.
The low-vol anomaly: low-risk stocks have historically delivered better
*risk-adjusted* returns than high-risk ones. This is the defensive sleeve — it
tilts to utilities, REITs, and staples, so it diversifies the momentum book
(which loads on high-vol semis/AI names).

## Portfolio construction (weighting + concentration caps)

After a sleeve picks its names, a shared construction layer
(`portfolio_construction.py`) turns them into final weights:
- **Weighting** — `equal` (default) or `inverse_vol` (risk-balanced: each name
  contributes similar volatility).
- **Sector caps** — no single sector exceeds `max_sector_weight` (default 30%),
  using the GICS sectors from your iShares file (`data/sectors.json`, auto-loaded).
  On the live momentum basket this pulls Information Technology from **48% → 30%**,
  spreading the rest across Industrials, Communication, Energy, Health Care.
- **Per-name cap** — `max_name_weight` (default 12%).
- **Vol targeting** — set `target_annual_vol > 0` to scale gross exposure down
  (hold cash) when the basket's estimated volatility runs hot.

Anything that can't be placed under the caps stays in **cash** — never forced in.

## Combining sleeves & turnover control

**Multi-sleeve portfolio (`combine`).** Blend sleeves into one vetted book:
```bash
python -m rhbot combine --sleeves momentum:0.5,low_vol:0.5 \
    --members data/membership.json --panel data/panel_full.csv
```
The blend is re-capped (sector/name) as one portfolio. Blending the high-vol
momentum book with the defensive low-vol book cuts top-sector concentration from
**30% → 15%** — diversification across *factors*, not just names.

The composite is a first-class strategy, so you can **backtest the blend** head
to head with `backtest --sleeves momentum:0.5,low_vol:0.5`. On the (still
survivorship-biased) demo panel the diversification shows up exactly as theory
predicts — the blend keeps most of momentum's risk-adjusted return at far lower
risk:

| | momentum | low_vol | combo |
|---|---|---|---|
| Sharpe | 1.26 | 0.43 | 1.22 |
| ann vol | 17.0% | 10.2% | 10.5% |
| max drawdown | −9.4% | −6.7% | **−5.0%** |

(Illustrative mechanics, **not** validation — see the survivorship warning below.)

**Turnover & cost.** Costs are charged on turnover everywhere (often the whole
story at small size). The backtest reports `avg_turnover` and total `cost_drag`;
`rebalance --current book.json` reports turnover vs your live book (buys/exits +
estimated bps). A **no-trade band** (`no_trade_band`) holds names whose target is
within the band of current — it trims *weight-drift* churn, though not
*composition* churn (names entering/leaving the basket always trade).

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

`data/sample_panel.csv` is a 14-name, 2-year demo fixture (mega-caps).
`data/r1000_demo_panel.csv` is a larger 66-name US-stock cross-section (the
liquid common stocks from Robinhood's "100 most popular", with ETFs and foreign
ADRs removed). Both exercise the pipeline; **neither validates a strategy.**

### Bring your own data (adapters)

You don't have to match our column names — map your vendor's export with the
`build-panel` / `build-membership` commands:

```bash
# Any long price CSV -> engine panel (remap your column names)
python -m rhbot build-panel --long-csv vendor_prices.csv \
    --date-col trade_date --symbol-col ric --price-col close_adj --out data/panel.csv

# A folder of dated holdings files (e.g. IWB_2025-01-31.csv) -> point-in-time membership
python -m rhbot build-membership --holdings-dir ./iwb_holdings --out data/membership.json
# ...or a single long CSV of date,ticker
python -m rhbot build-membership --long-csv constituents.csv --out data/membership.json
```

### Where the data realistically comes from

| need | free | bias-free (paid) |
|---|---|---|
| adjusted prices | this repo's broker pull, yfinance, Stooq | Polygon, Tiingo, Nasdaq Data Link |
| **point-in-time R1000 membership** | iShares IWB holdings = **current only**; or save monthly snapshots from today forward | Norgate (Russell historical constituents), CRSP, FTSE Russell |

**The honest gap:** there is no free source of *historical* point-in-time
Russell 1000 membership. Current IWB holdings give you today's list (fine for a
live rebalance, useless for a backtest of the past), and saving snapshots only
builds history going forward. A bias-free historical backtest needs a paid
constituents dataset. Until one is wired in, every backtest here — including the
66-name demo — is survivorship-biased and is **not** validation.

### Recommended free workflow (best you can do without paying)

1. **Get real *current* membership.** iShares bot-gates the download endpoint
   (an automated GET returns the product page, not the file), so download the
   holdings file from the iShares Russell 1000 page in a browser. The default
   **`.xls` (Excel 2003 XML) download works as-is**, as do `.xlsx` and `.csv` —
   all parsed natively (multiple worksheets, preamble rows, cash/futures lines,
   and iShares' raw-`&` malformed-XML quirk are all handled) — then:
   ```bash
   python -m rhbot snapshot-membership \
       --holdings-csv iSharesRussell1000ETF_fund.xls \
       --date 2026-06-16 --out data/membership.json   # -> 1003 real constituents
   ```
   A ready-made `data/membership.json` (the real R1000, as of 2026-06-15) and
   `data/r1000_tickers.txt` are already committed, so you can skip straight to
   the rebalance.
   This is bias-free for a *live* rebalance (it's the real index today) and
   seeds your point-in-time store.
2. **Accumulate history.** Re-run that snapshot command on a schedule (monthly).
   Over time `membership.json` becomes a genuine point-in-time record — the only
   free route to an eventually unbiased backtest.
3. **Get full prices, self-serve.** No broker/keys needed:
   ```bash
   python -m rhbot build-panel-yahoo --symbols-file data/r1000_tickers.txt \
       --start 2010-01-01 --out data/panel.csv      # needs: pip install yfinance
   ```
4. **Run it.** `rebalance` for today's vetted basket; `backtest` once you have
   real history. `data/membership.demo.json` is an example snapshot (the 66 demo
   names — a placeholder, not the real index).

## ⚠️ Read this before trusting any backtest number

**Do not believe these as validation.** They are a *smoke test of the engine*,
not a verdict on the strategies, because both demo fixtures are survivor-only,
static-membership, and only ~2 years long (momentum needs ~13 months of warmup,
so only ~12 real rebalances — a Sharpe from 12 points is statistically
meaningless).

Demo numbers through the full risk pipeline (5 bps/turnover costs):

| sleeve | 14-name mega-caps | 66-name cross-section |
|---|---|---|
| momentum (monthly) | Sharpe 1.08, DD −3.7% | Sharpe 1.02, DD −12.5% |
| mean-reversion (weekly) | Sharpe 1.11, DD −15.6% | **Sharpe 0.59, vol 34%, DD −27.5%** |

**The key finding is itself a warning about trusting backtests:** broadening the
universe from 14 to 66 names cut the mean-reversion Sharpe nearly *in half*
(1.11 → 0.59) and exposed a −27.5% drawdown at 34% annualized vol. The 14-name
large-cap set was *flattering* it. The broader number (~0.6) lands right in the
spec's own 0.5–0.7 target — but with brutal drawdowns that only showed up once
the cross-section was wider. The mega-cap momentum number is likewise survivor-
inflated (the "most popular" list is, by definition, today's winners).

A credible backtest needs the full point-in-time R1000 panel + membership.
Until then, the mean-reversion sleeve remains **un-validated** (its spec said
"BACKTEST PENDING" for a reason), and real capital beyond a token amount is not
justified. The momentum sleeve's "16-year, Sharpe 0.65" claim lives in the spec
and likewise needs to be reproduced on real data here before it's trusted.

## The bias-free path: the walk-forward paper ledger

The honest answer to the warning above. Instead of replaying the *past* (which
needs point-in-time membership nobody has for free), record each basket **as you
run it** and mark it forward:

```bash
# each rebalance: append the vetted basket (records the REAL index at that moment)
python -m rhbot paper-record --sleeves momentum:0.5,low_vol:0.5 \
    --members data/membership.json --panel data/panel.csv

# anytime: mark every recorded basket forward with realized prices
python -m rhbot paper-report --panel data/panel.csv
```

Because each entry uses the membership that was real *on its date*, the resulting
track record is **survivorship-free by construction** — no point-in-time history
required. It just takes calendar time to accumulate. `data/paper_ledger.json`
already holds the first real entry; append one each rebalance and the report
becomes a genuine, trustworthy verdict on the sleeves. This is the only free path
that turns the illustrative backtest Sharpes above into numbers you can believe.

**It runs itself.** `.github/workflows/monthly-paper-record.yml` fetches fresh
prices (Yahoo) and appends a combo basket on the 1st of each month, commits the
ledger, and logs the marked-forward performance — so the track record accrues
with no manual runs. (Needs Actions write permission: Settings → Actions →
Workflow permissions → Read and write.)

## Scale reality at $50

These are diversified *portfolio* sleeves (10 and 50 names). On $50 that's
$5/name and $1/name respectively — the risk layer will warn, and rightly so.
Diversified factor sleeves want four figures of capital to express cleanly.
Run them in `rebalance`/`backtest` (advisory/paper) until both the data and the
capital are real.
