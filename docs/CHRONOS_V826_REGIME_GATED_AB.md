# CHRONOS V8.26 — Exact Regime-Gated ETH Timing A/B

V8.26 removes the forced-direction smoke.

The A/B test is allowed to run only when the latest closed ETH 15-minute bar produces
the exact frozen V7 research candidate:

- species: trend_breakout
- side: SHORT
- regime: trend_down|mid_vol|momentum_mixed

The gate is evaluated before and after the live microstructure capture. If the exact
candidate is absent, no A/B result is produced. If the gate changes during capture,
the episode is rejected.

When the gate remains valid, the same slow direction is compared:
A = no CHRONOS timing filter
B = CHRONOS order-flow / liquidity / spread timing-veto filter

The workflow is scheduled near each 15-minute boundary so eligible episodes can be
observed without forcing a signal. All results remain research-only.
