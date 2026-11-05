# V6 Shadow Workers

This deployment package runs the V6 data/learning workers without enabling enforcement.

## Safety invariants

- `TRADING_MODE=paper`
- `V6_MARL_OBSERVE=true`
- `V6_MARL_ENABLED=false`
- `V6_MARL_SHADOW_MODE=true`
- No live-money execution
- No OpenRouter key is required by these two workers
- The deterministic risk engine remains authoritative in the paper decision path
- The policy worker cannot produce training updates before the configured warm-up

## Services

### v6-microstructure

Runs `python -m triconsensus.v6_microstructure_worker` continuously and persists throttled Bybit/Binance order-book evidence to Supabase.

### v6-policy

Runs `python -m triconsensus.v6_policy_worker` repeatedly. The worker consumes the service-role-only PGMQ reward queue. Before warm-up it records/archives a skipped update. After warm-up it bootstraps the conservative safe prior before any PPO update.

## Required runtime secrets

Set these only in the runtime environment, never commit them:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

## Start

From the repository root:

```bash
docker compose -f deploy/docker-compose.v6-shadow.yml up -d --build
```

Inspect:

```bash
docker compose -f deploy/docker-compose.v6-shadow.yml ps
docker compose -f deploy/docker-compose.v6-shadow.yml logs -f v6-microstructure
docker compose -f deploy/docker-compose.v6-shadow.yml logs -f v6-policy
```

Stop:

```bash
docker compose -f deploy/docker-compose.v6-shadow.yml down
```

Do not change `V6_MARL_ENABLED` to true as part of deployment. Enforcement requires a separate scientific promotion decision after sufficient shadow evidence.


## Lightweight free-tier collector

For always-on evidence collection without Torch, use:

- `deploy/v6-shadow-collector.Dockerfile`
- `.railway/railway.ts`
- `python -m triconsensus.v6_shadow_collector`

It collects:

- Bybit + Binance cross-venue order-book microstructure for BTC/ETH
- Bybit front-expiry near-ATM option mark IV for BTC/ETH every `V6_IV_POLL_SECONDS`

The collector has **no trading authority** and refuses to start if V6 enforcement is enabled. It requires only `SUPABASE_SERVICE_ROLE_KEY` as a runtime secret.


## Supabase shadow action observer

While a dedicated Python/Torch runtime is unavailable, Supabase also runs an observation-only layer:

- Edge Function: `v6-shadow-observer`
- Cron: `:12/:27/:42/:57` UTC, one minute after the V5.1 recorder cadence
- Reads only recent frozen V5.1 LONG/SHORT signals
- Builds warm-up portfolio state from immutable reward memory
- Applies the same symbolic Dynamic Risk baseline/projection used before MAPPO is ready
- Writes only `marl_action_logs`
- Never opens/closes paper trades and never writes reward memory
- Never invokes an LLM
- Stops automatically if an active `mappo_shared` policy appears, because neural inference then belongs to the Python/Torch runtime

The observer is authenticated with a separate random token stored in Supabase Vault. Its action rows are shadow observations only and cannot alter the frozen V5.1 protocol or deterministic risk authority.


## Vercel research runtime region

The isolated `triconsensus-runtime-preview` FastAPI project uses the `cdg1` function region for the read-only research runtime. This avoids the Binance Futures geo restriction observed from `iad1` while preserving the frozen V5.1 protocol. Region selection is an infrastructure setting only; it must not change thresholds, lag, horizon, cost, or signal semantics.


## Parallel V6 shadow learning

V5.1 remains the frozen scientific baseline. V6 now learns in parallel without
changing V5.1 decisions or the deterministic risk envelope.

Activation rules:

- Learning starts from the immutable activation timestamp stored in
  `v6_shadow_learning_control`.
- Historical V5.1 forward outcomes from before activation are forbidden as
  training rewards.
- Only new V5.1 `LONG`/`SHORT` signals with V6 shadow action logs can enter
  the replay dataset.
- BTC and ETH use isolated replay partitions and isolated learned safe-prior
  versions.
- A transition is evaluated only after its frozen 180-minute horizon has fully
  elapsed plus the candle-ingestion guard.
- Counterfactual outcomes are stored in
  `v6_shadow_learning_outcomes`, never in `marl_reward_memory`.
- `marl_reward_memory` remains reserved for actual closed paper trades.

Warm-up and learning:

- Minimum warm-up is 20 evaluated transitions per asset.
- Before warm-up, the existing neutral/symbolic shadow actions remain active.
- At 20+ transitions, a beta-smoothed half-Kelly safe prior is produced per
  asset. It can only cap/reduce the portfolio weight in shadow mode.
- The learned safe prior never raises a weight above the existing base risk
  envelope and may reduce it to zero.
- The risk actor is not trained from fixed-horizon counterfactual outcomes;
  risk learning requires real closed paper-trade rewards.
- MAPPO safe-prior initialization accepts the learned BTC/ETH caps so later PPO
  training starts from the learned conservative prior rather than a fixed
  95%/95% allocation.

Schedules:

- Shadow transition capture: `:04/:19/:34/:49`
- Horizon-safe evaluator: `:05/:20/:35/:50`
- Per-asset safe-prior updater: `:06/:21/:36/:51`

This layer has no trading authority and V6 enforcement remains disabled.


## Adaptive V2 rolling/regime safe prior

Adaptive V2 is a shadow-only successor to the cumulative half-Kelly cap. V5.1
remains frozen and the deterministic risk engine retains final authority.

The V2 activation boundary is stored in `v6_shadow_adaptive_control`. Data
before activation may seed the policy as bootstrap training data, but V2
validation metrics include only actions created after activation. No look-ahead
or historical rewrite is allowed.

Policy behavior:

- BTC and ETH remain isolated.
- The symbol rolling window is 30 evaluated outcomes.
- Regime policies use trend plus the IV/realized-volatility bucket.
- A regime needs at least 8 evaluated outcomes; otherwise the policy falls back
  to the last 20 symbol outcomes.
- Allowed relative exposure is discrete: `0, 0.25, 0.50, 0.75, 1.00`.
- V2 can never exceed the V5 base risk envelope.
- Hysteresis limits each policy update to one 0.25 step.
- Three consecutive losses can tighten the target to at most 0.25.
- Opportunity-cost recovery raises the target when recent completed outcomes
  show 3/4 or 4/6 profitable observations with positive cumulative net return.
- Counterfactual outcomes never train the risk actor and never enter
  `marl_reward_memory`.

The observer reads `v6_shadow_regime_policy_versions` and records
`policy_version=v6-shadow-adaptive-v2` in new shadow actions. The old V1
policy tables are retained only for audit/comparison.

V2 scorecards are append-only and track baseline/shadow profit factor, maximum
drawdown, losses avoided, winners missed, average exposure and exposure
efficiency. Scorecards begin only after the V2 activation boundary.

Schedules:

- Adaptive policy update: `:07/:22/:37/:52`
- Adaptive validation scorecard: `:08/:23/:38/:53`

Future MAPPO bootstrap reads the latest symbol-level V2 caps only after genuine
closed paper-trade reward memory completes warm-up. The risk actor is still
trained only from genuine closed paper-trade rewards.
