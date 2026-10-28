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
