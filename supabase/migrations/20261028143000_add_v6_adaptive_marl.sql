-- V6 Adaptive MARL reward memory, policy audit, and microstructure evidence.
-- Paper/shadow only. RLS stays closed; no anon/authenticated table policies are created.

create extension if not exists pgmq;

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create table if not exists public.marl_reward_memory (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid references public.experiments(id),
  trade_id uuid not null references public.paper_trades(id),
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  policy_version text not null,
  state_json jsonb not null,
  action_json jsonb not null,
  net_return numeric not null,
  max_drawdown numeric not null check (max_drawdown between 0 and 1),
  reward numeric not null,
  terminal boolean not null default true,
  checksum text not null unique,
  created_at timestamptz not null default now(),
  constraint marl_reward_memory_trade_id_key unique(trade_id)
);
alter table public.marl_reward_memory enable row level security;

create table if not exists public.marl_action_logs (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid references public.experiments(id),
  market_snapshot_id uuid references public.market_snapshots(id),
  signal_snapshot_id uuid references public.strategy_signal_snapshots(id),
  agent_role text not null check (agent_role in ('portfolio','dynamic_risk')),
  policy_version text not null,
  state_json jsonb not null,
  action_json jsonb not null,
  projected_action_json jsonb not null,
  shadow_mode boolean not null default true,
  vetoed_by_risk boolean not null default false,
  created_at timestamptz not null default now(),
  constraint marl_action_logs_signal_agent_key unique(signal_snapshot_id, agent_role)
);
alter table public.marl_action_logs enable row level security;

create table if not exists public.marl_policy_versions (
  id uuid primary key default gen_random_uuid(),
  agent_role text not null check (agent_role in ('mappo_shared','portfolio','dynamic_risk')),
  version text not null,
  status text not null check (status in ('candidate','active','rejected')),
  weights jsonb not null,
  training_sample_count integer not null check (training_sample_count >= 0),
  config_hash text not null,
  metrics jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(agent_role, version)
);
alter table public.marl_policy_versions enable row level security;

create table if not exists public.marl_policy_update_runs (
  id uuid primary key default gen_random_uuid(),
  agent_role text not null,
  source_queue_msg_id bigint,
  source_reward_id uuid references public.marl_reward_memory(id),
  prior_policy_version text,
  candidate_policy_version text,
  sample_count integer not null default 0,
  status text not null check (status in ('started','trained','skipped','failed')),
  metrics jsonb not null default '{}'::jsonb,
  error_code text,
  created_at timestamptz not null default now()
);
alter table public.marl_policy_update_runs enable row level security;

create table if not exists public.v6_microstructure_snapshots (
  id bigserial primary key,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  venue text not null check (venue in ('bybit','binance')),
  captured_at timestamptz not null,
  exchange_timestamp_ms bigint not null,
  received_timestamp_ms bigint not null,
  sequence bigint,
  bid_depth numeric not null check (bid_depth >= 0),
  ask_depth numeric not null check (ask_depth >= 0),
  imbalance numeric not null check (imbalance between -1 and 1),
  spread_bps numeric not null check (spread_bps >= 0),
  payload jsonb not null default '{}'::jsonb,
  checksum text not null,
  created_at timestamptz not null default now(),
  unique(venue, symbol, exchange_timestamp_ms, sequence)
);
alter table public.v6_microstructure_snapshots enable row level security;

create table if not exists public.v6_volatility_snapshots (
  id bigserial primary key,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  source text not null,
  captured_at timestamptz not null,
  mark_iv numeric not null check (mark_iv >= 0),
  expiry timestamptz,
  sample_count integer not null default 0 check (sample_count >= 0),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
alter table public.v6_volatility_snapshots enable row level security;

create index if not exists marl_reward_memory_symbol_created_idx
  on public.marl_reward_memory(symbol, created_at desc);
create index if not exists marl_action_logs_experiment_created_idx
  on public.marl_action_logs(experiment_id, created_at desc);
create index if not exists marl_policy_versions_role_created_idx
  on public.marl_policy_versions(agent_role, created_at desc);
create index if not exists marl_policy_update_runs_created_idx
  on public.marl_policy_update_runs(created_at desc);
create index if not exists v6_microstructure_symbol_venue_time_idx
  on public.v6_microstructure_snapshots(symbol, venue, captured_at desc);
create index if not exists v6_volatility_symbol_time_idx
  on public.v6_volatility_snapshots(symbol, captured_at desc);

create or replace function private.reject_append_only_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception '% is append-only', tg_table_name;
end;
$$;
revoke all on function private.reject_append_only_mutation()
  from public, anon, authenticated;

do $$
declare t text;
begin
  foreach t in array array[
    'marl_reward_memory',
    'marl_action_logs',
    'marl_policy_versions',
    'marl_policy_update_runs'
  ]
  loop
    execute format('drop trigger if exists %I_no_update on public.%I', t, t);
    execute format(
      'create trigger %I_no_update before update on public.%I for each row execute function private.reject_append_only_mutation()',
      t, t
    );
    execute format('drop trigger if exists %I_no_delete on public.%I', t, t);
    execute format(
      'create trigger %I_no_delete before delete on public.%I for each row execute function private.reject_append_only_mutation()',
      t, t
    );
  end loop;
end $$;

do $$
begin
  if not exists (
    select 1 from pgmq.list_queues()
    where queue_name='triconsensus_marl_rewards'
  ) then
    perform pgmq.create('triconsensus_marl_rewards');
  end if;
end $$;

create or replace function private.enqueue_marl_reward()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pgmq
as $$
begin
  perform pgmq.send(
    'triconsensus_marl_rewards',
    jsonb_build_object(
      'reward_id', new.id,
      'experiment_id', new.experiment_id,
      'trade_id', new.trade_id,
      'symbol', new.symbol,
      'policy_version', new.policy_version,
      'created_at', new.created_at
    )
  );
  return new;
end;
$$;
revoke all on function private.enqueue_marl_reward()
  from public, anon, authenticated;

drop trigger if exists marl_reward_memory_enqueue on public.marl_reward_memory;
create trigger marl_reward_memory_enqueue
after insert on public.marl_reward_memory
for each row execute function private.enqueue_marl_reward();

create or replace function public.v6_dequeue_marl_rewards(
  p_vt integer default 300,
  p_qty integer default 32
)
returns table(
  msg_id bigint,
  read_ct integer,
  enqueued_at timestamptz,
  vt timestamptz,
  message jsonb
)
language sql
security definer
set search_path = pg_catalog, pgmq
as $$
  select r.msg_id, r.read_ct, r.enqueued_at, r.vt, r.message
  from pgmq.read(
    'triconsensus_marl_rewards',
    greatest(30, least(p_vt, 3600)),
    greatest(1, least(p_qty, 128))
  ) r;
$$;
revoke all on function public.v6_dequeue_marl_rewards(integer,integer)
  from public, anon, authenticated;
grant execute on function public.v6_dequeue_marl_rewards(integer,integer)
  to service_role;

create or replace function public.v6_archive_marl_reward(p_msg_id bigint)
returns boolean
language sql
security definer
set search_path = pg_catalog, pgmq
as $$
  select pgmq.archive('triconsensus_marl_rewards', p_msg_id);
$$;
revoke all on function public.v6_archive_marl_reward(bigint)
  from public, anon, authenticated;
grant execute on function public.v6_archive_marl_reward(bigint)
  to service_role;
