with experiment as (
  select id
  from public.experiments
  where version='V5.1-PAPER'
    and execution_mode='paper'
    and config_hash='b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0'
  limit 1
),
raw as (
  select
    symbol,
    observed_at,
    long_short_ratio,
    to_timestamp(ceil(extract(epoch from observed_at) / 900.0) * 900) as bucket_end
  from public.market_binance_global_ratio_5m
  where observed_at between timestamptz '2026-10-26 22:45:00+00'
                        and timestamptz '2026-10-28 10:00:00+00'
),
complete_15m as (
  select
    symbol,
    bucket_end,
    (array_agg(long_short_ratio order by observed_at desc))[1]::numeric as ratio
  from raw
  group by symbol,bucket_end
  having count(*)=3
     and min(observed_at)=bucket_end-interval '10 minutes'
     and max(observed_at)=bucket_end
),
pairs as (
  select
    cur.symbol,
    cur.bucket_end as context_timestamp,
    cur.ratio as latest_ratio,
    prior.ratio as prior_ratio,
    ln(cur.ratio::double precision)-ln(prior.ratio::double precision) as feature_value
  from complete_15m cur
  join complete_15m prior
    on prior.symbol=cur.symbol
   and prior.bucket_end=cur.bucket_end-interval '1 hour'
  where cur.bucket_end between timestamptz '2026-10-27 00:00:00+00'
                           and timestamptz '2026-10-28 10:00:00+00'
),
cal as (
  select distinct on(symbol)
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
    config_hash
  from public.strategy_calibrations
  where strategy_version='V5.1'
    and feature_name='global_long_short_change_1h'
    and relationship='negative'
    and horizon_minutes=180
    and context_lag_minutes=15
    and abs(round_trip_cost-0.0015)<=0.000000000001
    and config_hash='84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef'
    and calibration_end=date '2026-10-26'
  order by symbol,created_at desc
),
signals as (
  select
    e.id as experiment_id,
    p.symbol as raw_symbol,
    case p.symbol when 'BTCUSDT' then 'BTC/USDT' else 'ETH/USDT' end as display_symbol,
    p.context_timestamp,
    p.feature_value,
    c.id as calibration_id,
    c.low_threshold,
    c.high_threshold,
    c.calibration_end,
    c.config_hash as calibration_config_hash,
    case
      when p.feature_value<=c.low_threshold then 'LONG'
      when p.feature_value>=c.high_threshold then 'SHORT'
      else 'NO_TRADE'
    end as signal,
    case
      when p.feature_value<=c.low_threshold then 'bottom_decile_inverse_signal'
      when p.feature_value>=c.high_threshold then 'top_decile_inverse_signal'
      else 'inside_calibrated_deciles'
    end as reason
  from pairs p
  cross join experiment e
  join cal c
    on c.symbol=case p.symbol when 'BTCUSDT' then 'BTC/USDT' else 'ETH/USDT' end
),
prepared as (
  select
    s.*,
    jsonb_build_object(
      'model_family','v5.1_deterministic_cross_venue',
      'strategy_version','V5.1',
      'signal',s.signal,
      'feature_name','global_long_short_change_1h',
      'feature_value',s.feature_value,
      'relationship','negative',
      'horizon_minutes',180,
      'context_lag_minutes',15,
      'round_trip_research_cost',0.0015,
      'context_timestamp_ms',(extract(epoch from s.context_timestamp)*1000)::bigint,
      'context_age_ms',1560000,
      'low_threshold',s.low_threshold,
      'high_threshold',s.high_threshold,
      'calibration_id',s.calibration_id,
      'calibration_end',s.calibration_end::text,
      'calibration_config_hash',s.calibration_config_hash,
      'reason',s.reason,
      'default_action','NO_TRADE',
      'recorder','supabase_forward_backfill_v1',
      'capture_mode','reconstructed_oos',
      'reconstruction_protocol','frozen_before_forward_window',
      'reconstruction_source','persisted Binance USD-M 5m ratio + frozen V5.1 calibration'
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
  p.experiment_id,
  null,
  p.calibration_id,
  'V5.1',
  p.display_symbol,
  p.context_timestamp + interval '26 minutes',
  p.context_timestamp,
  'global_long_short_change_1h',
  p.feature_value,
  p.low_threshold,
  p.high_threshold,
  p.signal,
  1560000,
  p.reason,
  p.payload,
  encode(extensions.digest(convert_to(p.payload::text,'UTF8'),'sha256'),'hex')
from prepared p
where not exists (
  select 1
  from public.strategy_signal_snapshots existing
  where existing.experiment_id=p.experiment_id
    and existing.calibration_id=p.calibration_id
    and existing.strategy_version='V5.1'
    and existing.symbol=p.display_symbol
    and existing.context_timestamp=p.context_timestamp
);

with recursive
experiment as (
  select id
  from public.experiments
  where version='V5.1-PAPER'
    and execution_mode='paper'
    and config_hash='b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0'
  limit 1
),
candidates as (
  select
    s.id as signal_snapshot_id,
    s.experiment_id,
    s.calibration_id,
    s.symbol,
    s.signal as side,
    s.context_timestamp,
    s.feature_value
  from public.strategy_signal_snapshots s
  cross join experiment e
  where s.experiment_id=e.id
    and s.strategy_version='V5.1'
    and s.signal in ('LONG','SHORT')
    and s.payload->>'recorder'='supabase_forward_backfill_v1'
    and s.payload->>'capture_mode'='reconstructed_oos'
),
seed as (
  select distinct on(symbol) *
  from candidates
  order by symbol,context_timestamp
),
selected(
  signal_snapshot_id,
  experiment_id,
  calibration_id,
  symbol,
  side,
  context_timestamp,
  feature_value
) as (
  select
    signal_snapshot_id,
    experiment_id,
    calibration_id,
    symbol,
    side,
    context_timestamp,
    feature_value
  from seed

  union all

  select
    nxt.signal_snapshot_id,
    nxt.experiment_id,
    nxt.calibration_id,
    nxt.symbol,
    nxt.side,
    nxt.context_timestamp,
    nxt.feature_value
  from selected prev
  cross join lateral (
    select c.*
    from candidates c
    where c.symbol=prev.symbol
      and c.context_timestamp>prev.context_timestamp+interval '180 minutes'
    order by c.context_timestamp
    limit 1
  ) nxt
),
with_price as (
  select
    s.*,
    c.close_price as entry_price,
    case s.symbol when 'BTC/USDT' then 'BTCUSDT' else 'ETHUSDT' end as raw_symbol
  from selected s
  join public.market_candles_15m c
    on c.symbol=case s.symbol when 'BTC/USDT' then 'BTCUSDT' else 'ETHUSDT' end
   and c.open_time=s.context_timestamp
   and c.source='bybit_v5_public'
),
prepared as (
  select
    p.*,
    jsonb_build_object(
      'evaluation_mode','frozen_rule_scientific_forward',
      'execution_simulation',false,
      'strategy_version','V5.1',
      'signal_snapshot_id',p.signal_snapshot_id,
      'calibration_id',p.calibration_id,
      'symbol',p.symbol,
      'side',p.side,
      'context_timestamp',p.context_timestamp,
      'entry_price_source','Bybit 15m candle close',
      'entry_candle_open_time',p.context_timestamp,
      'entry_price',p.entry_price,
      'exit_candle_open_time',p.context_timestamp+interval '180 minutes',
      'horizon_minutes',180,
      'round_trip_cost',0.0015,
      'non_overlap_policy','next candidate must be strictly after prior entry context + 180m',
      'capture_mode','reconstructed_oos',
      'default_action','NO_TRADE'
    ) as payload
  from with_price p
)
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
select
  p.experiment_id,
  p.signal_snapshot_id,
  p.calibration_id,
  'V5.1',
  p.symbol,
  p.side,
  p.context_timestamp,
  p.context_timestamp,
  p.entry_price,
  p.context_timestamp+interval '180 minutes',
  180,
  0.0015,
  'historical_rule_parity_non_overlapping_reconstructed',
  p.payload,
  encode(extensions.digest(convert_to(p.payload::text,'UTF8'),'sha256'),'hex')
from prepared p
on conflict (signal_snapshot_id) do nothing;

create or replace view public.v51_forward_signal_summary_by_capture_mode
with (security_invoker = true)
as
select
  e.version as experiment_version,
  s.symbol,
  coalesce(
    s.payload->>'capture_mode',
    case
      when s.payload->>'recorder'='supabase_pg_cron_v1' then 'prospective_live'
      else 'other'
    end
  ) as capture_mode,
  count(*)::bigint as snapshot_count,
  count(*) filter(where s.signal='LONG')::bigint as long_count,
  count(*) filter(where s.signal='SHORT')::bigint as short_count,
  count(*) filter(where s.signal='NO_TRADE')::bigint as no_trade_count,
  min(s.context_timestamp) as first_context_timestamp,
  max(s.context_timestamp) as latest_context_timestamp
from public.strategy_signal_snapshots s
join public.experiments e on e.id=s.experiment_id
where e.version='V5.1-PAPER'
  and s.strategy_version='V5.1'
group by e.version,s.symbol,capture_mode;

revoke all on public.v51_forward_signal_summary_by_capture_mode
  from public,anon,authenticated;
grant select on public.v51_forward_signal_summary_by_capture_mode to service_role;

create or replace view public.v51_forward_rule_summary_by_capture_mode
with (security_invoker = true)
as
select
  o.symbol,
  coalesce(s.payload->>'capture_mode','prospective_live') as capture_mode,
  count(*)::bigint as evaluated_trade_count,
  count(*) filter(where o.side='LONG')::bigint as long_count,
  count(*) filter(where o.side='SHORT')::bigint as short_count,
  avg(o.hit::int::numeric) as hit_rate,
  avg(o.gross_return) as mean_gross_return,
  avg(o.net_return) as mean_net_return,
  percentile_cont(0.5) within group(order by o.net_return) as median_net_return,
  (
    sum(o.net_return) filter(where o.net_return>0)
    /
    nullif(abs(sum(o.net_return) filter(where o.net_return<0)),0)
  ) as profit_factor,
  min(o.entry_context_timestamp) as first_entry_context,
  max(o.entry_context_timestamp) as latest_entry_context,
  max(o.evaluated_at) as latest_evaluated_at
from public.strategy_forward_rule_outcomes o
join public.strategy_forward_rule_selections s on s.id=o.selection_id
join public.experiments e on e.id=o.experiment_id
where e.version='V5.1-PAPER'
group by o.symbol,capture_mode;

revoke all on public.v51_forward_rule_summary_by_capture_mode
  from public,anon,authenticated;
grant select on public.v51_forward_rule_summary_by_capture_mode to service_role;
