create schema if not exists internal;

create or replace view internal.v51_counterfactual_gate_audit
with (security_invoker = true)
as
with exp as (
  select id
  from public.experiments
  where version = 'V5.1-PAPER'
  order by created_at desc
  limit 1
),
rejected as (
  select distinct on (s.id)
    s.id as signal_snapshot_id,
    s.market_snapshot_id,
    replace(s.symbol, '/', '') as symbol,
    s.signal as side,
    s.observed_at,
    c.id as consensus_decision_id,
    c.reason as consensus_reason,
    c.market_agent_run_id,
    c.strategist_agent_run_id,
    c.risk_agent_run_id
  from public.strategy_signal_snapshots s
  join exp e on e.id = s.experiment_id
  join public.consensus_decisions c
    on c.experiment_id = s.experiment_id
   and c.market_snapshot_id = s.market_snapshot_id
  where s.signal in ('LONG', 'SHORT')
    and c.outcome = 'NO_TRADE'
    and c.full_consensus = false
  order by s.id, c.created_at desc
),
base as (
  select
    r.*,
    m.decision as market_agent_decision,
    st.decision as strategist_agent_decision,
    ra.decision as risk_agent_decision,
    (m.id is null or st.id is null or ra.id is null) as missing_agent_chain,
    oa.status as orchestration_status,
    oa.reason as orchestration_reason,
    entry.open_time as entry_time,
    entry.open_price::double precision as entry_price
  from rejected r
  left join public.agent_runs m on m.id = r.market_agent_run_id
  left join public.agent_runs st on st.id = r.strategist_agent_run_id
  left join public.agent_runs ra on ra.id = r.risk_agent_run_id
  left join lateral (
    select a.status, a.reason
    from public.agent_orchestration_attempts a
    where a.signal_snapshot_id = r.signal_snapshot_id
    order by a.created_at desc
    limit 1
  ) oa on true
  join lateral (
    select mc.open_time, mc.open_price
    from public.market_candles_15m mc
    where mc.symbol = r.symbol
      and mc.open_time > r.observed_at
    order by mc.open_time
    limit 1
  ) entry on true
)
select
  b.signal_snapshot_id,
  b.market_snapshot_id,
  b.consensus_decision_id,
  b.symbol,
  b.side,
  b.observed_at,
  b.consensus_reason,
  b.market_agent_decision,
  b.strategist_agent_decision,
  b.risk_agent_decision,
  b.missing_agent_chain,
  b.orchestration_status,
  b.orchestration_reason,
  b.entry_time,
  b.entry_price,
  h.horizon_min,
  exitc.open_time + interval '15 minutes' as exit_time,
  exitc.close_price::double precision as exit_price,
  (exitc.open_time is not null) as matured,
  0.0015::double precision as round_trip_cost,
  case
    when exitc.open_time is null then null
    when b.side = 'LONG'
      then (exitc.close_price::double precision / b.entry_price - 1.0) - 0.0015
    when b.side = 'SHORT'
      then (b.entry_price - exitc.close_price::double precision) / b.entry_price - 0.0015
  end as net_return,
  case
    when exitc.open_time is null then null
    else (
      case
        when b.side = 'LONG'
          then (exitc.close_price::double precision / b.entry_price - 1.0) - 0.0015
        when b.side = 'SHORT'
          then (b.entry_price - exitc.close_price::double precision) / b.entry_price - 0.0015
      end
    ) > 0
  end as would_win
from base b
cross join (values (15), (30), (60), (180)) h(horizon_min)
left join public.market_candles_15m exitc
  on exitc.symbol = b.symbol
 and exitc.open_time = b.entry_time + make_interval(mins => h.horizon_min - 15);

create or replace view internal.v51_counterfactual_gate_summary
with (security_invoker = true)
as
select
  horizon_min,
  symbol,
  side,
  coalesce(orchestration_reason, 'NO_ORCHESTRATION_REASON') as orchestration_reason,
  count(*) filter (where matured) as matured_n,
  round(
    (avg((would_win)::int) filter (where matured) * 100)::numeric,
    2
  ) as win_rate_pct,
  round(
    (avg(net_return) filter (where matured) * 100)::numeric,
    4
  ) as mean_net_pct,
  round(
    (percentile_cont(0.5) within group (order by net_return)
      filter (where matured) * 100)::numeric,
    4
  ) as median_net_pct,
  round((
    (
      sum(case when net_return > 0 then net_return else 0 end)
      filter (where matured)
    ) /
    nullif(
      abs(
        sum(case when net_return < 0 then net_return else 0 end)
        filter (where matured)
      ),
      0
    )
  )::numeric, 4) as profit_factor
from internal.v51_counterfactual_gate_audit
group by horizon_min, symbol, side, coalesce(orchestration_reason, 'NO_ORCHESTRATION_REASON');
