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
