# CHRONOS V8.11 — Fresh Forward Shadow

V8.11 freezes a CHRONOS model after a real public-market training window and then
starts a separate future window.

During the future window:
- the model is not retrained;
- a forecast is hash-registered before its outcome exists;
- every receipt has a 30-second horizon lock;
- realized price is attached only after the horizon expires;
- calibration is computed only from resolved receipts.

This produces true short-horizon fresh-forward shadow evidence rather than a
retrospective backtest. The window is intentionally small and is not sufficient for
profitability claims.
