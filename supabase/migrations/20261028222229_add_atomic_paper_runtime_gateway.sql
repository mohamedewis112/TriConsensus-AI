alter table public.consensus_decisions
  add column if not exists runtime_decision_key text;

create unique index if not exists consensus_decisions_runtime_decision_key_uidx
  on public.consensus_decisions(runtime_decision_key)
  where runtime_decision_key is not null;

create or replace function public.triconsensus_runtime_snapshot(p_token text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';
  v_experiment public.experiments%rowtype;
begin
  if p_token is null or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  select * into v_experiment
  from public.experiments
  where version = 'V5.1-PAPER'
  limit 1;

  return jsonb_build_object(
    'generated_at', now(),
    'experiment', coalesce(to_jsonb(v_experiment), '{}'::jsonb),
    'paper_trades', coalesce((
      select jsonb_agg(to_jsonb(t) order by t.created_at desc)
      from (
        select *
        from public.paper_trades
        where experiment_id = v_experiment.id
        order by created_at desc
        limit 200
      ) t
    ), '[]'::jsonb),
    'reward_memory', coalesce((
      select jsonb_agg(to_jsonb(r) order by r.created_at)
      from (
        select *
        from public.marl_reward_memory
        where experiment_id = v_experiment.id
        order by created_at desc
        limit 80
      ) r
    ), '[]'::jsonb)
  );
end;
$$;

revoke all on function public.triconsensus_runtime_snapshot(text)
  from public;
grant execute on function public.triconsensus_runtime_snapshot(text)
  to anon, authenticated, service_role;


create or replace function public.triconsensus_runtime_commit_decision(
  p_token text,
  p_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';

  v_symbol text;
  v_signal text;
  v_outcome text;
  v_context_ms bigint;
  v_key text;
  v_candidate boolean := false;
  v_full_consensus boolean := false;
  v_risk_approved boolean := false;

  v_experiment public.experiments%rowtype;
  v_calibration public.strategy_calibrations%rowtype;
  v_existing_consensus public.consensus_decisions%rowtype;
  v_existing_trade public.paper_trades%rowtype;

  v_market_id uuid;
  v_signal_id uuid;
  v_market_agent_id uuid;
  v_strategist_id uuid;
  v_risk_agent_id uuid;
  v_consensus_id uuid;
  v_risk_id uuid;
  v_trade_id uuid;

  v_market jsonb;
  v_features jsonb;
  v_agents jsonb;
  v_result jsonb;
  v_risk jsonb;
  v_proposal jsonb;
  v_v6 jsonb;
  v_fill jsonb;
  v_account jsonb;

  v_max_concurrent integer := 1;
  v_open_count integer := 0;
  v_entry numeric;
  v_qty numeric;
  v_idem_reason text;
begin
  if p_token is null or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  if coalesce((p_payload->>'paper_only')::boolean, false) is not true
     or coalesce((p_payload->>'live_trading')::boolean, true) is not false
     or coalesce((p_payload->>'v6_enforcement')::boolean, true) is not false then
    raise exception 'runtime safety invariant violation';
  end if;

  v_symbol := p_payload->>'symbol';
  v_signal := p_payload#>>'{v51_signal,signal}';
  v_outcome := p_payload#>>'{agent_result,outcome}';
  v_context_ms := (p_payload#>>'{v51_signal,context_timestamp_ms}')::bigint;

  if v_symbol not in ('BTC/USDT','ETH/USDT')
     or v_signal not in ('LONG','SHORT')
     or v_outcome not in ('LONG','SHORT','NO_TRADE') then
    raise exception 'invalid runtime decision identity';
  end if;

  if p_payload#>>'{v51_signal,strategy_version}' <> 'V5.1'
     or p_payload#>>'{v51_signal,feature_name}' <> 'global_long_short_change_1h'
     or p_payload#>>'{v51_signal,relationship}' <> 'negative'
     or (p_payload#>>'{v51_signal,horizon_minutes}')::integer <> 180
     or (p_payload#>>'{v51_signal,context_lag_minutes}')::integer <> 15
     or (p_payload#>>'{v51_signal,round_trip_research_cost}')::numeric <> 0.0015
     or p_payload#>>'{v51_signal,calibration_config_hash}' <> '84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef'
     or coalesce((p_payload#>>'{v51_signal,frozen_protocol}')::boolean, false) is not true then
    raise exception 'frozen V5.1 protocol mismatch';
  end if;

  if v_symbol = 'BTC/USDT' then
    if (p_payload#>>'{v51_signal,low_threshold}')::numeric <> -0.019545157125130854
       or (p_payload#>>'{v51_signal,high_threshold}')::numeric <> 0.020643022165240422 then
      raise exception 'BTC frozen thresholds mismatch';
    end if;
  else
    if (p_payload#>>'{v51_signal,low_threshold}')::numeric <> -0.01949802950955428
       or (p_payload#>>'{v51_signal,high_threshold}')::numeric <> 0.020743615937876794 then
      raise exception 'ETH frozen thresholds mismatch';
    end if;
  end if;

  if (p_payload#>>'{v51_signal,context_age_ms}')::bigint < 900000
     or (p_payload#>>'{v51_signal,context_age_ms}')::bigint > 2700000 then
    raise exception 'V5.1 context outside allowed lag/staleness window';
  end if;

  v_key := encode(
    digest(
      concat_ws('|', 'V5.1-PAPER', v_symbol, v_context_ms::text, v_signal),
      'sha256'
    ),
    'hex'
  );

  perform pg_advisory_xact_lock(hashtextextended(v_key, 0));

  select * into v_existing_consensus
  from public.consensus_decisions
  where runtime_decision_key = v_key
  limit 1;

  if found then
    select * into v_existing_trade
    from public.paper_trades
    where consensus_decision_id = v_existing_consensus.id
    limit 1;

    return jsonb_build_object(
      'committed', true,
      'duplicate', true,
      'runtime_decision_key', v_key,
      'consensus_id', v_existing_consensus.id,
      'trade_id', v_existing_trade.id,
      'trade_status', v_existing_trade.status,
      'opened', v_existing_trade.id is not null
    );
  end if;

  select * into v_experiment
  from public.experiments
  where version = 'V5.1-PAPER'
  limit 1;

  if not found then
    raise exception 'V5.1-PAPER experiment missing';
  end if;

  select * into v_calibration
  from public.strategy_calibrations
  where strategy_version = 'V5.1'
    and symbol = v_symbol
    and config_hash = '84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef'
    and low_threshold = (p_payload#>>'{v51_signal,low_threshold}')::numeric
    and high_threshold = (p_payload#>>'{v51_signal,high_threshold}')::numeric
  order by calibration_end desc, created_at desc
  limit 1;

  if not found then
    raise exception 'frozen calibration row missing';
  end if;

  v_market := p_payload->'market_snapshot';
  v_features := p_payload->'feature_snapshot';
  v_result := p_payload->'agent_result';
  v_agents := coalesce(v_result->'agents', '{}'::jsonb);
  v_risk := coalesce(v_result->'risk', '{}'::jsonb);
  v_proposal := coalesce(v_agents->'proposal', '{}'::jsonb);
  v_v6 := coalesce(v_result->'v6_adaptive', '{}'::jsonb);
  v_fill := coalesce(p_payload->'paper_fill', '{}'::jsonb);
  v_account := coalesce(p_payload->'account_state', '{}'::jsonb);

  if v_market is null or v_features is null then
    raise exception 'market/features missing';
  end if;

  insert into public.market_snapshots (
    experiment_id, symbol, primary_timeframe, captured_at, source,
    market_data, orderbook, funding_rate, open_interest,
    market_data_age_ms, checksum
  )
  values (
    v_experiment.id,
    v_symbol,
    '15m',
    (v_market#>>'{ticker,captured_at}')::timestamptz,
    'bybit',
    v_market,
    v_market->'orderbook',
    nullif(v_market#>>'{ticker,funding_rate}','')::numeric,
    nullif(v_market#>>'{ticker,open_interest}','')::numeric,
    coalesce((v_market->>'market_data_age_ms')::integer, 0),
    encode(digest(v_market::text, 'sha256'), 'hex')
  )
  returning id into v_market_id;

  insert into public.strategy_signal_snapshots (
    experiment_id, market_snapshot_id, calibration_id, strategy_version,
    symbol, observed_at, context_timestamp, feature_name, feature_value,
    low_threshold, high_threshold, signal, context_age_ms, reason,
    payload, checksum
  )
  values (
    v_experiment.id,
    v_market_id,
    v_calibration.id,
    'V5.1',
    v_symbol,
    coalesce((p_payload->>'observed_at')::timestamptz, now()),
    to_timestamp(v_context_ms / 1000.0),
    'global_long_short_change_1h',
    (p_payload#>>'{v51_signal,feature_value}')::numeric,
    (p_payload#>>'{v51_signal,low_threshold}')::numeric,
    (p_payload#>>'{v51_signal,high_threshold}')::numeric,
    v_signal,
    (p_payload#>>'{v51_signal,context_age_ms}')::bigint,
    p_payload#>>'{v51_signal,reason}',
    p_payload->'v51_signal',
    encode(digest((p_payload->'v51_signal')::text, 'sha256'), 'hex')
  )
  returning id into v_signal_id;

  insert into public.feature_snapshots (
    experiment_id, market_snapshot_id, symbol, feature_version,
    features, leakage_guard
  )
  values (
    v_experiment.id,
    v_market_id,
    v_symbol,
    'v1.5-runtime',
    v_features,
    jsonb_build_object(
      'open_candle_excluded', true,
      'ordered_oldest_to_newest', true,
      'llm_numeric_levels_restricted', true,
      'account_state_server_derived', true,
      'runtime_gateway', 'atomic_v1'
    )
  );

  if v_agents ? 'market' then
    insert into public.agent_runs (
      experiment_id, market_snapshot_id, agent_role, provider, model_id,
      prompt_version, input_context_hash, output_json, decision,
      confidence, evidence, is_valid
    )
    values (
      v_experiment.id, v_market_id, 'market_intelligence',
      coalesce(p_payload->>'llm_provider','vercel_ai_gateway'),
      coalesce(p_payload->>'llm_model','unknown'),
      'v1.5-runtime',
      encode(digest((p_payload - 'paper_fill')::text, 'sha256'), 'hex'),
      v_agents->'market',
      v_agents#>>'{market,bias}',
      nullif(v_agents#>>'{market,confidence}','')::numeric,
      coalesce(v_agents#>'{market,evidence}','[]'::jsonb),
      true
    )
    returning id into v_market_agent_id;
  end if;

  if v_agents ? 'proposal' then
    insert into public.agent_runs (
      experiment_id, market_snapshot_id, agent_role, provider, model_id,
      prompt_version, input_context_hash, output_json, decision,
      confidence, evidence, is_valid
    )
    values (
      v_experiment.id, v_market_id, 'trade_strategist',
      coalesce(p_payload->>'llm_provider','vercel_ai_gateway'),
      coalesce(p_payload->>'llm_model','unknown'),
      'v1.5-runtime',
      encode(digest((p_payload - 'paper_fill')::text, 'sha256'), 'hex'),
      v_agents->'proposal',
      v_agents#>>'{proposal,decision}',
      nullif(v_agents#>>'{proposal,confidence}','')::numeric,
      coalesce(v_agents#>'{proposal,evidence}','[]'::jsonb),
      coalesce(v_result->>'reason','') <> 'strategist_used_unapproved_numeric_levels'
    )
    returning id into v_strategist_id;
  end if;

  if v_agents ? 'critic' then
    insert into public.agent_runs (
      experiment_id, market_snapshot_id, agent_role, provider, model_id,
      prompt_version, input_context_hash, output_json, decision,
      confidence, evidence, is_valid
    )
    values (
      v_experiment.id, v_market_id, 'risk_advocate',
      coalesce(p_payload->>'llm_provider','vercel_ai_gateway'),
      coalesce(p_payload->>'llm_model','unknown'),
      'v1.5-runtime',
      encode(digest((p_payload - 'paper_fill')::text, 'sha256'), 'hex'),
      v_agents->'critic',
      v_agents#>>'{critic,verdict}',
      nullif(v_agents#>>'{critic,confidence}','')::numeric,
      coalesce(v_agents#>'{critic,evidence}','[]'::jsonb),
      true
    )
    returning id into v_risk_agent_id;
  end if;

  v_full_consensus := coalesce((v_result->>'full_consensus')::boolean, false);
  v_risk_approved := coalesce((v_risk->>'approved')::boolean, false);

  insert into public.consensus_decisions (
    experiment_id, market_snapshot_id,
    market_agent_run_id, strategist_agent_run_id, risk_agent_run_id,
    outcome, full_consensus, reason, runtime_decision_key
  )
  values (
    v_experiment.id, v_market_id,
    v_market_agent_id, v_strategist_id, v_risk_agent_id,
    case when v_outcome in ('LONG','SHORT') and v_full_consensus
         then v_outcome else 'NO_TRADE' end,
    v_full_consensus,
    coalesce(v_result->>'consensus_reason', v_result->>'reason', 'runtime_decision'),
    v_key
  )
  returning id into v_consensus_id;

  if jsonb_typeof(v_risk) = 'object' and v_risk <> '{}'::jsonb
     and jsonb_typeof(v_proposal) = 'object' and v_proposal <> '{}'::jsonb then
    v_entry := (
      select avg(value::numeric)
      from jsonb_array_elements_text(v_proposal->'entry_zone')
    );

    insert into public.risk_evaluations (
      experiment_id, consensus_decision_id, approved, checks,
      proposed_entry, stop_loss, take_profit_1, take_profit_2,
      risk_reward, risk_fraction, position_size, max_exposure_after,
      spread_bps, slippage_bps, rejection_reason
    )
    values (
      v_experiment.id,
      v_consensus_id,
      v_risk_approved,
      jsonb_build_object(
        'reason', v_risk->>'reason',
        'numeric_levels_validated', true,
        'full_consensus', v_full_consensus,
        'account_state', v_account,
        'v6_adaptive', v_v6,
        'runtime_gateway', 'atomic_v1'
      ),
      v_entry,
      nullif(v_proposal->>'stop_loss','')::numeric,
      nullif(v_proposal->>'take_profit_1','')::numeric,
      nullif(v_proposal->>'take_profit_2','')::numeric,
      nullif(v_risk->>'risk_reward','')::numeric,
      coalesce((v_experiment.config->>'risk_per_trade')::numeric, 0.0025),
      nullif(v_risk->>'position_size','')::numeric,
      nullif(v_risk->>'projected_exposure','')::numeric,
      nullif(p_payload#>>'{feature_snapshot,primary,spread_bps}','')::numeric,
      nullif(v_account->>'slippage_bps','')::numeric,
      case when v_risk_approved then null else v_risk->>'reason' end
    )
    returning id into v_risk_id;
  end if;

  if v_v6 ? 'portfolio' then
    insert into public.marl_action_logs (
      experiment_id, market_snapshot_id, signal_snapshot_id,
      agent_role, policy_version, state_json, action_json,
      projected_action_json, shadow_mode, vetoed_by_risk
    )
    values (
      v_experiment.id, v_market_id, v_signal_id,
      'portfolio',
      coalesce(v_v6#>>'{portfolio,policy_version}','v6-shadow'),
      coalesce(p_payload->'v6_adaptive_context','{}'::jsonb),
      v_v6->'portfolio',
      v_v6->'portfolio',
      true,
      not v_risk_approved
    )
    on conflict (signal_snapshot_id, agent_role) do nothing;
  end if;

  if v_v6 ? 'dynamic_risk' then
    insert into public.marl_action_logs (
      experiment_id, market_snapshot_id, signal_snapshot_id,
      agent_role, policy_version, state_json, action_json,
      projected_action_json, shadow_mode, vetoed_by_risk
    )
    values (
      v_experiment.id, v_market_id, v_signal_id,
      'dynamic_risk',
      coalesce(v_v6#>>'{dynamic_risk,policy_version}','v6-shadow'),
      coalesce(p_payload->'v6_adaptive_context','{}'::jsonb),
      v_v6->'dynamic_risk',
      v_v6->'dynamic_risk',
      true,
      not v_risk_approved
    )
    on conflict (signal_snapshot_id, agent_role) do nothing;
  end if;

  v_candidate :=
    v_outcome in ('LONG','SHORT')
    and v_outcome = v_signal
    and v_full_consensus
    and v_risk_approved
    and coalesce((p_payload#>>'{protocol_gate,v51_direction_match}')::boolean, false)
    and coalesce((p_payload#>>'{protocol_gate,deterministic_risk_approved}')::boolean, false)
    and coalesce((p_payload#>>'{protocol_gate,research_paper_candidate}')::boolean, false);

  if not v_candidate then
    insert into public.system_events (
      experiment_id, severity, component, event_type, message, payload
    )
    values (
      v_experiment.id, 'info', 'paper_runtime', 'NO_TRADE',
      'Runtime decision committed without opening a paper trade',
      jsonb_build_object(
        'symbol', v_symbol,
        'runtime_decision_key', v_key,
        'signal', v_signal,
        'outcome', v_outcome,
        'full_consensus', v_full_consensus,
        'risk_approved', v_risk_approved
      )
    );

    return jsonb_build_object(
      'committed', true,
      'duplicate', false,
      'runtime_decision_key', v_key,
      'consensus_id', v_consensus_id,
      'opened', false,
      'reason', 'decision_not_qualified'
    );
  end if;

  if v_risk_id is null then
    raise exception 'qualified decision missing risk audit';
  end if;

  v_max_concurrent := greatest(
    1,
    coalesce((v_experiment.config->>'max_concurrent_trades')::integer, 1)
  );

  select count(*) into v_open_count
  from public.paper_trades
  where experiment_id = v_experiment.id
    and status = 'open';

  if v_open_count >= v_max_concurrent then
    v_idem_reason := 'max_concurrent_trades';
  elsif exists (
    select 1 from public.paper_trades
    where experiment_id = v_experiment.id
      and status = 'open'
      and symbol = v_symbol
  ) then
    v_idem_reason := 'symbol_already_open';
  end if;

  if v_idem_reason is not null then
    insert into public.system_events (
      experiment_id, severity, component, event_type, message, payload
    )
    values (
      v_experiment.id, 'warning', 'paper_runtime', 'NO_TRADE',
      'Qualified paper decision blocked by runtime concurrency guard',
      jsonb_build_object(
        'symbol', v_symbol,
        'runtime_decision_key', v_key,
        'reason', v_idem_reason,
        'open_count', v_open_count,
        'max_concurrent', v_max_concurrent
      )
    );

    return jsonb_build_object(
      'committed', true,
      'duplicate', false,
      'runtime_decision_key', v_key,
      'consensus_id', v_consensus_id,
      'opened', false,
      'reason', v_idem_reason
    );
  end if;

  v_entry := nullif(v_fill->>'requested_price','')::numeric;
  v_qty := nullif(v_fill->>'quantity','')::numeric;

  if v_entry is null or v_entry <= 0
     or v_qty is null or v_qty <= 0
     or nullif(v_fill->>'fill_price','')::numeric <= 0 then
    raise exception 'invalid simulated paper fill';
  end if;

  insert into public.paper_trades (
    experiment_id, consensus_decision_id, risk_evaluation_id,
    symbol, side, status, requested_entry, actual_entry,
    stop_loss, take_profit_1, take_profit_2,
    position_size, notional_value, estimated_fees, realized_fees,
    estimated_slippage, realized_slippage, opened_at
  )
  values (
    v_experiment.id, v_consensus_id, v_risk_id,
    v_symbol, v_outcome, 'open',
    v_entry,
    (v_fill->>'fill_price')::numeric,
    (v_proposal->>'stop_loss')::numeric,
    (v_proposal->>'take_profit_1')::numeric,
    nullif(v_proposal->>'take_profit_2','')::numeric,
    v_qty,
    (v_fill->>'fill_price')::numeric * v_qty,
    coalesce((v_fill->>'fee')::numeric,0),
    coalesce((v_fill->>'fee')::numeric,0),
    coalesce((v_fill->>'slippage_cost')::numeric,0),
    coalesce((v_fill->>'slippage_cost')::numeric,0),
    coalesce((p_payload->>'opened_at')::timestamptz, now())
  )
  returning id into v_trade_id;

  insert into public.trade_events (
    experiment_id, trade_id, event_type, event_time, payload
  )
  values (
    v_experiment.id, v_trade_id, 'PAPER_OPEN',
    coalesce((p_payload->>'opened_at')::timestamptz, now()),
    jsonb_build_object(
      'fill', v_fill,
      'account_state_at_entry', v_account,
      'v6_adaptive_context', coalesce(p_payload->'v6_adaptive_context','{}'::jsonb),
      'v6_adaptive', v_v6,
      'runtime_decision_key', v_key,
      'paper_only', true,
      'live_trading', false
    )
  );

  return jsonb_build_object(
    'committed', true,
    'duplicate', false,
    'runtime_decision_key', v_key,
    'consensus_id', v_consensus_id,
    'risk_evaluation_id', v_risk_id,
    'trade_id', v_trade_id,
    'trade_status', 'open',
    'opened', true
  );

exception
  when unique_violation then
    select * into v_existing_consensus
    from public.consensus_decisions
    where runtime_decision_key = v_key
    limit 1;

    if found then
      select * into v_existing_trade
      from public.paper_trades
      where consensus_decision_id = v_existing_consensus.id
      limit 1;

      return jsonb_build_object(
        'committed', true,
        'duplicate', true,
        'runtime_decision_key', v_key,
        'consensus_id', v_existing_consensus.id,
        'trade_id', v_existing_trade.id,
        'trade_status', v_existing_trade.status,
        'opened', v_existing_trade.id is not null
      );
    end if;
    raise;
end;
$$;

revoke all on function public.triconsensus_runtime_commit_decision(text,jsonb)
  from public;
grant execute on function public.triconsensus_runtime_commit_decision(text,jsonb)
  to anon, authenticated, service_role;


create or replace function public.triconsensus_runtime_monitor_paper_trade(
  p_token text,
  p_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';

  v_trade public.paper_trades%rowtype;
  v_experiment public.experiments%rowtype;
  v_trade_id uuid;
  v_action text;
  v_mae numeric;
  v_mfe numeric;
  v_open_payload jsonb;
  v_adaptive jsonb;
  v_policy_version text;
  v_notional numeric;
  v_net_return numeric;
  v_max_drawdown numeric;
  v_reward numeric;
  v_state jsonb;
  v_checksum text;
begin
  if p_token is null or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  if coalesce((p_payload->>'paper_only')::boolean, false) is not true
     or coalesce((p_payload->>'live_trading')::boolean, true) is not false
     or coalesce((p_payload->>'v6_enforcement')::boolean, true) is not false then
    raise exception 'runtime safety invariant violation';
  end if;

  v_trade_id := (p_payload->>'trade_id')::uuid;
  v_action := upper(coalesce(p_payload->>'action','MONITOR'));

  perform pg_advisory_xact_lock(hashtextextended(v_trade_id::text, 0));

  select * into v_trade
  from public.paper_trades
  where id = v_trade_id
  for update;

  if not found then
    raise exception 'paper trade not found';
  end if;

  select * into v_experiment
  from public.experiments
  where id = v_trade.experiment_id;

  if v_experiment.version <> 'V5.1-PAPER'
     or v_experiment.execution_mode <> 'paper' then
    raise exception 'unexpected experiment for runtime monitor';
  end if;

  if v_trade.status = 'closed' then
    return jsonb_build_object(
      'updated', true,
      'duplicate', true,
      'trade_id', v_trade.id,
      'status', v_trade.status,
      'close_reason', v_trade.close_reason,
      'pnl_net', v_trade.pnl_net
    );
  end if;

  if v_trade.status <> 'open' then
    raise exception 'paper trade is not open';
  end if;

  v_mae := coalesce((p_payload->>'mae')::numeric, v_trade.mae, 0);
  v_mfe := coalesce((p_payload->>'mfe')::numeric, v_trade.mfe, 0);

  if v_action = 'MONITOR' then
    update public.paper_trades
    set mae = least(coalesce(mae,0), v_mae, 0),
        mfe = greatest(coalesce(mfe,0), v_mfe, 0)
    where id = v_trade.id;

    return jsonb_build_object(
      'updated', true,
      'duplicate', false,
      'trade_id', v_trade.id,
      'status', 'open',
      'action', 'MONITORED'
    );
  end if;

  if v_action <> 'CLOSE' then
    raise exception 'invalid monitor action';
  end if;

  if p_payload->>'close_reason' not in ('STOP_LOSS','TAKE_PROFIT_1','TRAILING_PROFIT','HORIZON_LIMIT') then
    raise exception 'invalid paper close reason';
  end if;

  update public.paper_trades
  set status = 'closed',
      requested_exit = (p_payload->>'requested_exit')::numeric,
      actual_exit = (p_payload->>'actual_exit')::numeric,
      realized_fees = (p_payload->>'realized_fees')::numeric,
      realized_slippage = (p_payload->>'realized_slippage')::numeric,
      pnl_gross = (p_payload->>'pnl_gross')::numeric,
      pnl_net = (p_payload->>'pnl_net')::numeric,
      mae = least(coalesce(mae,0), v_mae, 0),
      mfe = greatest(coalesce(mfe,0), v_mfe, 0),
      closed_at = coalesce((p_payload->>'closed_at')::timestamptz, now()),
      close_reason = p_payload->>'close_reason',
      trade_duration_seconds = (p_payload->>'trade_duration_seconds')::bigint
  where id = v_trade.id;

  insert into public.trade_events (
    experiment_id, trade_id, event_type, event_time, payload
  )
  values (
    v_trade.experiment_id,
    v_trade.id,
    'PAPER_CLOSE',
    coalesce((p_payload->>'closed_at')::timestamptz, now()),
    jsonb_build_object(
      'reason', p_payload->>'close_reason',
      'fill', coalesce(p_payload->'fill','{}'::jsonb),
      'pnl', jsonb_build_object(
        'gross', (p_payload->>'pnl_gross')::numeric,
        'fees', (p_payload->>'realized_fees')::numeric,
        'net', (p_payload->>'pnl_net')::numeric
      ),
      'paper_only', true,
      'live_trading', false
    )
  );

  select payload into v_open_payload
  from public.trade_events
  where trade_id = v_trade.id
    and event_type = 'PAPER_OPEN'
  order by event_time
  limit 1;

  v_adaptive := coalesce(v_open_payload->'v6_adaptive', '{}'::jsonb);

  if v_adaptive <> '{}'::jsonb then
    v_policy_version := coalesce(
      v_adaptive#>>'{portfolio,policy_version}',
      v_adaptive#>>'{dynamic_risk,policy_version}',
      'v6-shadow'
    );
    v_notional := greatest(coalesce(v_trade.notional_value,0), 0.000000000001);
    v_net_return := (p_payload->>'pnl_net')::numeric / v_notional;
    v_max_drawdown := least(
      1,
      abs(least(v_mae,0)) / v_notional
    );
    v_reward := v_net_return - (2 * v_max_drawdown);

    v_state :=
      coalesce(v_open_payload->'v6_adaptive_context','{}'::jsonb)
      || jsonb_build_object(
        'account_state_at_entry', coalesce(v_open_payload->'account_state_at_entry','{}'::jsonb),
        'close_reason', p_payload->>'close_reason',
        'trade_duration_seconds', (p_payload->>'trade_duration_seconds')::bigint,
        'mae', v_mae,
        'mfe', v_mfe
      );

    v_checksum := encode(
      digest(
        jsonb_build_object(
          'experiment_id', v_trade.experiment_id,
          'trade_id', v_trade.id,
          'symbol', v_trade.symbol,
          'policy_version', v_policy_version,
          'state_json', v_state,
          'action_json', v_adaptive,
          'net_return', v_net_return,
          'max_drawdown', v_max_drawdown,
          'reward', v_reward,
          'terminal', true
        )::text,
        'sha256'
      ),
      'hex'
    );

    insert into public.marl_reward_memory (
      experiment_id, trade_id, symbol, policy_version,
      state_json, action_json, net_return, max_drawdown,
      reward, terminal, checksum
    )
    values (
      v_trade.experiment_id, v_trade.id, v_trade.symbol, v_policy_version,
      v_state, v_adaptive, v_net_return, v_max_drawdown,
      v_reward, true, v_checksum
    )
    on conflict (trade_id) do nothing;
  end if;

  return jsonb_build_object(
    'updated', true,
    'duplicate', false,
    'trade_id', v_trade.id,
    'status', 'closed',
    'action', 'CLOSED',
    'close_reason', p_payload->>'close_reason',
    'pnl_net', (p_payload->>'pnl_net')::numeric,
    'reward_written', v_adaptive <> '{}'::jsonb
  );
end;
$$;

revoke all on function public.triconsensus_runtime_monitor_paper_trade(text,jsonb)
  from public;
grant execute on function public.triconsensus_runtime_monitor_paper_trade(text,jsonb)
  to anon, authenticated, service_role;

