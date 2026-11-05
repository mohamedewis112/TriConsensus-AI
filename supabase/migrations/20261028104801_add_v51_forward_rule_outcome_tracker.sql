create table if not exists public.strategy_forward_rule_selections (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  signal_snapshot_id uuid not null unique references public.strategy_signal_snapshots(id) on delete restrict,
  calibration_id uuid not null references public.strategy_calibrations(id) on delete restrict,
  strategy_version text not null check (strategy_version = 'V5.1'),
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  side text not null check (side in ('LONG','SHORT')),
  context_timestamp timestamptz not null,
  entry_candle_open_time timestamptz not null,
  entry_price numeric not null check (entry_price > 0),
  exit_candle_open_time timestamptz not null,
  horizon_minutes integer not null check (horizon_minutes = 180),
  round_trip_cost numeric not null check (round_trip_cost = 0.0015),
  selection_policy text not null,
  payload jsonb not null default '{}'::jsonb,
  checksum text not null check (length(checksum) = 64),
  selected_at timestamptz not null default now(),
  unique (experiment_id, symbol, context_timestamp)
);

create table if not exists public.strategy_forward_rule_outcomes (
  id uuid primary key default gen_random_uuid(),
  selection_id uuid not null unique references public.strategy_forward_rule_selections(id) on delete restrict,
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  side text not null check (side in ('LONG','SHORT')),
  entry_price numeric not null check (entry_price > 0),
  exit_price numeric not null check (exit_price > 0),
  gross_return numeric not null,
  round_trip_cost numeric not null check (round_trip_cost = 0.0015),
  net_return numeric not null,
  hit boolean not null,
  entry_context_timestamp timestamptz not null,
  exit_candle_open_time timestamptz not null,
  payload jsonb not null default '{}'::jsonb,
  checksum text not null check (length(checksum) = 64),
  evaluated_at timestamptz not null default now()
);

alter table public.strategy_forward_rule_selections enable row level security;
alter table public.strategy_forward_rule_outcomes enable row level security;

revoke all on public.strategy_forward_rule_selections from anon, authenticated;
revoke all on public.strategy_forward_rule_outcomes from anon, authenticated;
grant select on public.strategy_forward_rule_selections to service_role;
grant select on public.strategy_forward_rule_outcomes to service_role;

create index if not exists idx_forward_rule_selections_exp_symbol_context
  on public.strategy_forward_rule_selections (experiment_id, symbol, context_timestamp desc);

create index if not exists idx_forward_rule_selections_exit_time
  on public.strategy_forward_rule_selections (exit_candle_open_time);

create index if not exists idx_forward_rule_outcomes_exp_symbol_eval
  on public.strategy_forward_rule_outcomes (experiment_id, symbol, evaluated_at desc);

create or replace function private.reject_forward_research_mutation()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public, private
as $$
begin
  raise exception 'forward research rows are append-only';
end;
$$;

revoke all on function private.reject_forward_research_mutation() from public, anon, authenticated;

drop trigger if exists trg_forward_rule_selections_append_only
  on public.strategy_forward_rule_selections;
create trigger trg_forward_rule_selections_append_only
before update or delete on public.strategy_forward_rule_selections
for each row execute function private.reject_forward_research_mutation();

drop trigger if exists trg_forward_rule_outcomes_append_only
  on public.strategy_forward_rule_outcomes;
create trigger trg_forward_rule_outcomes_append_only
before update or delete on public.strategy_forward_rule_outcomes
for each row execute function private.reject_forward_research_mutation();

create or replace function private.select_v51_forward_rule_trades()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  v_experiment_id uuid;
  v_symbol text;
  v_raw_symbol text;
  v_last_context timestamptz;
  v_entry_price numeric;
  v_inserted integer := 0;
  v_payload jsonb;
  r record;
begin
  if current_date < date '2026-10-27'
     or current_date > date '2026-12-25' then
    return 0;
  end if;

  perform pg_advisory_xact_lock(hashtext('triconsensus_v51_forward_rule_selector'));

  select id
  into v_experiment_id
  from public.experiments
  where version = 'V5.1-PAPER'
    and execution_mode = 'paper'
    and config_hash = 'b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0'
  limit 1;

  if v_experiment_id is null then
    raise exception 'V5.1-PAPER immutable experiment configuration is missing or mismatched';
  end if;

  foreach v_symbol in array array['BTC/USDT'::text, 'ETH/USDT'::text]
  loop
    v_raw_symbol := case v_symbol
      when 'BTC/USDT' then 'BTCUSDT'
      when 'ETH/USDT' then 'ETHUSDT'
    end;

    select max(context_timestamp)
    into v_last_context
    from public.strategy_forward_rule_selections
    where experiment_id = v_experiment_id
      and symbol = v_symbol;

    for r in
      select s.*
      from public.strategy_signal_snapshots s
      where s.experiment_id = v_experiment_id
        and s.strategy_version = 'V5.1'
        and s.symbol = v_symbol
        and s.signal in ('LONG','SHORT')
        and s.payload->>'recorder' = 'supabase_pg_cron_v1'
        and s.context_timestamp >= timestamptz '2026-10-27 00:00:00+00'
        and s.context_timestamp <= timestamptz '2026-12-25 20:45:00+00'
        and not exists (
          select 1
          from public.strategy_forward_rule_selections x
          where x.signal_snapshot_id = s.id
        )
        and (
          v_last_context is null
          or s.context_timestamp > v_last_context + interval '180 minutes'
        )
      order by s.context_timestamp
    loop
      if v_last_context is not null
         and r.context_timestamp <= v_last_context + interval '180 minutes' then
        continue;
      end if;

      if now() < r.context_timestamp + interval '26 minutes' then
        continue;
      end if;

      select c.close_price
      into v_entry_price
      from public.market_candles_15m c
      where c.symbol = v_raw_symbol
        and c.open_time = r.context_timestamp
        and c.source = 'bybit_v5_public'
      limit 1;

      if v_entry_price is null or v_entry_price <= 0 then
        continue;
      end if;

      v_payload := jsonb_build_object(
        'evaluation_mode', 'frozen_rule_scientific_forward',
        'execution_simulation', false,
        'strategy_version', 'V5.1',
        'signal_snapshot_id', r.id,
        'calibration_id', r.calibration_id,
        'symbol', v_symbol,
        'side', r.signal,
        'context_timestamp', r.context_timestamp,
        'entry_price_source', 'Bybit 15m candle close',
        'entry_candle_open_time', r.context_timestamp,
        'entry_price', v_entry_price,
        'exit_candle_open_time', r.context_timestamp + interval '180 minutes',
        'horizon_minutes', 180,
        'round_trip_cost', 0.0015,
        'non_overlap_policy', 'next candidate must be strictly after prior entry context + 180m',
        'default_action', 'NO_TRADE'
      );

      insert into public.strategy_forward_rule_selections (
        experiment_id,
        signal_snapshot_id,
        calibration_id,
        strategy_version,
        symbol,
        side,
        context_timestamp,
        entry_candle_open_time,
        entry_price,
        exit_candle_open_time,
        horizon_minutes,
        round_trip_cost,
        selection_policy,
        payload,
        checksum
      )
      values (
        v_experiment_id,
        r.id,
        r.calibration_id,
        'V5.1',
        v_symbol,
        r.signal,
        r.context_timestamp,
        r.context_timestamp,
        v_entry_price,
        r.context_timestamp + interval '180 minutes',
        180,
        0.0015,
        'historical_rule_parity_non_overlapping',
        v_payload,
        encode(extensions.digest(convert_to(v_payload::text, 'UTF8'), 'sha256'), 'hex')
      )
      on conflict (signal_snapshot_id) do nothing;

      if found then
        v_inserted := v_inserted + 1;
        v_last_context := r.context_timestamp;
      end if;
    end loop;
  end loop;

  return v_inserted;
end;
$$;

revoke all on function private.select_v51_forward_rule_trades() from public, anon, authenticated;

create or replace function private.evaluate_v51_forward_rule_trades()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  v_raw_symbol text;
  v_exit_price numeric;
  v_gross numeric;
  v_net numeric;
  v_inserted integer := 0;
  v_payload jsonb;
  r record;
begin
  perform pg_advisory_xact_lock(hashtext('triconsensus_v51_forward_rule_evaluator'));

  for r in
    select s.*
    from public.strategy_forward_rule_selections s
    where not exists (
      select 1
      from public.strategy_forward_rule_outcomes o
      where o.selection_id = s.id
    )
      and now() >= s.exit_candle_open_time + interval '28 minutes'
    order by s.exit_candle_open_time
  loop
    v_raw_symbol := case r.symbol
      when 'BTC/USDT' then 'BTCUSDT'
      when 'ETH/USDT' then 'ETHUSDT'
    end;

    select c.close_price
    into v_exit_price
    from public.market_candles_15m c
    where c.symbol = v_raw_symbol
      and c.open_time = r.exit_candle_open_time
      and c.source = 'bybit_v5_public'
    limit 1;

    if v_exit_price is null or v_exit_price <= 0 then
      continue;
    end if;

    v_gross := case r.side
      when 'LONG' then (v_exit_price / r.entry_price) - 1
      when 'SHORT' then 1 - (v_exit_price / r.entry_price)
    end;
    v_net := v_gross - r.round_trip_cost;

    v_payload := jsonb_build_object(
      'evaluation_mode', 'frozen_rule_scientific_forward',
      'execution_simulation', false,
      'selection_id', r.id,
      'signal_snapshot_id', r.signal_snapshot_id,
      'strategy_version', r.strategy_version,
      'symbol', r.symbol,
      'side', r.side,
      'entry_context_timestamp', r.context_timestamp,
      'entry_price', r.entry_price,
      'exit_candle_open_time', r.exit_candle_open_time,
      'exit_price', v_exit_price,
      'gross_return', v_gross,
      'round_trip_cost', r.round_trip_cost,
      'net_return', v_net,
      'hit', (v_net > 0),
      'horizon_minutes', r.horizon_minutes
    );

    insert into public.strategy_forward_rule_outcomes (
      selection_id,
      experiment_id,
      symbol,
      side,
      entry_price,
      exit_price,
      gross_return,
      round_trip_cost,
      net_return,
      hit,
      entry_context_timestamp,
      exit_candle_open_time,
      payload,
      checksum
    )
    values (
      r.id,
      r.experiment_id,
      r.symbol,
      r.side,
      r.entry_price,
      v_exit_price,
      v_gross,
      r.round_trip_cost,
      v_net,
      (v_net > 0),
      r.context_timestamp,
      r.exit_candle_open_time,
      v_payload,
      encode(extensions.digest(convert_to(v_payload::text, 'UTF8'), 'sha256'), 'hex')
    )
    on conflict (selection_id) do nothing;

    if found then
      v_inserted := v_inserted + 1;
    end if;
  end loop;

  return v_inserted;
end;
$$;

revoke all on function private.evaluate_v51_forward_rule_trades() from public, anon, authenticated;

select cron.schedule(
  'triconsensus_v51_forward_rule_selector',
  '12,27,42,57 * * * *',
  'select private.select_v51_forward_rule_trades();'
);

select cron.schedule(
  'triconsensus_v51_forward_rule_evaluator',
  '13,28,43,58 * * * *',
  'select private.evaluate_v51_forward_rule_trades();'
);
