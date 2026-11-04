# CHRONOS V8.16 — Efficient Public Live Source

Live shadow now uses:
- OKX perpetual 20-level L2 for depth, spread and order-flow imbalance;
- OKX funding, open interest and candles for derivatives context;
- Coinbase spot ticker only as the independent spot reference for perp/spot basis.

Coinbase full Level-2 is intentionally not polled every second. It is expensive and
its depth coverage is not directly comparable to OKX contract depth. This keeps the
feature semantics clean and removes a major live-runtime bottleneck.

All evidence remains research-only with zero paper/live execution authority.
