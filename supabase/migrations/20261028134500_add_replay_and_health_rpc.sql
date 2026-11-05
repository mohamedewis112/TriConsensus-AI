-- Secure replay and health-center RPCs for TriConsensus preview.

create or replace function public.triconsensus_replay(p_token text, p_consensus_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';
begin
  if p_token is null or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode='42501';
  end if;

  return coalesce((
    select jsonb_build_object(
      'consensus', to_jsonb(c),
      'market_snapshot', coalesce(to_jsonb(ms), '{}'::jsonb),
      'market_agent', coalesce(to_jsonb(ma), '{}'::jsonb),
      'strategist_agent', coalesce(to_jsonb(sa), '{}'::jsonb),
      'risk_agent', coalesce(to_jsonb(ra), '{}'::jsonb),
      'ml_prediction', coalesce((
        select to_jsonb(mp)
        from public.ml_predictions mp
        where mp.market_snapshot_id=c.market_snapshot_id
        order by mp.created_at desc limit 1
      ), '{}'::jsonb),
      'risk', coalesce((
        select to_jsonb(r)
        from public.risk_evaluations r
        where r.consensus_decision_id=c.id
        order by r.created_at desc limit 1
      ), '{}'::jsonb),
      'trade', coalesce((
        select to_jsonb(t)
        from public.paper_trades t
        where t.consensus_decision_id=c.id
        order by t.created_at desc limit 1
      ), '{}'::jsonb),
      'trade_events', coalesce((
        select jsonb_agg(to_jsonb(te) order by te.event_time)
        from public.trade_events te
        join public.paper_trades t on t.id=te.trade_id
        where t.consensus_decision_id=c.id
      ), '[]'::jsonb)
    )
    from public.consensus_decisions c
    left join public.market_snapshots ms on ms.id=c.market_snapshot_id
    left join public.agent_runs ma on ma.id=c.market_agent_run_id
    left join public.agent_runs sa on sa.id=c.strategist_agent_run_id
    left join public.agent_runs ra on ra.id=c.risk_agent_run_id
    where c.id=p_consensus_id
  ), '{}'::jsonb);
end;
$$;

revoke all on function public.triconsensus_replay(text,uuid) from public, authenticated;
grant execute on function public.triconsensus_replay(text,uuid) to anon, service_role;

create or replace function public.triconsensus_health_snapshot(p_token text)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';
begin
  if p_token is null or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode='42501';
  end if;

  return jsonb_build_object(
    'generated_at', now(),
    'watchdog', coalesce((
      select to_jsonb(h) from (
        select status, checked_at, checks
        from public.strategy_forward_health_checks
        order by checked_at desc limit 1
      ) h
    ), '{}'::jsonb),
    'market_freshness', jsonb_build_object(
      'BTCUSDT', jsonb_build_object(
        'latest_candle', (select open_time from public.market_candles_15m where symbol='BTCUSDT' order by open_time desc limit 1),
        'latest_ratio', (select observed_at from public.market_binance_global_ratio_5m where symbol='BTCUSDT' order by observed_at desc limit 1)
      ),
      'ETHUSDT', jsonb_build_object(
        'latest_candle', (select open_time from public.market_candles_15m where symbol='ETHUSDT' order by open_time desc limit 1),
        'latest_ratio', (select observed_at from public.market_binance_global_ratio_5m where symbol='ETHUSDT' order by observed_at desc limit 1)
      )
    ),
    'runtime_capabilities', coalesce((
      select jsonb_agg(to_jsonb(rc) order by capability)
      from public.runtime_capabilities rc
    ), '[]'::jsonb),
    'cron_jobs', coalesce((
      select jsonb_agg(jsonb_build_object(
        'jobid', j.jobid,
        'jobname', j.jobname,
        'schedule', j.schedule,
        'active', j.active,
        'last_status', lr.status,
        'last_start', lr.start_time,
        'last_end', lr.end_time,
        'last_message', lr.return_message
      ) order by j.jobid)
      from cron.job j
      left join lateral (
        select status,start_time,end_time,return_message
        from cron.job_run_details d
        where d.jobid=j.jobid
        order by start_time desc
        limit 1
      ) lr on true
      where j.jobname like 'triconsensus_%'
         or j.jobname='v51-agent-orchestration-preflight'
    ), '[]'::jsonb),
    'execution_counts', jsonb_build_object(
      'agent_runs', (select count(*) from public.agent_runs),
      'consensus_decisions', (select count(*) from public.consensus_decisions),
      'risk_evaluations', (select count(*) from public.risk_evaluations),
      'paper_trades', (select count(*) from public.paper_trades)
    )
  );
end;
$$;

revoke all on function public.triconsensus_health_snapshot(text) from public, authenticated;
grant execute on function public.triconsensus_health_snapshot(text) to anon, service_role;
