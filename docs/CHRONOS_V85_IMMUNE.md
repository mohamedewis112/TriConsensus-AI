# CHRONOS V8.5 — Alpha Immune System

V8.5 turns causal stress testing into an explicit lifecycle for research alphas.

## Vaccination

An alpha is evaluated under controlled interventions. CHRONOS records which stresses
preserve its signed edge and which cause it to collapse or reverse.

## Failure signatures

Every causal failure receives a stable fingerprint containing:
- alpha id,
- intervention,
- affected factors,
- base/stressed research score,
- intervention severity,
- sign-flip status.

Repeated fingerprints become immune memory.

## Minimal causal repair

The repair engine does not retrain the alpha automatically. It proposes the smallest
deterministic guard that covers observed failure signatures, such as order-flow
alignment, liquidity floor, funding/crowding guard, liquidation guard or cross-venue
freshness.

Every repair remains research-only until separate fresh-forward validation.
