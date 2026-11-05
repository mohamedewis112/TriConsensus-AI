begin;

create schema if not exists internal;

create table public.experiments (
  id uuid primary key default gen_random_uuid(),
  version text not null unique,
  status text not null default 'draft'
    check (status in ('draft','backtest','forward_test','completed','failed','archived')),
  execution_mode text not null default 'paper'
    check (execution_mode in ('paper','dry_run','demo')),
  config jsonb not null default '{}'::jsonb,
  config_hash text not null,
  notes text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  ended_at timestamptz
);

create table public.market_snapshots (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  primary_timeframe text not null default '15m' check (primary_timeframe = '15m'),
  captured_at timestamptz not null,
  source text not null default 'bybit',
  market_data jsonb not null,
  orderbook jsonb,
  funding_rate numeric,
  open_interest numeric,
  market_data_age_ms integer not null default 0 check (market_data_age_ms >= 0),
  checksum text,
  created_at timestamptz not null default now()
);

create table public.feature_snapshots (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  market_snapshot_id uuid not null references public.market_snapshots(id) on delete restrict,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  feature_version text not null,
  features jsonb not null,
  leakage_guard jsonb not null default '{}'::jsonb,
  calculated_at timestamptz not null default now()
);

create table public.ml_predictions (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  market_snapshot_id uuid not null references public.market_snapshots(id) on delete restrict,
  model_family text not null,
  model_version text not null,
  target text not null,
  prediction jsonb not null,
  confidence numeric check (confidence is null or (confidence >= 0 and confidence <= 1)),
  created_at timestamptz not null default now()
);

create table public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  market_snapshot_id uuid not null references public.market_snapshots(id) on delete restrict,
  agent_role text not null check (agent_role in ('market_intelligence','trade_strategist','risk_advocate')),
  provider text not null,
  model_id text not null,
  prompt_version text not null,
  input_context_hash text not null,
  output_json jsonb,
  decision text,
  confidence numeric check (confidence is null or (confidence >= 0 and confidence <= 1)),
  evidence jsonb not null default '[]'::jsonb,
  is_valid boolean not null default false,
  error_code text,
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  created_at timestamptz not null default now()
);

create table public.consensus_decisions (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  market_snapshot_id uuid not null references public.market_snapshots(id) on delete restrict,
  market_agent_run_id uuid references public.agent_runs(id) on delete restrict,
  strategist_agent_run_id uuid references public.agent_runs(id) on delete restrict,
  risk_agent_run_id uuid references public.agent_runs(id) on delete restrict,
  outcome text not null check (outcome in ('LONG','SHORT','NO_TRADE')),
  full_consensus boolean not null default false,
  reason text not null,
  created_at timestamptz not null default now(),
  check ((outcome = 'NO_TRADE') or full_consensus = true)
);

create table public.risk_evaluations (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  consensus_decision_id uuid not null unique references public.consensus_decisions(id) on delete restrict,
  approved boolean not null,
  checks jsonb not null,
  proposed_entry numeric,
  stop_loss numeric,
  take_profit_1 numeric,
  take_profit_2 numeric,
  risk_reward numeric,
  risk_fraction numeric,
  position_size numeric,
  max_exposure_after numeric,
  spread_bps numeric,
  slippage_bps numeric,
  rejection_reason text,
  created_at timestamptz not null default now()
);

create table public.paper_trades (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  consensus_decision_id uuid not null unique references public.consensus_decisions(id) on delete restrict,
  risk_evaluation_id uuid not null unique references public.risk_evaluations(id) on delete restrict,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  side text not null check (side in ('LONG','SHORT')),
  status text not null default 'pending'
    check (status in ('pending','open','closed','cancelled','rejected')),
  requested_entry numeric not null,
  actual_entry numeric,
  stop_loss numeric not null,
  take_profit_1 numeric,
  take_profit_2 numeric,
  position_size numeric not null check (position_size > 0),
  notional_value numeric,
  estimated_fees numeric not null default 0,
  realized_fees numeric not null default 0,
  estimated_slippage numeric not null default 0,
  realized_slippage numeric not null default 0,
  pnl_gross numeric,
  pnl_net numeric,
  mae numeric,
  mfe numeric,
  opened_at timestamptz,
  closed_at timestamptz,
  close_reason text,
  created_at timestamptz not null default now()
);

create table public.trade_events (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  trade_id uuid not null references public.paper_trades(id) on delete restrict,
  event_type text not null,
  event_time timestamptz not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.system_events (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid references public.experiments(id) on delete restrict,
  severity text not null check (severity in ('info','warning','error','critical')),
  component text not null,
  event_type text not null,
  message text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index idx_market_snapshots_exp_symbol_time
  on public.market_snapshots(experiment_id, symbol, captured_at desc);
create index idx_feature_snapshots_market
  on public.feature_snapshots(market_snapshot_id);
create index idx_ml_predictions_market
  on public.ml_predictions(market_snapshot_id);
create index idx_agent_runs_market_role
  on public.agent_runs(market_snapshot_id, agent_role, created_at desc);
create index idx_consensus_exp_time
  on public.consensus_decisions(experiment_id, created_at desc);
create index idx_paper_trades_exp_status
  on public.paper_trades(experiment_id, status, created_at desc);
create index idx_trade_events_trade_time
  on public.trade_events(trade_id, event_time);
create index idx_system_events_exp_time
  on public.system_events(experiment_id, created_at desc);

create or replace function internal.reject_mutation()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public, internal
as $$
begin
  raise exception 'immutable audit record: % cannot be updated or deleted', tg_table_name;
end;
$$;

create or replace function internal.protect_experiment_config()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public, internal
as $$
begin
  if new.config is distinct from old.config
     or new.config_hash is distinct from old.config_hash
     or new.execution_mode is distinct from old.execution_mode
     or new.version is distinct from old.version then
    raise exception 'experiment configuration is immutable';
  end if;
  return new;
end;
$$;

create trigger trg_market_snapshots_immutable
before update or delete on public.market_snapshots
for each row execute function internal.reject_mutation();

create trigger trg_feature_snapshots_immutable
before update or delete on public.feature_snapshots
for each row execute function internal.reject_mutation();

create trigger trg_ml_predictions_immutable
before update or delete on public.ml_predictions
for each row execute function internal.reject_mutation();

create trigger trg_agent_runs_immutable
before update or delete on public.agent_runs
for each row execute function internal.reject_mutation();

create trigger trg_consensus_decisions_immutable
before update or delete on public.consensus_decisions
for each row execute function internal.reject_mutation();

create trigger trg_risk_evaluations_immutable
before update or delete on public.risk_evaluations
for each row execute function internal.reject_mutation();

create trigger trg_trade_events_immutable
before update or delete on public.trade_events
for each row execute function internal.reject_mutation();

create trigger trg_system_events_immutable
before update or delete on public.system_events
for each row execute function internal.reject_mutation();

create trigger trg_experiments_config_immutable
before update on public.experiments
for each row execute function internal.protect_experiment_config();

alter table public.experiments enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.feature_snapshots enable row level security;
alter table public.ml_predictions enable row level security;
alter table public.agent_runs enable row level security;
alter table public.consensus_decisions enable row level security;
alter table public.risk_evaluations enable row level security;
alter table public.paper_trades enable row level security;
alter table public.trade_events enable row level security;
alter table public.system_events enable row level security;

revoke all on table
  public.experiments,
  public.market_snapshots,
  public.feature_snapshots,
  public.ml_predictions,
  public.agent_runs,
  public.consensus_decisions,
  public.risk_evaluations,
  public.paper_trades,
  public.trade_events,
  public.system_events
from anon, authenticated;

grant select, insert, update, delete on table
  public.experiments,
  public.paper_trades
to service_role;

grant select, insert on table
  public.market_snapshots,
  public.feature_snapshots,
  public.ml_predictions,
  public.agent_runs,
  public.consensus_decisions,
  public.risk_evaluations,
  public.trade_events,
  public.system_events
to service_role;

commit;
