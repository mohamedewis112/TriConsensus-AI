# CHRONOS V8.3 — Real V6 Replay Bridge

V8.3 removes the need for invented historical L2 labels.

It reads the already-collected V6 microstructure snapshot evidence from Bybit and
Binance, aligns those minute buckets with public Bybit 1-minute candles, constructs
real CHRONOS MarketState sequences, then derives state -> next-state transitions.

## Fail-closed rules

- both Bybit and Binance must exist in a minute before it becomes a state;
- at least 180 aligned transitions are required for V8.1 training;
- alignment ratio must be >= 70%;
- the bridge is read-only against V6 evidence;
- funding/OI/liquidations/alternative fields that are not present in V6 history are
  explicitly zero/missing rather than fabricated;
- real replay does not write MARL reward memory;
- paper/live authority remain false.

## Why this is important

V8.0 and V8.2 can run synthetic smoke worlds to verify architecture, but V8.3 provides
a path to train and calibrate the same V8.1 model on actual cross-venue microstructure
already captured by TriConsensus.
