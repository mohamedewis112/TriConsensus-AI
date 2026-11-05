create or replace view public.v51_forward_gate_precheck
with (security_invoker = true)
as
with grouped as (
  select
    o.symbol,
    coalesce(s.payload->>'capture_mode','prospective_live') as capture_mode,
    count(*)::bigint as trade_count,
    avg(o.net_return) as mean_net_return,
    (
      sum(o.net_return) filter (where o.net_return > 0)
      /
      nullif(abs(sum(o.net_return) filter (where o.net_return < 0)), 0)
    ) as profit_factor,
    count(*) filter (where o.net_return > 0)::bigint as winning_trades,
    min(o.entry_context_timestamp) as first_entry_context,
    max(o.entry_context_timestamp) as latest_entry_context
  from public.strategy_forward_rule_outcomes o
  join public.strategy_forward_rule_selections s on s.id=o.selection_id
  join public.experiments e on e.id=o.experiment_id
  where e.version='V5.1-PAPER'
    and o.round_trip_cost=0.0015
  group by o.symbol,capture_mode
)
select
  symbol,
  capture_mode,
  trade_count,
  winning_trades,
  mean_net_return,
  profit_factor,
  first_entry_context,
  latest_entry_context,
  80::bigint as minimum_trades,
  0.0005::numeric as minimum_mean_net_return,
  1.20::numeric as minimum_profit_factor,
  3::integer as minimum_profitable_folds,
  true as requires_positive_bootstrap_lcb,
  (trade_count >= 80) as sample_size_ready,
  case
    when trade_count < 80 then 'INSUFFICIENT_SAMPLE'
    else 'REQUIRES_FULL_FROZEN_GATE_EVALUATION'
  end as qualification_state
from grouped;

revoke all on public.v51_forward_gate_precheck
  from public,anon,authenticated;
grant select on public.v51_forward_gate_precheck to service_role;
