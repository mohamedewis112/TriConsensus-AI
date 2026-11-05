# CHRONOS V8.6 — Research Firewall

The Research Firewall exists to stop CHRONOS from promoting a strategy merely because
one backtest looks exceptional.

## Gates

- probability of backtest overfitting (CSCV/PBO);
- deflated Sharpe probability adjusted for the number and dispersion of trials;
- parameter-plateau robustness instead of a single magic parameter;
- positive net expectancy.

A candidate that fails any configured gate remains a research hypothesis.

The firewall itself has no paper/live authority.
