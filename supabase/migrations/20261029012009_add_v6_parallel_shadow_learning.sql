create table if not exists public.v6_shadow_learning_control (
  id text primary key check (id = 'parallel-v1'),
  activated_at timestamptz not null,
  horizon_minutes integer not null check (horizon_minutes = 180),
  round_trip_cost numeric not null check (round_trip_cost = 0.0015),
  minimum_transitions_per_asset integer not null check (minimum_transitions_per_asset >= 20),
  active boolean not null default true,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

insert into public.v6_shadow_learning_control (
  id, activated_at, horizon_minutes, round_trip_cost,
  minimum_transitions_per_asset, active, payload
)
values (
  'parallel-v1', now(), 180, 0.0015, 20, true,
  jsonb_build_object(
    'purpose','parallel_v6_shadow_learning',
    'v51_frozen_baseline',true,
    'old_forward_outcomes_allowed',false,
    'marl_reward_memory_allowed',false,
    'btc_eth_replay_isolated',true,
    'portfolio_safe_prior_learning',true,
    'risk_actor_training_requires_real_closed_paper_rewards',true,
    'trading_authority',false
  )
)
on conflict (id) do nothing;

create table if not exists public.v6_shadow_learning_transitions (
  id uuid primary key default gen_random_uuid(),
  signal_snapshot_id uuid not null unique
    references public.strategy_signal_snapshots(id) on delete restrict,
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  side text not null check (side in ('LONG','SHORT')),
  context_timestamp timestamptz not null,
  entry_candle_open_time timestamptz not null,
  entry_price numeric not null check (entry_price > 0),
  exit_candle_open_time timestamptz not null,
  state_json jsonb not null,
  portfolio_action_json jsonb not null,
  dynamic_risk_action_json jsonb not null,
  portfolio_policy_version text not null,
  dynamic_risk_policy_version text not null,
  training_partition text not null default 'shadow_parallel_v1'
    check (training_partition = 'shadow_parallel_v1'),
  payload jsonb not null default '{}'::jsonb,
  checksum text not null check (length(checksum)=64),
  created_at timestamptz not null default now(),
  unique (symbol, context_timestamp)
);

create table if not exists public.v6_shadow_learning_outcomes (
  id uuid primary key default gen_random_uuid(),
  transition_id uuid not null unique
    references public.v6_shadow_learning_transitions(id) on delete restrict,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  side text not null check (side in ('LONG','SHORT')),
  entry_price numeric not null check (entry_price > 0),
  exit_price numeric not null check (exit_price > 0),
  baseline_gross_return numeric not null,
  baseline_net_return numeric not null,
  effective_exposure numeric not null check (effective_exposure between 0 and 1),
  shadow_net_return numeric not null,
  reward numeric not null,
  baseline_hit boolean not null,
  shadow_hit boolean not null,
  evaluation_mode text not null
    check (evaluation_mode = 'counterfactual_fixed_horizon_exposure_gate_v1'),
  payload jsonb not null default '{}'::jsonb,
  checksum text not null check (length(checksum)=64),
  evaluated_at timestamptz not null default now()
);

create table if not exists public.v6_shadow_asset_policy_versions (
  id uuid primary key default gen_random_uuid(),
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  version text not null,
  sample_count integer not null check (sample_count >= 20),
  wins integer not null check (wins >= 0 and wins <= sample_count),
  posterior_win_rate numeric not null check (posterior_win_rate between 0 and 1),
  mean_baseline_net_return numeric not null,
  avg_win numeric not null check (avg_win >= 0),
  avg_loss numeric not null check (avg_loss >= 0),
  max_weight numeric not null check (max_weight between 0 and 1),
  methodology text not null
    check (methodology = 'beta_smoothed_half_kelly_safe_prior_v1'),
  portfolio_trainable boolean not null default true,
  risk_trainable boolean not null default false,
  mappo_ready boolean not null default false,
  payload jsonb not null default '{}'::jsonb,
  checksum text not null check (length(checksum)=64),
  created_at timestamptz not null default now(),
  unique(symbol, sample_count),
  unique(symbol, version)
);

alter table public.v6_shadow_learning_control enable row level security;
alter table public.v6_shadow_learning_transitions enable row level security;
alter table public.v6_shadow_learning_outcomes enable row level security;
alter table public.v6_shadow_asset_policy_versions enable row level security;

revoke all on public.v6_shadow_learning_control from public,anon,authenticated;
revoke all on public.v6_shadow_learning_transitions from public,anon,authenticated;
revoke all on public.v6_shadow_learning_outcomes from public,anon,authenticated;
revoke all on public.v6_shadow_asset_policy_versions from public,anon,authenticated;

grant select on public.v6_shadow_learning_control to service_role;
grant select on public.v6_shadow_learning_transitions to service_role;
grant select on public.v6_shadow_learning_outcomes to service_role;
grant select on public.v6_shadow_asset_policy_versions to service_role;

create index if not exists idx_v6_shadow_learning_transition_due
  on public.v6_shadow_learning_transitions(exit_candle_open_time);
create index if not exists idx_v6_shadow_learning_outcome_symbol_eval
  on public.v6_shadow_learning_outcomes(symbol,evaluated_at desc);
create index if not exists idx_v6_shadow_asset_policy_symbol_samples
  on public.v6_shadow_asset_policy_versions(symbol,sample_count desc);

create or replace function private.reject_v6_shadow_learning_mutation()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public, private
as $$
begin
  raise exception 'V6 shadow learning research rows are append-only';
end;
$$;

revoke all on function private.reject_v6_shadow_learning_mutation()
  from public,anon,authenticated;

drop trigger if exists trg_v6_shadow_learning_control_immutable
  on public.v6_shadow_learning_control;
create trigger trg_v6_shadow_learning_control_immutable
before update or delete on public.v6_shadow_learning_control
for each row execute function private.reject_v6_shadow_learning_mutation();

drop trigger if exists trg_v6_shadow_learning_transition_immutable
  on public.v6_shadow_learning_transitions;
create trigger trg_v6_shadow_learning_transition_immutable
before update or delete on public.v6_shadow_learning_transitions
for each row execute function private.reject_v6_shadow_learning_mutation();

drop trigger if exists trg_v6_shadow_learning_outcome_immutable
  on public.v6_shadow_learning_outcomes;
create trigger trg_v6_shadow_learning_outcome_immutable
before update or delete on public.v6_shadow_learning_outcomes
for each row execute function private.reject_v6_shadow_learning_mutation();

drop trigger if exists trg_v6_shadow_asset_policy_immutable
  on public.v6_shadow_asset_policy_versions;
create trigger trg_v6_shadow_asset_policy_immutable
before update or delete on public.v6_shadow_asset_policy_versions
for each row execute function private.reject_v6_shadow_learning_mutation();

create or replace function private.capture_v6_parallel_shadow_transitions()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  v_activation timestamptz;
  v_inserted integer := 0;
begin
  perform pg_advisory_xact_lock(hashtext('triconsensus_v6_parallel_shadow_capture'));

  select activated_at into v_activation
  from public.v6_shadow_learning_control
  where id='parallel-v1' and active=true;

  if v_activation is null then
    return 0;
  end if;

  with candidates as (
    select
      s.id as signal_snapshot_id,
      s.experiment_id,
      s.symbol,
      s.signal as side,
      s.context_timestamp,
      p.state_json,
      p.action_json as portfolio_action_json,
      d.action_json as dynamic_risk_action_json,
      p.policy_version as portfolio_policy_version,
      d.policy_version as dynamic_risk_policy_version,
      case s.symbol
        when 'BTC/USDT' then 'BTCUSDT'
        when 'ETH/USDT' then 'ETHUSDT'
      end as raw_symbol
    from public.strategy_signal_snapshots s
    join public.marl_action_logs p
      on p.signal_snapshot_id=s.id
     and p.agent_role='portfolio'
     and p.shadow_mode=true
    join public.marl_action_logs d
      on d.signal_snapshot_id=s.id
     and d.agent_role='dynamic_risk'
     and d.shadow_mode=true
    where s.strategy_version='V5.1'
      and s.signal in ('LONG','SHORT')
      and s.payload->>'recorder'='supabase_pg_cron_v1'
      and s.observed_at >= v_activation
      and p.created_at >= v_activation
      and d.created_at >= v_activation
      and coalesce((p.state_json #>> '{observer,trading_authority}')::boolean,false)=false
      and coalesce((d.state_json #>> '{observer,trading_authority}')::boolean,false)=false
      and not exists (
        select 1 from public.v6_shadow_learning_transitions t
        where t.signal_snapshot_id=s.id
      )
  ),
  priced as (
    select
      c.*,
      m.close_price as entry_price
    from candidates c
    join public.market_candles_15m m
      on m.symbol=c.raw_symbol
     and m.open_time=c.context_timestamp
     and m.source='bybit_v5_public'
    where m.close_price > 0
  ),
  prepared as (
    select
      p.*,
      jsonb_build_object(
        'learning_mode','parallel_shadow_counterfactual',
        'activation_epoch',v_activation,
        'old_forward_outcomes_used',false,
        'marl_reward_memory_used',false,
        'overlap_allowed_for_training_only',true,
        'scientific_forward_benchmark',false,
        'horizon_minutes',180,
        'round_trip_cost',0.0015,
        'portfolio_trainable',true,
        'risk_trainable',false,
        'trading_authority',false
      ) as payload
    from priced p
  )
  insert into public.v6_shadow_learning_transitions(
    signal_snapshot_id,experiment_id,symbol,side,context_timestamp,
    entry_candle_open_time,entry_price,exit_candle_open_time,
    state_json,portfolio_action_json,dynamic_risk_action_json,
    portfolio_policy_version,dynamic_risk_policy_version,payload,checksum
  )
  select
    p.signal_snapshot_id,p.experiment_id,p.symbol,p.side,p.context_timestamp,
    p.context_timestamp,p.entry_price,p.context_timestamp+interval '180 minutes',
    p.state_json,p.portfolio_action_json,p.dynamic_risk_action_json,
    p.portfolio_policy_version,p.dynamic_risk_policy_version,p.payload,
    encode(extensions.digest(
      convert_to(
        jsonb_build_object(
          'signal_snapshot_id',p.signal_snapshot_id,
          'symbol',p.symbol,
          'side',p.side,
          'context_timestamp',p.context_timestamp,
          'entry_price',p.entry_price,
          'portfolio_action',p.portfolio_action_json,
          'dynamic_risk_action',p.dynamic_risk_action_json,
          'activation_epoch',v_activation
        )::text,'UTF8'
      ),'sha256'
    ),'hex')
  from prepared p
  on conflict(signal_snapshot_id) do nothing;

  get diagnostics v_inserted = row_count;
  return v_inserted;
end;
$$;

create or replace function private.evaluate_v6_parallel_shadow_transitions()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  r record;
  v_raw_symbol text;
  v_exit_price numeric;
  v_gross numeric;
  v_baseline_net numeric;
  v_weight numeric;
  v_force_no_trade boolean;
  v_effective_exposure numeric;
  v_shadow_net numeric;
  v_payload jsonb;
  v_inserted integer := 0;
begin
  perform pg_advisory_xact_lock(hashtext('triconsensus_v6_parallel_shadow_evaluator'));

  for r in
    select t.*
    from public.v6_shadow_learning_transitions t
    where not exists (
      select 1 from public.v6_shadow_learning_outcomes o
      where o.transition_id=t.id
    )
      and now() >= t.exit_candle_open_time + interval '28 minutes'
    order by t.exit_candle_open_time
  loop
    v_raw_symbol := case r.symbol
      when 'BTC/USDT' then 'BTCUSDT'
      when 'ETH/USDT' then 'ETHUSDT'
    end;

    select c.close_price into v_exit_price
    from public.market_candles_15m c
    where c.symbol=v_raw_symbol
      and c.open_time=r.exit_candle_open_time
      and c.source='bybit_v5_public'
    limit 1;

    if v_exit_price is null or v_exit_price <= 0 then
      continue;
    end if;

    v_gross := case r.side
      when 'LONG' then (v_exit_price/r.entry_price)-1
      when 'SHORT' then 1-(v_exit_price/r.entry_price)
    end;
    v_baseline_net := v_gross - 0.0015;

    v_weight := greatest(
      0,
      least(
        1,
        coalesce(
          ((r.portfolio_action_json->'asset_weights')->>r.symbol)::numeric,
          1
        )
      )
    );
    v_force_no_trade := coalesce(
      (r.dynamic_risk_action_json->>'force_no_trade')::boolean,
      false
    );
    v_effective_exposure := case when v_force_no_trade then 0 else v_weight end;
    v_shadow_net := v_baseline_net * v_effective_exposure;

    v_payload := jsonb_build_object(
      'evaluation_mode','counterfactual_fixed_horizon_exposure_gate_v1',
      'training_only',true,
      'scientific_forward_benchmark',false,
      'source_signal_snapshot_id',r.signal_snapshot_id,
      'entry_context_timestamp',r.context_timestamp,
      'entry_price',r.entry_price,
      'exit_candle_open_time',r.exit_candle_open_time,
      'exit_price',v_exit_price,
      'baseline_gross_return',v_gross,
      'round_trip_cost',0.0015,
      'baseline_net_return',v_baseline_net,
      'portfolio_weight',v_weight,
      'dynamic_force_no_trade',v_force_no_trade,
      'effective_exposure',v_effective_exposure,
      'shadow_net_return',v_shadow_net,
      'reward',v_shadow_net,
      'risk_actor_trainable',false,
      'marl_reward_memory_written',false,
      'anti_leakage_guard','evaluated_only_after_exit_candle_plus_28m'
    );

    insert into public.v6_shadow_learning_outcomes(
      transition_id,symbol,side,entry_price,exit_price,
      baseline_gross_return,baseline_net_return,effective_exposure,
      shadow_net_return,reward,baseline_hit,shadow_hit,
      evaluation_mode,payload,checksum
    )
    values(
      r.id,r.symbol,r.side,r.entry_price,v_exit_price,
      v_gross,v_baseline_net,v_effective_exposure,
      v_shadow_net,v_shadow_net,(v_baseline_net>0),(v_shadow_net>0),
      'counterfactual_fixed_horizon_exposure_gate_v1',
      v_payload,
      encode(extensions.digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex')
    )
    on conflict(transition_id) do nothing;

    if found then
      v_inserted := v_inserted + 1;
    end if;
  end loop;

  return v_inserted;
end;
$$;

create or replace function private.update_v6_parallel_safe_priors()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  v_symbol text;
  v_n integer;
  v_wins integer;
  v_mean numeric;
  v_avg_win numeric;
  v_avg_loss numeric;
  v_p numeric;
  v_b numeric;
  v_kelly numeric;
  v_half_kelly numeric;
  v_version text;
  v_payload jsonb;
  v_inserted integer := 0;
begin
  perform pg_advisory_xact_lock(hashtext('triconsensus_v6_parallel_safe_prior'));

  foreach v_symbol in array array['BTC/USDT'::text,'ETH/USDT'::text]
  loop
    select
      count(*)::integer,
      count(*) filter (where baseline_net_return>0)::integer,
      avg(baseline_net_return),
      coalesce(avg(baseline_net_return) filter (where baseline_net_return>0),0),
      coalesce(abs(avg(baseline_net_return) filter (where baseline_net_return<0)),0)
    into v_n,v_wins,v_mean,v_avg_win,v_avg_loss
    from public.v6_shadow_learning_outcomes
    where symbol=v_symbol;

    if v_n < 20 then
      continue;
    end if;

    if exists(
      select 1 from public.v6_shadow_asset_policy_versions
      where symbol=v_symbol and sample_count=v_n
    ) then
      continue;
    end if;

    v_p := (v_wins + 1.0) / (v_n + 2.0);

    if v_avg_win <= 0 then
      v_half_kelly := 0;
    elsif v_avg_loss <= 0 then
      v_half_kelly := least(0.50, v_p * 0.50);
    else
      v_b := v_avg_win / v_avg_loss;
      v_kelly := v_p - ((1-v_p)/nullif(v_b,0));
      v_half_kelly := greatest(0,least(1,0.5*v_kelly));
    end if;

    v_version := 'v6-shadow-safe-prior-' ||
      case v_symbol when 'BTC/USDT' then 'btc' else 'eth' end ||
      '-n' || v_n::text;

    v_payload := jsonb_build_object(
      'symbol',v_symbol,
      'sample_count',v_n,
      'wins',v_wins,
      'posterior_win_rate',v_p,
      'mean_baseline_net_return',v_mean,
      'avg_win',v_avg_win,
      'avg_loss',v_avg_loss,
      'max_weight',v_half_kelly,
      'methodology','beta_smoothed_half_kelly_safe_prior_v1',
      'portfolio_trainable',true,
      'risk_trainable',false,
      'mappo_ready',false,
      'replay_isolation','per_asset',
      'only_reduce_base_risk',true,
      'old_forward_outcomes_used',false,
      'marl_reward_memory_used',false,
      'trading_authority',false
    );

    insert into public.v6_shadow_asset_policy_versions(
      symbol,version,sample_count,wins,posterior_win_rate,
      mean_baseline_net_return,avg_win,avg_loss,max_weight,
      methodology,portfolio_trainable,risk_trainable,mappo_ready,
      payload,checksum
    )
    values(
      v_symbol,v_version,v_n,v_wins,v_p,
      coalesce(v_mean,0),v_avg_win,v_avg_loss,v_half_kelly,
      'beta_smoothed_half_kelly_safe_prior_v1',
      true,false,false,
      v_payload,
      encode(extensions.digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex')
    )
    on conflict(symbol,sample_count) do nothing;

    if found then
      v_inserted := v_inserted + 1;
    end if;
  end loop;

  return v_inserted;
end;
$$;

revoke all on function private.capture_v6_parallel_shadow_transitions()
  from public,anon,authenticated;
revoke all on function private.evaluate_v6_parallel_shadow_transitions()
  from public,anon,authenticated;
revoke all on function private.update_v6_parallel_safe_priors()
  from public,anon,authenticated;

select cron.schedule(
  'triconsensus_v6_parallel_shadow_capture',
  '4,19,34,49 * * * *',
  'select private.capture_v6_parallel_shadow_transitions();'
);

select cron.schedule(
  'triconsensus_v6_parallel_shadow_evaluator',
  '5,20,35,50 * * * *',
  'select private.evaluate_v6_parallel_shadow_transitions();'
);

select cron.schedule(
  'triconsensus_v6_parallel_safe_prior',
  '6,21,36,51 * * * *',
  'select private.update_v6_parallel_safe_priors();'
);

