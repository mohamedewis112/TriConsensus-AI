# CHRONOS V8.1 — Learned Market Dynamics

V8.1 keeps the V8.0 safety contract and adds a data-driven layer without granting
paper or live trading authority.

## Added

- Chronological transition dataset: state -> next return / next order flow /
  next liquidity change.
- Probabilistic ridge bootstrap ensemble with multiple heads.
- 70/15/15 chronological split:
  - first 70% training,
  - next 15% split-conformal uncertainty calibration,
  - final 15% untouched evaluation.
- Conformal forecast bands instead of raw point confidence.
- Sequential residual drift detector that fails the research gate closed.
- Adaptive Digital-Mind weighting: behavioural hypotheses are reweighted by how well
  they explain realised next order flow; this is not identity attribution.
- Explicit learning safety gate with zero paper/live authority.

## Why this matters

V8.0 contains transparent behavioural priors and a transparent world simulator.
V8.1 creates the first replaceable learned dynamics layer and quantifies when it should
not be trusted. A model that is uncalibrated, under-sampled, or drifting cannot become
research-ready.

## Still not claimed

- Digital Minds are not verified real-world identities.
- Synthetic smoke performance is not evidence of market predictability.
- CHRONOS has not yet demonstrated live shadow calibration.
- No simulated forecast is allowed into MARL reward memory.
