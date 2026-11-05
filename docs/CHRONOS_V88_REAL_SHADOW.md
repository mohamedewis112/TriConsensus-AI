# CHRONOS V8.8 — Real Shadow Evidence Run

This workflow is the first end-to-end CHRONOS run intended to consume existing real
V6 cross-venue microstructure evidence rather than synthetic smoke data.

For BTC and ETH it:
- reads up to 120 hours of Bybit + Binance V6 microstructure history;
- aligns it with public Bybit 1-minute candles;
- trains the V8.1 model chronologically;
- reports the untouched evaluation slice;
- builds leakage-safe market memory;
- runs 100,000 learned future universes for the latest replay state;
- emits a JSON evidence artifact.

The workflow fails closed when no Supabase backend credential is available.
It cannot place trades and keeps paper/live authority false.
