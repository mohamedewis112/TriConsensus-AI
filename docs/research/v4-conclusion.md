# V4 Research Conclusion — Rejected

Status: **REJECTED / NO_TRADE**

This document freezes the V4 hypothesis family so later work does not silently
retune thresholds after seeing results.

## What was tested

- Venue/data source: Bybit public historical market data.
- Symbols: BTCUSDT and ETHUSDT.
- Primary timeframe: 15m.
- Trading cost assumption: 15 bps round trip.
- Fail-closed default: NO_TRADE.
- Evidence gate: minimum sample size, chronological fold stability, positive
  mean net return, positive bootstrap lower confidence bound, and multi-period
  coverage.
- Frozen deterministic setup version: v4.1-frozen-quant-setups.
- Setup families:
  - trend_continuation
  - trend_pullback
  - range_reversion
  - breakout

## V4 regime-only discovery

BTC 2024 (300 days): **0 qualified regimes**.

ETH clean 2024 window (244 days, Apr-Nov, 100% coverage):
**0 qualified regimes**.

BTC recent 2026 window: **0 qualified regimes**.

## V4.1 setup x regime discovery

BTC 2024:
- 2,874 setup signals.
- **0 qualified setup/regime pairs**.
- Best-looking pair was trend_pullback SHORT inside
  trend_down|mid_vol|momentum_down:
  35 trades, 60% hit rate, PF ~1.47, mean net ~+0.0875%.
  Rejected because only 2/4 folds were profitable and bootstrap LCB was
  negative.

ETH clean 2024:
- 2,604 setup signals.
- **0 qualified setup/regime pairs**.
- Several small-sample positive-looking slices existed, but none passed the
  sample-size + bootstrap + fold-stability gate.

Independent 2023 replication:
- BTCUSDT: **0 qualified**.
- ETHUSDT: **0 qualified**.

## V4.2 frozen-entry event study

Purpose: separate entry quality from the fixed SL/TP exit rule.

Horizons tested without changing the frozen setup definitions:
- 4 candles = 1h
- 12 candles = 3h
- 24 candles = 6h
- 48 candles = 12h

Event windows are non-overlapping within each setup+side and net results charge
15 bps round-trip cost.

BTC 2024:
- 4,492 event observations.
- **0 qualified setup/horizon pairs**.
- breakout LONG H24: 125 events, mean net ~+0.140%, PF ~1.31,
  3/4 profitable folds; rejected because worst fold and bootstrap LCB were
  negative.
- breakout LONG H48: 125 events, mean net ~+0.172%, PF ~1.28,
  3/4 profitable folds; rejected because worst fold and bootstrap LCB were
  negative.

ETH clean 2024:
- 4,112 event observations.
- **0 qualified setup/horizon pairs**.
- range_reversion SHORT H4: 24 events, mean net ~+0.224%, PF ~2.42,
  4/4 profitable folds; rejected because sample size is below the frozen gate
  and bootstrap LCB remained negative.
- range_reversion SHORT H12: 24 events, mean net ~+0.274%, PF ~2.11,
  3/4 profitable folds; rejected for the same reasons.

## Decision

Do **not** loosen thresholds, reduce costs, lower sample-size requirements, or
retune the four frozen setups based on these results.

Do **not** train ML to rescue this rejected signal family.

V4 remains available only as negative evidence and regression coverage.
Production/paper decision policy remains **NO_TRADE** for V4.

## Next research family

V5 must test a genuinely different information set rather than more OHLCV
threshold tuning:

- derivatives state: funding, open interest and basis/premium where historical
  data quality permits;
- market microstructure/order-flow evidence where historical coverage permits;
- higher-timeframe context aligned with 15m decisions;
- strictly pre-registered hypotheses and disjoint out-of-sample replication.

Any V5 signal must pass the same fail-closed principles before it can become
paper-trading eligible.
