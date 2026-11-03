# CHRONOS V8 — Artificial Market Civilization (Research Only)

CHRONOS is a new research layer above the frozen V5.1 control, V6 adaptive risk layer,
and V7 Signal Lab. It does not alter those systems and has zero paper/live execution
authority.

## Core thesis

Price is treated as the delayed visible consequence of interacting participant intents.
CHRONOS therefore models the actors and their future reactions rather than asking one
model for a direct next-price forecast.

## V8.0 research kernel

1. **Market Nervous System**
   - Unified decision-time state for L2 depth, order flow, spread, funding, OI,
     liquidations, cross-venue basis/latency, stablecoin-flow proxy, dealer gamma,
     mempool pressure and crowding.
   - Existing V6 collectors can feed this state; future collectors can add DEX/on-chain
     sources without changing the safety contract.

2. **Digital Minds**
   - Explicit behavioural proxies for momentum funds, market makers, arbitrage,
     options dealers, liquidation engines, whales/capital flow, DEX LPs and retail.
   - Every contribution exposes pressure, confidence, latency and rationale.

3. **Intent Field**
   - Aggregates actor pressure into an explainable future-order-flow field.
   - Reports uncertainty, confidence, dominant actor and expected aggressive-flow share.

4. **Latent Actors**
   - Robust residual detector searches for persistent order flow not explained by known
     Digital Minds and assigns a stable latent-actor fingerprint.

5. **Parallel Futures**
   - Vectorized world engine can run thousands to 100k+ research universes.
   - Produces a probability landscape rather than a single-point forecast.
   - Future Diversity estimates whether consensus comes from genuinely different paths.

6. **Action / No-Action Twins**
   - Uses identical random worlds for a no-trade universe and a hypothetical-action
     universe.
   - Measures self-impact separately from market movement and charges frozen research
     cost on hypothetical action worlds.

7. **Causal Survival Passport**
   - Stress interventions mutate liquidity, order flow, funding/crowding, liquidations
     and cross-venue dislocations.
   - Records survival rate and the first/fatal failure signatures.
   - A passport is evidence only; never an execution permission.

8. **Market Physics Miner**
   - Mines transparent candidate relationships and interaction laws.
   - Requires support and sign stability across contiguous folds.
   - Discovered laws are hypotheses only and must later pass purged validation,
     fresh-forward shadow evidence, intervention testing and cost-aware evaluation.

## Non-negotiable safety

- research_only = true
- paper_trading_authority = false
- live_trading_authority = false
- no exchange credentials
- no order placement
- no V5.1 mutation
- no MAPPO reward injection from simulated futures
- no simulated/counterfactual result may be written as real reward memory

## Next scientific stages

V8.0 establishes architecture and invariants with a transparent world kernel.
V8.1 should calibrate the world model against live shadow microstructure and test
forecast calibration. V8.2 can introduce learned actor/world models. Only after fresh
forward evidence should CHRONOS be allowed to influence V6 sizing in shadow mode.
