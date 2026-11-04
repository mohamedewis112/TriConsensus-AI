# CHRONOS V8.19 — WebSocket Market Nervous System

V8.19 is a research-only challenger to the 1-second REST snapshot collector.

Public OKX WebSocket feeds:
- books5 for BTC-USDT-SWAP and ETH-USDT-SWAP;
- trades for BTC-USDT-SWAP and ETH-USDT-SWAP.

Features:
- spread;
- microprice displacement from mid;
- L1 and L5 depth imbalance;
- book-pressure slope;
- taker buy/sell imbalance over 1s and 5s;
- trade intensity over 1s and 5s;
- signed trade-contract flow.

The layer is intentionally separate from the current learned model. It must prove feed
stability first, then it can become a challenger feature source.

No credentials, no order placement, research-only, paper/live authority false.
