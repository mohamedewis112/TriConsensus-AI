create index if not exists idx_agent_runs_experiment
  on public.agent_runs(experiment_id);

create index if not exists idx_consensus_market_snapshot
  on public.consensus_decisions(market_snapshot_id);
create index if not exists idx_consensus_market_agent
  on public.consensus_decisions(market_agent_run_id);
create index if not exists idx_consensus_strategist_agent
  on public.consensus_decisions(strategist_agent_run_id);
create index if not exists idx_consensus_risk_agent
  on public.consensus_decisions(risk_agent_run_id);

create index if not exists idx_feature_snapshots_experiment
  on public.feature_snapshots(experiment_id);
create index if not exists idx_ml_predictions_experiment
  on public.ml_predictions(experiment_id);
create index if not exists idx_risk_evaluations_experiment
  on public.risk_evaluations(experiment_id);
create index if not exists idx_trade_events_experiment
  on public.trade_events(experiment_id);

create policy "deny_client_access_experiments"
on public.experiments as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_market_snapshots"
on public.market_snapshots as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_feature_snapshots"
on public.feature_snapshots as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_ml_predictions"
on public.ml_predictions as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_agent_runs"
on public.agent_runs as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_consensus_decisions"
on public.consensus_decisions as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_risk_evaluations"
on public.risk_evaluations as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_paper_trades"
on public.paper_trades as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_trade_events"
on public.trade_events as restrictive for all to anon, authenticated
using (false) with check (false);

create policy "deny_client_access_system_events"
on public.system_events as restrictive for all to anon, authenticated
using (false) with check (false);
