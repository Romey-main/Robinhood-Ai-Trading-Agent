# Robinhood-Ai-Trading-Agent

## [`kalshi-arena/`](kalshi-arena/) — two Kalshi bots that compete and learn from each other

A paper-trading arena in which two prediction-market bots trade the same
markets round after round, are scored on money *and* forecasting *and*
execution, and exchange what they have learned between rounds under
statistical gating.

- **Bot A (`incumbent`)** is a faithful port of the existing `kalshi-bot`
  from `Romey-main/experimental1@claude/kalshi-bot-ipIYv`.
- **Bot B (`challenger`)** anchors its beliefs on the market price and moves
  off it only as far as its measured track record justifies.

Across 8 seeds × 8 rounds the challenger loses 4× less in efficient markets
(where the correct play is to not trade), and earns 7× more where prices are
genuinely mispriced. In a 10-round tournament with cross-learning enabled it
finished +$1,127 to −$82, winning 9 rounds of 10 — and taught the incumbent
maker-first execution along the way, while learning position sizing from it.

Paper only: there is no order-placement code in the package. No dependencies
beyond the standard library.

```bash
cd kalshi-arena
python3 -m arena.cli compete --rounds 10 --markets 200
python3 -m pytest -q
```

See [`kalshi-arena/README.md`](kalshi-arena/README.md) for the full design,
the ablation table, and a record of the modelling mistakes made along the way.

---

Other work in this repository lives on its own branches; see the open pull
requests.
