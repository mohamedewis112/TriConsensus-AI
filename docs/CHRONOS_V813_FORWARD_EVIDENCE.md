# CHRONOS V8.13 — Forward Evidence Auditor

A high directional hit rate is not evidence when the realized market window is
one-sided. V8.13 audits fresh-forward receipts against an empirical base-rate
reference and removes overlapping forecast horizons before treating observations as
independent evidence.

A forward result is not qualified unless it has enough total forecasts, both realized
classes, enough non-overlapping horizons, excess accuracy over the empirical base rate,
and positive Brier skill. This explicitly rejects a 90%+ hit rate if an always-long or
always-short baseline would do as well or better.
