create view public.decision_replay
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
  t.close_reason
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
