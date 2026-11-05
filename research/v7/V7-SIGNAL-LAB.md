# TriConsensus V7 Signal Lab

## Status

Research-only. No paper or live execution authority.

V7 is additive and does not modify the frozen V5.1 protocol, thresholds,
calibration hashes, forward experiment, V6 deterministic veto, or V6 reward
memory rules.

## Hypothesis

A single cross-venue contrarian edge is too narrow to carry all market states.
V7 tests independent edge families and lets market state decide which family is
eligible to propose a candidate.

## Independent signal species

1. **Trend / breakout**
   - EMA structure
   - ADX trend strength
   - multi-horizon momentum
   - MACD histogram
   - prior support/resistance breakout
   - volume confirmation

2. **Mean reversion / crowding**
   - range / weak-trend routing
   - Bollinger displacement
   - RSI / stochastic extremes
   - ATR-normalized displacement
   - optional funding, open-interest and long/short crowding confirmation

3. **Microstructure / order flow**
   - Bybit + Binance cross-venue depth imbalance
   - spread quality gate
   - directional agreement required
   - live-shadow only until sufficient historical order-book evidence exists

No historical order-book backtest is fabricated for species 3.

## Labels

Every historical price candidate is evaluated at fixed horizons:

- 30 minutes
- 60 minutes
- 180 minutes

All returns include the frozen 15 bps round-trip research cost. V7 also records
180-minute MAE and MFE.

## Validation

- Causal features only.
- Regime volatility thresholds fit on the initial calibration rows only.
- Maximum label horizon: 180 minutes.
- Time-based purge / embargo: 180 minutes between training and validation.
- Final 20% is held out from model selection.
- V7.0 meta-label baseline is fixed Logistic Regression.
- No hyperparameter search is permitted in V7.0.
- Candidate ranking does not grant execution authority.

Initial evidence gate:

- >= 30 selected holdout trades
- holdout hit rate >= 52%
- holdout mean net return >= +0.03%
- holdout profit factor >= 1.25
- >= 3 profitable walk-forward folds

Passing this research gate still does not enable paper or live trading. A future
preregistered forward shadow window is required before V7 can be considered for
paper-trade integration.

## Scientific boundary

The first historical V7 runs are development evidence, not final untouched
evidence, because the V7 design was informed by prior TriConsensus observations.
The protocol is frozen at `v7.0-signal-lab-research-1` before any future
forward V7 qualification window begins.
