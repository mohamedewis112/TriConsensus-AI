create or replace view public.v51_forward_signal_summary
with (security_invoker = true)
as
select
  e.version as experiment_version,
  s.symbol,
  count(*)::bigint as snapshot_count,
  count(*) filter (where s.signal = 'LONG')::bigint as long_count,
  count(*) filter (where s.signal = 'SHORT')::bigint as short_count,
  count(*) filter (where s.signal = 'NO_TRADE')::bigint as no_trade_count,
  min(s.context_timestamp) as first_context_timestamp,
  max(s.context_timestamp) as latest_context_timestamp,
  min(s.low_threshold) as low_threshold,
  max(s.high_threshold) as high_threshold
from public.strategy_signal_snapshots s
join public.experiments e on e.id = s.experiment_id
where e.version = 'V5.1-PAPER'
  and s.strategy_version = 'V5.1'
  and s.payload->>'recorder' = 'supabase_pg_cron_v1'
group by e.version, s.symbol;

create or replace view public.v51_forward_rule_summary
with (security_invoker = true)
as
with base as (
  select
    o.*,
    exp(
      sum(ln(1 + o.net_return::double precision))
      over (
        partition by o.symbol
        order by o.entry_context_timestamp, o.id
        rows between unbounded preceding and current row
      )
    ) as equity_factor
  from public.strategy_forward_rule_outcomes o
  join public.experiments e on e.id = o.experiment_id
  where e.version = 'V5.1-PAPER'
    and o.net_return > -1
),
with_peaks as (
  select
    *,
    max(equity_factor)
      over (
        partition by symbol
        order by entry_context_timestamp, id
        rows between unbounded preceding and current row
      ) as peak_equity_factor
  from base
)
select
  symbol,
  count(*)::bigint as evaluated_trade_count,
  count(*) filter (where side = 'LONG')::bigint as long_count,
  count(*) filter (where side = 'SHORT')::bigint as short_count,
  avg(hit::int::numeric) as hit_rate,
  avg(gross_return) as mean_gross_return,
  avg(net_return) as mean_net_return,
  percentile_cont(0.5) within group (order by net_return) as median_net_return,
  (
    sum(net_return) filter (where net_return > 0)
    /
    nullif(abs(sum(net_return) filter (where net_return < 0)), 0)
  ) as profit_factor,
  (
    exp(sum(ln(1 + net_return::double precision))) - 1
  ) as cumulative_compounded_return,
  max(
    case
      when peak_equity_factor > 0
      then (peak_equity_factor - equity_factor) / peak_equity_factor
      else 0
    end
  ) as max_drawdown,
  min(entry_context_timestamp) as first_entry_context,
  max(entry_context_timestamp) as latest_entry_context,
  max(evaluated_at) as latest_evaluated_at
from with_peaks
group by symbol;

revoke all on public.v51_forward_signal_summary from public, anon, authenticated;
revoke all on public.v51_forward_rule_summary from public, anon, authenticated;
grant select on public.v51_forward_signal_summary to service_role;
grant select on public.v51_forward_rule_summary to service_role;
