begin;

create table public.strategy_calibrations (
  id uuid primary key default gen_random_uuid(),
  strategy_version text not null,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  feature_name text not null,
  relationship text not null check (relationship in ('positive','negative')),
  low_threshold numeric not null,
  high_threshold numeric not null,
  calibration_start date not null,
  calibration_end date not null,
  horizon_minutes integer not null check (horizon_minutes > 0),
  context_lag_minutes integer not null check (context_lag_minutes >= 15),
  round_trip_cost numeric not null check (round_trip_cost >= 0),
  source text not null,
  quality jsonb not null default '{}'::jsonb,
  config_hash text not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  check (low_threshold < high_threshold),
  check (calibration_start <= calibration_end),
  unique (strategy_version, symbol, calibration_end, config_hash)
);

create table public.strategy_signal_snapshots (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  market_snapshot_id uuid references public.market_snapshots(id) on delete restrict,
  calibration_id uuid not null references public.strategy_calibrations(id) on delete restrict,
  strategy_version text not null,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  observed_at timestamptz not null,
  context_timestamp timestamptz not null,
  feature_name text not null,
  feature_value numeric,
  low_threshold numeric not null,
  high_threshold numeric not null,
  signal text not null check (signal in ('LONG','SHORT','NO_TRADE')),
  context_age_ms bigint not null check (context_age_ms >= 0),
  reason text not null,
  payload jsonb not null default '{}'::jsonb,
  checksum text not null,
  created_at timestamptz not null default now(),
  check (low_threshold < high_threshold)
);

create index idx_strategy_calibrations_lookup
  on public.strategy_calibrations(strategy_version, symbol, calibration_end desc, created_at desc);

create index idx_strategy_signal_snapshots_exp_symbol_time
  on public.strategy_signal_snapshots(experiment_id, symbol, observed_at desc);

alter table public.strategy_calibrations enable row level security;
alter table public.strategy_signal_snapshots enable row level security;

revoke all on public.strategy_calibrations from anon, authenticated;
revoke all on public.strategy_signal_snapshots from anon, authenticated;

grant select, insert on public.strategy_calibrations to service_role;
grant select, insert on public.strategy_signal_snapshots to service_role;

create policy "deny_client_access_strategy_calibrations"
on public.strategy_calibrations
as restrictive
for all
to anon, authenticated
using (false)
with check (false);

create policy "deny_client_access_strategy_signal_snapshots"
on public.strategy_signal_snapshots
as restrictive
for all
to anon, authenticated
using (false)
with check (false);

create trigger trg_strategy_calibrations_immutable
before update or delete on public.strategy_calibrations
for each row execute function internal.reject_mutation();

create trigger trg_strategy_signal_snapshots_immutable
before update or delete on public.strategy_signal_snapshots
for each row execute function internal.reject_mutation();

commit;
