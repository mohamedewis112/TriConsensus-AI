-- TriConsensus V5.1 live operations, finance telemetry, and fail-closed orchestration preflight.

create table if not exists public.runtime_capabilities (
  capability text primary key,
  enabled boolean not null default false,
  reason text,
  updated_at timestamptz not null default now()
);
alter table public.runtime_capabilities enable row level security;

insert into public.runtime_capabilities(capability, enabled, reason)
values ('external_llm_runtime', false, 'OPENROUTER_API_KEY_NOT_CONFIGURED')
on conflict (capability) do nothing;

create table if not exists public.agent_orchestration_attempts (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid references public.experiments(id),
  signal_snapshot_id uuid not null unique references public.strategy_signal_snapshots(id),
  symbol text not null,
  signal text not null check (signal in ('LONG','SHORT')),
  context_timestamp timestamptz not null,
  status text not null check (status in ('READY','BLOCKED','DISPATCHED','FAILED')),
  reason text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
alter table public.agent_orchestration_attempts enable row level security;

create index if not exists agent_orchestration_attempts_created_at_idx
  on public.agent_orchestration_attempts(created_at desc);
create index if not exists agent_orchestration_attempts_experiment_id_idx
  on public.agent_orchestration_attempts(experiment_id);

create or replace function public.reject_agent_orchestration_attempt_mutation()
returns trigger
language plpgsql
set search_path = public
as $
begin
  raise exception 'agent_orchestration_attempts is append-only';
end;
$$;

drop trigger if exists agent_orchestration_attempts_no_update on public.agent_orchestration_attempts;
create trigger agent_orchestration_attempts_no_update
before update on public.agent_orchestration_attempts
for each row execute function public.reject_agent_orchestration_attempt_mutation();

drop trigger if exists agent_orchestration_attempts_no_delete on public.agent_orchestration_attempts;
create trigger agent_orchestration_attempts_no_delete
before delete on public.agent_orchestration_attempts
for each row execute function public.reject_agent_orchestration_attempt_mutation();

create or replace function public.v51_agent_orchestration_preflight()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  inserted_count integer;
  runtime_enabled boolean;
  runtime_reason text;
begin
  select enabled, coalesce(reason,'EXTERNAL_LLM_RUNTIME_DISABLED')
    into runtime_enabled, runtime_reason
  from public.runtime_capabilities
  where capability='external_llm_runtime';

  runtime_enabled := coalesce(runtime_enabled, false);
  runtime_reason := coalesce(runtime_reason, 'EXTERNAL_LLM_RUNTIME_NOT_CONFIGURED');

  insert into public.agent_orchestration_attempts(
    experiment_id, signal_snapshot_id, symbol, signal, context_timestamp,
    status, reason, payload
  )
  select
    s.experiment_id,
    s.id,
    s.symbol,
    s.signal,
    s.context_timestamp,
    case when runtime_enabled then 'READY' else 'BLOCKED' end,
    case when runtime_enabled then 'AGENT_RUNTIME_READY' else runtime_reason end,
    jsonb_build_object(
      'strategy_version', s.strategy_version,
      'feature_value', s.feature_value,
      'low_threshold', s.low_threshold,
      'high_threshold', s.high_threshold,
      'default_action', 'NO_TRADE',
      'paper_only', true
    )
  from public.strategy_signal_snapshots s
  where s.payload->>'recorder'='supabase_pg_cron_v1'
    and s.signal in ('LONG','SHORT')
    and not exists (
      select 1 from public.agent_orchestration_attempts a
      where a.signal_snapshot_id=s.id
    )
  order by s.context_timestamp
  on conflict (signal_snapshot_id) do nothing;

  get diagnostics inserted_count = row_count;
  return inserted_count;
end;
$$;

revoke all on function public.v51_agent_orchestration_preflight() from public, anon, authenticated;
grant execute on function public.v51_agent_orchestration_preflight() to service_role;

do $$
declare existing_job bigint;
begin
  select jobid into existing_job
  from cron.job
  where jobname='v51-agent-orchestration-preflight'
  limit 1;

  if existing_job is null then
    perform cron.schedule(
      'v51-agent-orchestration-preflight',
      '* * * * *',
      'select public.v51_agent_orchestration_preflight();'
    );
  end if;
end $$;

create or replace function public.triconsensus_live_snapshot(p_token text, p_limit integer default 80)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';
  safe_limit integer := greatest(10, least(coalesce(p_limit, 80), 200));
begin
  if p_token is null or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  return jsonb_build_object(
    'generated_at', now(),
    'health', coalesce((
      select to_jsonb(h) from (
        select status, checked_at, checks
        from public.strategy_forward_health_checks
        order by checked_at desc limit 1
      ) h
    ), '{}'::jsonb),
    'latest_signals', coalesce((
      select jsonb_agg(to_jsonb(s) order by s.symbol)
      from (
        select distinct on (symbol)
          symbol, context_timestamp, feature_value, low_threshold, high_threshold,
          signal, reason, created_at
        from public.strategy_signal_snapshots
        where payload->>'recorder'='supabase_pg_cron_v1'
        order by symbol, context_timestamp desc
      ) s
    ), '[]'::jsonb),
    'finance', jsonb_build_object(
      'paper', jsonb_build_object(
        'starting_balance', 10000.0,
        'realized_pnl', coalesce((select sum(pnl_net)::float8 from public.paper_trades where status='closed'),0),
        'unrealized_pnl', coalesce((
          select sum(
            case
              when upper(t.side)='LONG' then (p.last_price - coalesce(t.actual_entry,t.requested_entry,0)::float8) * coalesce(t.position_size,0)::float8
              when upper(t.side)='SHORT' then (coalesce(t.actual_entry,t.requested_entry,0)::float8 - p.last_price) * coalesce(t.position_size,0)::float8
              else 0
            end
          )
          from public.paper_trades t
          cross join lateral (
            select c.close_price::float8 as last_price
            from public.market_candles_15m c
            where replace(replace(upper(c.symbol),'/',''),'-','') = replace(replace(upper(t.symbol),'/',''),'-','')
            order by c.open_time desc
            limit 1
          ) p
          where t.status='open'
        ),0),
        'fees', coalesce((select sum(coalesce(realized_fees,estimated_fees,0))::float8 from public.paper_trades),0),
        'open_positions', (select count(*) from public.paper_trades where status='open'),
        'closed_trades', (select count(*) from public.paper_trades where status='closed'),
        'wins', (select count(*) from public.paper_trades where status='closed' and coalesce(pnl_net,0)>0),
        'losses', (select count(*) from public.paper_trades where status='closed' and coalesce(pnl_net,0)<0),
        'gross_profit', coalesce((select sum(pnl_net)::float8 from public.paper_trades where status='closed' and pnl_net>0),0),
        'gross_loss', abs(coalesce((select sum(pnl_net)::float8 from public.paper_trades where status='closed' and pnl_net<0),0)),
        'notional_open', coalesce((select sum(notional_value)::float8 from public.paper_trades where status='open'),0)
      ),
      'research_outcomes', coalesce((
        select jsonb_agg(to_jsonb(o) order by o.entry_context_timestamp)
        from (
          select o.symbol, o.side, o.entry_price::float8, o.exit_price::float8,
                 o.gross_return::float8, o.net_return::float8, o.hit,
                 o.entry_context_timestamp, o.exit_candle_open_time, o.evaluated_at
          from public.strategy_forward_rule_outcomes o
          join public.strategy_forward_rule_selections s on s.id=o.selection_id
          where s.payload->>'capture_mode'='reconstructed_oos'
          order by o.entry_context_timestamp
        ) o
      ), '[]'::jsonb)
    ),
    'events', coalesce((
      select jsonb_agg(to_jsonb(e) order by e.event_time desc)
      from (
        select * from (
          select created_at as event_time, 'SIGNAL'::text as kind, symbol as subject,
                 signal as state, reason as title,
                 jsonb_build_object('feature',feature_value,'low',low_threshold,'high',high_threshold,'context',context_timestamp) as details
          from public.strategy_signal_snapshots
          where payload->>'recorder'='supabase_pg_cron_v1'

          union all
          select created_at, 'AGENT', agent_role, coalesce(decision,'FAILED'),
                 coalesce(error_code,'Agent decision'),
                 jsonb_build_object('confidence',confidence,'provider',provider,'model',model_id,'valid',is_valid,'latency_ms',latency_ms)
          from public.agent_runs

          union all
          select created_at, 'CONSENSUS', '3-agent consensus', outcome,
                 coalesce(reason,'Consensus decision'),
                 jsonb_build_object('full_consensus',full_consensus,'id',id)
          from public.consensus_decisions

          union all
          select created_at, 'RISK', 'Deterministic risk engine',
                 case when approved then 'APPROVED' else 'VETO' end,
                 coalesce(rejection_reason,'Risk evaluation'),
                 jsonb_build_object('entry',proposed_entry,'stop',stop_loss,'tp1',take_profit_1,'tp2',take_profit_2,'rr',risk_reward,'risk_fraction',risk_fraction,'size',position_size)
          from public.risk_evaluations

          union all
          select created_at, 'TRADE', symbol, upper(status),
                 coalesce(close_reason, side || ' paper trade'),
                 jsonb_build_object('side',side,'entry',actual_entry,'exit',actual_exit,'pnl_net',pnl_net,'stop',stop_loss,'tp1',take_profit_1,'tp2',take_profit_2)
          from public.paper_trades

          union all
          select event_time, 'TRADE_EVENT', coalesce(trade_id::text,'trade'), upper(event_type),
                 event_type, payload
          from public.trade_events

          union all
          select checked_at, 'WATCHDOG', 'forward pipeline', upper(status),
                 'Forward watchdog check', checks
          from public.strategy_forward_health_checks

          union all
          select selected_at, 'OOS_SELECTION', symbol, side,
                 'Frozen-rule OOS selection',
                 jsonb_build_object('entry',entry_price,'context',context_timestamp,'exit_due',exit_candle_open_time,'capture_mode',payload->>'capture_mode')
          from public.strategy_forward_rule_selections

          union all
          select evaluated_at, 'OOS_OUTCOME', symbol,
                 case when hit then 'WIN' else 'LOSS' end,
                 side || ' OOS outcome',
                 jsonb_build_object('net_return',net_return,'gross_return',gross_return,'entry',entry_price,'exit',exit_price,'context',entry_context_timestamp)
          from public.strategy_forward_rule_outcomes
        ) u
        order by event_time desc
        limit safe_limit
      ) e
    ), '[]'::jsonb),
    'agents', coalesce((
      select jsonb_agg(to_jsonb(a) order by a.agent_role)
      from (
        select distinct on (agent_role)
          agent_role, decision, confidence, provider, model_id, is_valid,
          error_code, latency_ms, created_at
        from public.agent_runs
        order by agent_role, created_at desc
      ) a
    ), '[]'::jsonb),
    'latest_consensus', coalesce((
      select to_jsonb(c) from (
        select id, outcome, full_consensus, reason, created_at
        from public.consensus_decisions order by created_at desc limit 1
      ) c
    ), '{}'::jsonb),
    'latest_risk', coalesce((
      select to_jsonb(r) from (
        select approved, proposed_entry, stop_loss, take_profit_1, take_profit_2,
               risk_reward, risk_fraction, position_size, rejection_reason, created_at
        from public.risk_evaluations order by created_at desc limit 1
      ) r
    ), '{}'::jsonb),
    'open_trades', coalesce((
      select jsonb_agg(to_jsonb(t) order by t.created_at desc)
      from public.paper_trades t where t.status='open'
    ), '[]'::jsonb)
  );
end;
$$;

revoke all on function public.triconsensus_live_snapshot(text,integer) from public;
grant execute on function public.triconsensus_live_snapshot(text,integer) to anon, service_role;
revoke execute on function public.triconsensus_dashboard_snapshot(text) from authenticated;

create or replace function public.triconsensus_orchestration_status(p_token text)
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
    'capability', coalesce((
      select to_jsonb(c) from (
        select capability, enabled, reason, updated_at
        from public.runtime_capabilities
        where capability='external_llm_runtime'
      ) c
    ), '{}'::jsonb),
    'attempts', coalesce((
      select jsonb_agg(to_jsonb(a) order by a.created_at desc)
      from (
        select id, signal_snapshot_id, symbol, signal, context_timestamp,
               status, reason, payload, created_at
        from public.agent_orchestration_attempts
        order by created_at desc
        limit 30
      ) a
    ), '[]'::jsonb)
  );
end;
$$;

revoke all on function public.triconsensus_orchestration_status(text) from public;
grant execute on function public.triconsensus_orchestration_status(text) to anon, service_role;
