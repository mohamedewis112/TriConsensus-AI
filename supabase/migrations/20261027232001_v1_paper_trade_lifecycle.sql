alter table public.paper_trades
  add column if not exists requested_exit numeric,
  add column if not exists actual_exit numeric,
  add column if not exists trade_duration_seconds bigint;

create or replace function internal.protect_paper_trade()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public, internal
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'paper trade records cannot be deleted';
  end if;

  if old.status = 'closed' then
    raise exception 'closed paper trade is immutable';
  end if;

  if new.experiment_id is distinct from old.experiment_id
     or new.consensus_decision_id is distinct from old.consensus_decision_id
     or new.risk_evaluation_id is distinct from old.risk_evaluation_id
     or new.symbol is distinct from old.symbol
     or new.side is distinct from old.side
     or new.requested_entry is distinct from old.requested_entry
     or new.actual_entry is distinct from old.actual_entry
     or new.stop_loss is distinct from old.stop_loss
     or new.take_profit_1 is distinct from old.take_profit_1
     or new.take_profit_2 is distinct from old.take_profit_2
     or new.position_size is distinct from old.position_size
     or new.notional_value is distinct from old.notional_value
     or new.opened_at is distinct from old.opened_at then
    raise exception 'paper trade entry facts are immutable';
  end if;

  return new;
end;
$$;

drop trigger if exists trg_paper_trades_protect on public.paper_trades;
create trigger trg_paper_trades_protect
before update or delete on public.paper_trades
for each row execute function internal.protect_paper_trade();

create or replace view public.decision_replay
with (security_invoker = true)
as
select
  c.id as consensus_id,
  c.experiment_id,
  c.created_at as decision_at,
  c.outcome,
  c.full_consensus,
  c.reason as consensus_reason,
  m.id as market_snapshot_id,
  m.symbol,
  m.captured_at,
  m.market_data,
  m.funding_rate,
  m.open_interest,
  m.market_data_age_ms,
  fs.features,
  fs.leakage_guard,
  mp.prediction as ml_prediction,
  mp.model_family as ml_model_family,
  ma.output_json as market_agent_output,
  sa.output_json as strategist_agent_output,
  ra.output_json as risk_agent_output,
  re.approved as risk_approved,
  re.checks as risk_checks,
  re.proposed_entry,
  re.stop_loss,
  re.take_profit_1,
  re.take_profit_2,
  re.risk_reward,
  re.position_size,
  re.max_exposure_after,
  t.id as trade_id,
  t.status as trade_status,
  t.actual_entry,
  t.pnl_gross,
  t.pnl_net,
  t.realized_fees,
  t.realized_slippage,
  t.mae,
  t.mfe,
  t.opened_at,
  t.closed_at,
  t.close_reason,
  t.requested_exit,
  t.actual_exit,
  t.trade_duration_seconds
from public.consensus_decisions c
join public.market_snapshots m on m.id = c.market_snapshot_id
left join lateral (
  select f.features, f.leakage_guard
  from public.feature_snapshots f
  where f.market_snapshot_id = m.id
  order by f.calculated_at desc
  limit 1
) fs on true
left join lateral (
  select p.prediction, p.model_family
  from public.ml_predictions p
  where p.market_snapshot_id = m.id
  order by p.created_at desc
  limit 1
) mp on true
left join public.agent_runs ma on ma.id = c.market_agent_run_id
left join public.agent_runs sa on sa.id = c.strategist_agent_run_id
left join public.agent_runs ra on ra.id = c.risk_agent_run_id
left join public.risk_evaluations re on re.consensus_decision_id = c.id
left join public.paper_trades t on t.consensus_decision_id = c.id;

revoke all on public.decision_replay from anon, authenticated;
grant select on public.decision_replay to service_role;
