create table if not exists public.strategy_forward_health_checks (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null references public.experiments(id) on delete restrict,
  strategy_version text not null check (strategy_version = 'V5.1'),
  status text not null check (status in ('healthy','fail_closed')),
  checked_at timestamptz not null default now(),
  checks jsonb not null,
  checksum text not null check (length(checksum) = 64)
);

alter table public.strategy_forward_health_checks enable row level security;
revoke all on public.strategy_forward_health_checks from anon, authenticated;
grant select on public.strategy_forward_health_checks to service_role;

create index if not exists idx_forward_health_exp_time
  on public.strategy_forward_health_checks (experiment_id, checked_at desc);

create or replace function private.reject_forward_health_mutation()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public, private
as $$
begin
  raise exception 'forward health checks are append-only';
end;
$$;

revoke all on function private.reject_forward_health_mutation() from public, anon, authenticated;

drop trigger if exists trg_forward_health_append_only
  on public.strategy_forward_health_checks;
create trigger trg_forward_health_append_only
before update or delete on public.strategy_forward_health_checks
for each row execute function private.reject_forward_health_mutation();

create or replace function private.audit_v51_forward_health()
returns text
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions, cron
as $$
declare
  v_experiment_id uuid;
  v_btc_ratio timestamptz;
  v_eth_ratio timestamptz;
  v_btc_signal timestamptz;
  v_eth_signal timestamptz;
  v_active_calibrations integer := 0;
  v_active_jobs integer := 0;
  v_recent_success_jobs integer := 0;
  v_status text := 'healthy';
  v_checks jsonb;
begin
  if current_date < date '2026-10-27'
     or current_date > date '2026-12-25' then
    return 'outside_forward_window';
  end if;

  perform pg_advisory_xact_lock(hashtext('triconsensus_v51_forward_health'));

  select id
  into v_experiment_id
  from public.experiments
  where version = 'V5.1-PAPER'
    and execution_mode = 'paper'
    and config_hash = 'b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0'
  limit 1;

  select max(observed_at) into v_btc_ratio
  from public.market_binance_global_ratio_5m
  where symbol = 'BTCUSDT';

  select max(observed_at) into v_eth_ratio
  from public.market_binance_global_ratio_5m
  where symbol = 'ETHUSDT';

  select max(context_timestamp) into v_btc_signal
  from public.strategy_signal_snapshots s
  where s.experiment_id = v_experiment_id
    and s.strategy_version = 'V5.1'
    and s.symbol = 'BTC/USDT'
    and s.payload->>'recorder' = 'supabase_pg_cron_v1';

  select max(context_timestamp) into v_eth_signal
  from public.strategy_signal_snapshots s
  where s.experiment_id = v_experiment_id
    and s.strategy_version = 'V5.1'
    and s.symbol = 'ETH/USDT'
    and s.payload->>'recorder' = 'supabase_pg_cron_v1';

  select count(*)
  into v_active_calibrations
  from public.strategy_calibrations
  where strategy_version = 'V5.1'
    and symbol in ('BTC/USDT','ETH/USDT')
    and feature_name = 'global_long_short_change_1h'
    and relationship = 'negative'
    and horizon_minutes = 180
    and context_lag_minutes = 15
    and abs(round_trip_cost - 0.0015) <= 0.000000000001
    and config_hash = '84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef'
    and calibration_end = date '2026-10-26'
    and expires_at > now();

  select count(*)
  into v_active_jobs
  from cron.job
  where active = true
    and jobname in (
      'triconsensus_derivatives_btc_15m',
      'triconsensus_derivatives_eth_15m',
      'triconsensus_v51_forward_signal_capture',
      'triconsensus_v51_forward_rule_selector',
      'triconsensus_v51_forward_rule_evaluator'
    );

  with expected(jobname) as (
    values
      ('triconsensus_derivatives_btc_15m'),
      ('triconsensus_derivatives_eth_15m'),
      ('triconsensus_v51_forward_signal_capture'),
      ('triconsensus_v51_forward_rule_selector'),
      ('triconsensus_v51_forward_rule_evaluator')
  ),
  latest as (
    select distinct on (j.jobname)
      j.jobname,
      d.status,
      d.start_time
    from cron.job j
    join cron.job_run_details d on d.jobid = j.jobid
    where j.jobname in (select jobname from expected)
    order by j.jobname, d.start_time desc
  )
  select count(*)
  into v_recent_success_jobs
  from latest
  where status = 'succeeded'
    and start_time >= now() - interval '30 minutes';

  if v_experiment_id is null
     or v_active_calibrations <> 2
     or v_active_jobs <> 5
     or v_recent_success_jobs <> 5
     or v_btc_ratio is null
     or v_eth_ratio is null
     or now() - v_btc_ratio > interval '25 minutes'
     or now() - v_eth_ratio > interval '25 minutes'
     or v_btc_signal is null
     or v_eth_signal is null
     or now() - v_btc_signal > interval '45 minutes'
     or now() - v_eth_signal > interval '45 minutes'
  then
    v_status := 'fail_closed';
  end if;

  v_checks := jsonb_build_object(
    'experiment_present', v_experiment_id is not null,
    'active_calibrations', v_active_calibrations,
    'expected_active_calibrations', 2,
    'active_pipeline_jobs', v_active_jobs,
    'expected_active_pipeline_jobs', 5,
    'recent_success_pipeline_jobs', v_recent_success_jobs,
    'expected_recent_success_pipeline_jobs', 5,
    'btc_latest_ratio_at', v_btc_ratio,
    'eth_latest_ratio_at', v_eth_ratio,
    'btc_latest_signal_context', v_btc_signal,
    'eth_latest_signal_context', v_eth_signal,
    'max_ratio_age_minutes', 25,
    'max_signal_context_age_minutes', 45,
    'calibration_protocol_hash', '84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef',
    'paper_experiment_config_hash', 'b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0',
    'default_action', 'NO_TRADE'
  );

  insert into public.strategy_forward_health_checks (
    experiment_id,
    strategy_version,
    status,
    checks,
    checksum
  )
  values (
    coalesce(
      v_experiment_id,
      (select id from public.experiments where version='V5.1-PAPER' limit 1)
    ),
    'V5.1',
    v_status,
    v_checks,
    encode(extensions.digest(convert_to(v_checks::text, 'UTF8'), 'sha256'), 'hex')
  );

  if v_status = 'fail_closed' then
    insert into public.system_events (
      experiment_id,
      severity,
      component,
      event_type,
      message,
      payload
    )
    select
      v_experiment_id,
      'critical',
      'v51_forward_watchdog',
      'forward_pipeline_fail_closed',
      'V5.1 forward pipeline health check failed; default action remains NO_TRADE.',
      v_checks
    where v_experiment_id is not null
      and not exists (
        select 1
        from public.system_events se
        where se.experiment_id = v_experiment_id
          and se.component = 'v51_forward_watchdog'
          and se.event_type = 'forward_pipeline_fail_closed'
          and se.created_at >= now() - interval '60 minutes'
      );
  end if;

  return v_status;
end;
$$;

revoke all on function private.audit_v51_forward_health() from public, anon, authenticated;

select cron.schedule(
  'triconsensus_v51_forward_watchdog',
  '14,29,44,59 * * * *',
  'select private.audit_v51_forward_health();'
);
