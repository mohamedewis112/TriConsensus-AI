# CHRONOS V8.4 — Causal Market Memory

V8.4 gives CHRONOS memory of recurring market states without reducing the world to a
small set of regime labels.

The memory index stores past state -> next-state episodes and retrieves only episodes
strictly older than the current query timestamp.

## Recall output

- nearest historical state analogues;
- recurrence score;
- weighted next return / order flow / liquidity change;
- dispersion of historical outcomes;
- recurring latent-actor fingerprints;
- optional per-alpha historical score and survival rate.

## Leakage rule

A state at or after the query timestamp is excluded before similarity ranking. This is
a hard invariant so the same memory engine can be used in historical replay and live
shadow research without future contamination.

## Authority

Memory can rank research hypotheses only. It has no paper/live authority.
