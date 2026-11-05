# CHRONOS V8.17 — Statistical Forward Evidence

Forward evidence now has two explicit tiers.

1. Provisional forward signal
   - enough total forecasts,
   - both outcome classes represented,
   - minimum non-overlapping horizons,
   - beats empirical directional base rate,
   - positive Brier skill.

2. Statistically qualified forward evidence
   - all provisional gates pass,
   - at least 20 non-overlapping horizons by default,
   - one-sided exact binomial test beats the empirical base-rate accuracy at p < 0.05.

Eight non-overlapping horizons can therefore validate pipeline behavior and produce a
provisional signal, but can never be called final evidence.

All results remain research-only.
