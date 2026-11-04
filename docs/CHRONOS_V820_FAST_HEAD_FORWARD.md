# CHRONOS V8.20 — Fast Head Fresh Forward

This experiment freezes the 1-second transition model after a real public-market
training window, then measures the fast heads on a new future window.

Baselines are explicit:
- return head vs zero-return;
- order-flow head vs current-order-flow persistence;
- liquidity-change head vs zero-change.

The fast execution layer qualifies only if BOTH order-flow and liquidity heads:
- have at least 50 fresh-forward samples;
- beat their naive RMSE baselines;
- retain >=72% interval coverage;
- have positive prediction/realization correlation.

The price head is reported but cannot create direction. All evidence remains
research/shadow only.
