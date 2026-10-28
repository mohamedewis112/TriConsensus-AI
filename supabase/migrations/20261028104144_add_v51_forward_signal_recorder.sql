insert into public.experiments (
  version,
  status,
  execution_mode,
  config,
  config_hash,
  notes
)
values (
  'V5.1-PAPER',
  'forward_test',
  'paper',
  '{
    "symbols":["BTC/USDT","ETH/USDT"],
    "market":"USDT_PERPETUAL",
    "primary_timeframe":"15m",
    "context_timeframes":["1m","5m","15m","1h","4h"],
    "paper_slippage_bps":2.0,
    "risk_per_trade":0.0025,
    "max_daily_loss":0.01,
    "max_drawdown":0.05,
    "max_concurrent_trades":1,
    "max_account_exposure":0.2,
    "max_correlated_exposure":0.2,
    "max_losing_streak":3,
    "cooldown_after_loss_minutes":60,
    "min_risk_reward":1.5,
    "max_spread_bps":8.0,
    "max_slippage_bps":10.0,
    "max_realized_volatility_20":0.08,
    "trade_level_formula":"entry=last; stop=1.5ATR; tp1=minRR; tp2=1.5xTP1distance",
    "model_id":"nvidia/nemotron-3-ultra-550b-a55b:free",
    "strategy_version":"V5.1",
    "strategy_feature":"global_long_short_change_1h",
    "strategy_relationship":"negative",
    "strategy_rule":"bottom_decile=>LONG;top_decile=>SHORT",
    "strategy_horizon_minutes":180,
    "strategy_context_lag_minutes":15,
    "strategy_round_trip_research_cost":0.0015,
    "paper_exit_policy":"agent_levels_plus_deterministic_risk_engine",
    "mode":"paper"
  }'::jsonb,
  'b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0',
  'Frozen V5.1 forward-paper experiment. Deterministic evidence recorder is paper-only and never opens trades.'
)
on conflict (version) do nothing;

create or replace function private.capture_v51_forward_signals()
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, private, extensions
as $$
declare
  v_experiment_id uuid;
  v_inserted integer := 0;
begin
  if current_date < date '2026-10-27'
     or current_date > date '2026-12-25' then
    return 0;
  end if;

  perform pg_advisory_xact_lock(hashtext('triconsensus_v51_forward_signal_capture'));

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

  with raw as (
    select
      symbol,
      observed_at,
      long_short_ratio,
      to_timestamp(ceil(extract(epoch from observed_at) / 900.0) * 900) as bucket_end
    from public.market_binance_global_ratio_5m
    where symbol in ('BTCUSDT','ETHUSDT')
      and observed_at >= now() - interval '3 hours'
  ),
  complete_15m as (
    select
      symbol,
      bucket_end,
      (array_agg(long_short_ratio order by observed_at desc))[1]::numeric as ratio
    from raw
    group by symbol, bucket_end
    having count(*) = 3
       and min(observed_at) = bucket_end - interval '10 minutes'
       and max(observed_at) = bucket_end
  ),
  eligible as (
    select
      *,
      row_number() over (partition by symbol order by bucket_end desc) as rn
    from complete_15m
    where bucket_end <= now() - interval '15 minutes'
  ),
  pairs as (
    select
      e1.symbol,
      e1.bucket_end as context_timestamp,
      e1.ratio as latest_ratio,
      e5.bucket_end as prior_timestamp,
      e5.ratio as prior_ratio,
      round(extract(epoch from (now() - e1.bucket_end)) * 1000)::bigint as context_age_ms,
      ln(e1.ratio::double precision) - ln(e5.ratio::double precision) as feature_value
    from eligible e1
    join eligible e5
      on e5.symbol = e1.symbol
     and e5.rn = 5
    where e1.rn = 1
      and e1.bucket_end - e5.bucket_end = interval '1 hour'
      and not exists (
        select 1
        from (
          select
            bucket_end,
            lag(bucket_end) over (order by bucket_end) as prev_end
          from eligible e
          where e.symbol = e1.symbol
            and e.rn <= 5
        ) gaps
        where prev_end is not null
          and bucket_end - prev_end <> interval '15 minutes'
      )
  ),
  cal as (
    select distinct on (symbol)
      id,
      symbol,
      feature_name,
      relationship,
      low_threshold,
      high_threshold,
      calibration_end,
      horizon_minutes,
      context_lag_minutes,
      round_trip_cost,
      config_hash,
      expires_at
    from public.strategy_calibrations
    where strategy_version = 'V5.1'
      and calibration_end <= current_date
      and expires_at > now()
    order by symbol, calibration_end desc, created_at desc
  ),
  signals as (
    select
      p.symbol as raw_symbol,
      case p.symbol
        when 'BTCUSDT' then 'BTC/USDT'
        when 'ETHUSDT' then 'ETH/USDT'
      end as display_symbol,
      p.context_timestamp,
      p.prior_timestamp,
      p.latest_ratio,
      p.prior_ratio,
      p.context_age_ms,
      p.feature_value,
      c.id as calibration_id,
      c.feature_name,
      c.relationship,
      c.low_threshold,
      c.high_threshold,
      c.calibration_end,
      c.horizon_minutes,
      c.context_lag_minutes,
      c.round_trip_cost,
      c.config_hash as calibration_config_hash,
      case
        when p.context_age_ms < 900000 or p.context_age_ms > 2700000 then 'NO_TRADE'
        when p.feature_value <= c.low_threshold then 'LONG'
        when p.feature_value >= c.high_threshold then 'SHORT'
        else 'NO_TRADE'
      end as signal,
      case
        when p.context_age_ms < 900000 then 'context_lag_guard_violated'
        when p.context_age_ms > 2700000 then 'global_ratio_context_stale'
        when p.feature_value <= c.low_threshold then 'bottom_decile_inverse_signal'
        when p.feature_value >= c.high_threshold then 'top_decile_inverse_signal'
        else 'inside_calibrated_deciles'
      end as reason
    from pairs p
    join cal c
      on c.symbol = case p.symbol
        when 'BTCUSDT' then 'BTC/USDT'
        when 'ETHUSDT' then 'ETH/USDT'
      end
    where c.feature_name = 'global_long_short_change_1h'
      and c.relationship = 'negative'
      and c.horizon_minutes = 180
      and c.context_lag_minutes = 15
      and abs(c.round_trip_cost - 0.0015) <= 0.000000000001
  ),
  prepared as (
    select
      s.*,
      jsonb_build_object(
        'model_family', 'v5.1_deterministic_cross_venue',
        'strategy_version', 'V5.1',
        'signal', s.signal,
        'feature_name', 'global_long_short_change_1h',
        'feature_value', s.feature_value,
        'relationship', 'negative',
        'horizon_minutes', 180,
        'context_lag_minutes', 15,
        'round_trip_research_cost', 0.0015,
        'context_timestamp_ms', (extract(epoch from s.context_timestamp) * 1000)::bigint,
        'context_age_ms', s.context_age_ms,
        'low_threshold', s.low_threshold,
        'high_threshold', s.high_threshold,
        'calibration_id', s.calibration_id,
        'calibration_end', s.calibration_end::text,
        'calibration_config_hash', s.calibration_config_hash,
        'reason', s.reason,
        'default_action', 'NO_TRADE',
        'recorder', 'supabase_pg_cron_v1',
        'checksum_scheme', 'postgres_jsonb_sha256_v1'
      ) as payload
    from signals s
  )
  insert into public.strategy_signal_snapshots (
    experiment_id,
    market_snapshot_id,
    calibration_id,
    strategy_version,
    symbol,
    observed_at,
    context_timestamp,
    feature_name,
    feature_value,
    low_threshold,
    high_threshold,
    signal,
    context_age_ms,
    reason,
    payload,
    checksum
  )
  select
    v_experiment_id,
    null,
    p.calibration_id,
    'V5.1',
    p.display_symbol,
    now(),
    p.context_timestamp,
    'global_long_short_change_1h',
    p.feature_value,
    p.low_threshold,
    p.high_threshold,
    p.signal,
    p.context_age_ms,
    p.reason,
    p.payload,
    encode(extensions.digest(convert_to(p.payload::text, 'UTF8'), 'sha256'), 'hex')
  from prepared p
  where not exists (
    select 1
    from public.strategy_signal_snapshots existing
    where existing.experiment_id = v_experiment_id
      and existing.calibration_id = p.calibration_id
      and existing.strategy_version = 'V5.1'
      and existing.symbol = p.display_symbol
      and existing.context_timestamp = p.context_timestamp
      and existing.payload->>'recorder' = 'supabase_pg_cron_v1'
  );

  get diagnostics v_inserted = row_count;
  return v_inserted;
end;
$$;

revoke all on function private.capture_v51_forward_signals() from public;
revoke all on function private.capture_v51_forward_signals() from anon;
revoke all on function private.capture_v51_forward_signals() from authenticated;

select cron.schedule(
  'triconsensus_v51_forward_signal_capture',
  '11,26,41,56 * * * *',
  'select private.capture_v51_forward_signals();'
);
