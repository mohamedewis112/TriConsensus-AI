alter table public.paper_trades
  add column if not exists strategy_signal_snapshot_id uuid
  references public.strategy_signal_snapshots(id) on delete restrict;

create unique index if not exists paper_trades_strategy_signal_snapshot_id_key
  on public.paper_trades(strategy_signal_snapshot_id)
  where strategy_signal_snapshot_id is not null;

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
     or new.strategy_signal_snapshot_id is distinct from old.strategy_signal_snapshot_id
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

create or replace function public.triconsensus_open_paper_trade(
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
  frozen_experiment_hash constant text := 'b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0';
  frozen_calibration_hash constant text := '84c9370ad5c25383cef7b7c00be15b24d0c5f6f636742e925a78779b47a017ef';
  v_experiment public.experiments%rowtype;
  v_calibration public.strategy_calibrations%rowtype;
  v_signal public.strategy_signal_snapshots%rowtype;
  v_existing_trade public.paper_trades%rowtype;
  v_market public.market_snapshots%rowtype;
  v_feature public.feature_snapshots%rowtype;
  v_consensus public.consensus_decisions%rowtype;
  v_risk public.risk_evaluations%rowtype;
  v_trade public.paper_trades%rowtype;
  v_symbol text;
  v_side text;
  v_context_timestamp timestamptz;
  v_observed_at timestamptz;
  v_feature_value numeric;
  v_low numeric;
  v_high numeric;
  v_expected_low numeric;
  v_expected_high numeric;
  v_agent_market_id uuid;
  v_agent_strategist_id uuid;
  v_agent_risk_id uuid;
  v_agent jsonb;
  v_market_payload jsonb;
  v_feature_payload jsonb;
  v_agent_result jsonb;
  v_risk_payload jsonb;
  v_fill jsonb;
  v_adaptive jsonb;
  v_adaptive_context jsonb;
  v_account_state jsonb;
  v_input_hash text;
  v_signal_checksum text;
  v_market_checksum text;
  v_open_count integer;
  v_max_open integer;
  v_provider text;
  v_model_id text;
begin
  if p_token is null
     or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  if coalesce((p_payload->>'paper_only')::boolean, false) is not true
     or coalesce((p_payload->>'live_trading')::boolean, true) is not false then
    raise exception 'paper-only contract violation';
  end if;

  if coalesce((p_payload->>'v6_enforcement')::boolean, true) is not false then
    raise exception 'v6 enforcement is forbidden';
  end if;

  v_symbol := p_payload->>'symbol';
  v_side := p_payload->>'side';
  if v_symbol not in ('BTC/USDT','ETH/USDT') or v_side not in ('LONG','SHORT') then
    raise exception 'invalid symbol/side';
  end if;

  if p_payload #>> '{strategy,strategy_version}' <> 'V5.1'
     or p_payload #>> '{strategy,feature_name}' <> 'global_long_short_change_1h'
     or p_payload #>> '{strategy,relationship}' <> 'negative'
     or (p_payload #>> '{strategy,horizon_minutes}')::integer <> 180
     or (p_payload #>> '{strategy,context_lag_minutes}')::integer <> 15
     or (p_payload #>> '{strategy,round_trip_research_cost}')::numeric <> 0.0015
     or coalesce((p_payload #>> '{strategy,frozen_protocol}')::boolean,false) is not true
     or p_payload #>> '{strategy,signal}' <> v_side
     or p_payload #>> '{strategy,calibration_config_hash}' <> frozen_calibration_hash then
    raise exception 'frozen V5.1 protocol mismatch';
  end if;

  v_low := (p_payload #>> '{strategy,low_threshold}')::numeric;
  v_high := (p_payload #>> '{strategy,high_threshold}')::numeric;
  if v_symbol = 'BTC/USDT' then
    v_expected_low := -0.019545157125130854;
    v_expected_high := 0.020643022165240422;
  else
    v_expected_low := -0.01949802950955428;
    v_expected_high := 0.020743615937876794;
  end if;
  if v_low <> v_expected_low or v_high <> v_expected_high then
    raise exception 'frozen V5.1 thresholds mismatch';
  end if;

  select *
    into v_experiment
  from public.experiments
  where version='V5.1-PAPER'
  order by created_at desc
  limit 1;

  if v_experiment.id is null
     or v_experiment.execution_mode <> 'paper'
     or v_experiment.config_hash <> frozen_experiment_hash then
    raise exception 'frozen V5.1 experiment identity mismatch';
  end if;

  select *
    into v_calibration
  from public.strategy_calibrations
  where strategy_version='V5.1'
    and symbol=v_symbol
    and low_threshold=v_expected_low
    and high_threshold=v_expected_high
    and horizon_minutes=180
    and context_lag_minutes=15
    and round_trip_cost=0.0015
    and config_hash=frozen_calibration_hash
    and expires_at > now()
  order by created_at desc
  limit 1;

  if v_calibration.id is null then
    raise exception 'active frozen calibration missing';
  end if;

  v_context_timestamp := to_timestamp(
    (p_payload #>> '{strategy,context_timestamp_ms}')::double precision / 1000.0
  );
  v_observed_at := coalesce(
    nullif(p_payload->>'observed_at','')::timestamptz,
    now()
  );
  v_feature_value := (p_payload #>> '{strategy,feature_value}')::numeric;

  if (p_payload #>> '{strategy,context_age_ms}')::bigint < 0
     or (p_payload #>> '{strategy,context_age_ms}')::bigint > 2700000 then
    raise exception 'stale V5.1 context';
  end if;

  v_agent_result := p_payload->'agent_result';
  v_risk_payload := v_agent_result->'risk';
  if coalesce((v_agent_result->>'full_consensus')::boolean,false) is not true
     or v_agent_result->>'consensus_outcome' <> v_side
     or v_agent_result->>'outcome' <> v_side
     or coalesce((v_risk_payload->>'approved')::boolean,false) is not true then
    raise exception 'full consensus and deterministic risk approval required';
  end if;

  if p_payload #>> '{protocol_gate,v51_direction_match}' <> 'true'
     or p_payload #>> '{protocol_gate,deterministic_risk_approved}' <> 'true'
     or p_payload #>> '{protocol_gate,research_paper_candidate}' <> 'true' then
    raise exception 'paper candidate gate not satisfied';
  end if;

  v_provider := p_payload->>'provider';
  v_model_id := p_payload->>'model_id';
  if v_provider <> 'vercel_ai_gateway_oidc'
     or v_model_id <> 'nvidia/nemotron-3.5-lightning-free' then
    raise exception 'unapproved agent provider/model';
  end if;

  v_fill := p_payload->'paper_fill';
  if v_fill is null
     or (v_fill->>'fill_price')::numeric <= 0
     or (v_fill->>'quantity')::numeric <= 0 then
    raise exception 'invalid paper fill';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('triconsensus-v51-paper-open', 0));

  select *
    into v_signal
  from public.strategy_signal_snapshots
  where experiment_id=v_experiment.id
    and calibration_id=v_calibration.id
    and strategy_version='V5.1'
    and symbol=v_symbol
    and context_timestamp=v_context_timestamp
    and feature_name='global_long_short_change_1h'
    and low_threshold=v_expected_low
    and high_threshold=v_expected_high
    and signal=v_side
  order by created_at desc
  limit 1;

  if v_signal.id is null then
    v_signal_checksum := encode(
      digest(
        convert_to(
          jsonb_build_object(
            'strategy_version','V5.1',
            'symbol',v_symbol,
            'context_timestamp',v_context_timestamp,
            'feature_name','global_long_short_change_1h',
            'feature_value',v_feature_value,
            'low_threshold',v_expected_low,
            'high_threshold',v_expected_high,
            'signal',v_side,
            'runtime','vercel_paper_v1'
          )::text,
          'UTF8'
        ),
        'sha256'
      ),
      'hex'
    );

    insert into public.strategy_signal_snapshots(
      experiment_id, market_snapshot_id, calibration_id, strategy_version,
      symbol, observed_at, context_timestamp, feature_name, feature_value,
      low_threshold, high_threshold, signal, context_age_ms, reason, payload, checksum
    )
    values(
      v_experiment.id, null, v_calibration.id, 'V5.1',
      v_symbol, v_observed_at, v_context_timestamp,
      'global_long_short_change_1h', v_feature_value,
      v_expected_low, v_expected_high, v_side,
      (p_payload #>> '{strategy,context_age_ms}')::bigint,
      coalesce(p_payload #>> '{strategy,reason}','paper runtime candidate'),
      (p_payload->'strategy') || jsonb_build_object(
        'paper_runtime','vercel_paper_v1',
        'paper_only',true,
        'v51_protocol_changed',false
      ),
      v_signal_checksum
    )
    returning * into v_signal;
  end if;

  select *
    into v_existing_trade
  from public.paper_trades
  where strategy_signal_snapshot_id=v_signal.id
  limit 1;

  if v_existing_trade.id is not null then
    return jsonb_build_object(
      'status','EXISTING',
      'trade_id',v_existing_trade.id,
      'strategy_signal_snapshot_id',v_signal.id,
      'paper_only',true
    );
  end if;

  v_max_open := greatest(
    1,
    least(
      3,
      coalesce((v_experiment.config->>'max_concurrent_trades')::integer,1)
    )
  );
  select count(*)::integer
    into v_open_count
  from public.paper_trades
  where experiment_id=v_experiment.id and status='open';

  if v_open_count >= v_max_open then
    return jsonb_build_object(
      'status','NO_TRADE',
      'reason','MAX_CONCURRENT_TRADES',
      'strategy_signal_snapshot_id',v_signal.id,
      'paper_only',true
    );
  end if;

  v_market_payload := coalesce(p_payload->'market_snapshot','{}'::jsonb);
  v_market_checksum := encode(
    digest(convert_to(v_market_payload::text,'UTF8'),'sha256'),
    'hex'
  );

  insert into public.market_snapshots(
    experiment_id,symbol,primary_timeframe,captured_at,source,market_data,
    orderbook,funding_rate,open_interest,market_data_age_ms,checksum
  )
  values(
    v_experiment.id,
    v_symbol,
    '15m',
    coalesce(nullif(v_market_payload #>> '{ticker,captured_at}','')::timestamptz,now()),
    'bybit',
    v_market_payload,
    v_market_payload->'orderbook',
    nullif(v_market_payload #>> '{ticker,funding_rate}','')::numeric,
    nullif(v_market_payload #>> '{ticker,open_interest}','')::numeric,
    greatest(0,coalesce((v_market_payload->>'market_data_age_ms')::integer,0)),
    v_market_checksum
  )
  returning * into v_market;

  v_feature_payload := coalesce(p_payload->'feature_snapshot','{}'::jsonb);
  insert into public.feature_snapshots(
    experiment_id,market_snapshot_id,symbol,feature_version,features,leakage_guard
  )
  values(
    v_experiment.id,v_market.id,v_symbol,'v1.4.0',v_feature_payload,
    jsonb_build_object(
      'open_candle_excluded',true,
      'ordered_oldest_to_newest',true,
      'llm_numeric_levels_restricted',true,
      'account_state_server_derived',true,
      'runtime','vercel_paper_v1'
    )
  )
  returning * into v_feature;

  v_account_state := coalesce(p_payload->'account_state','{}'::jsonb);
  v_adaptive := coalesce(v_agent_result->'v6_adaptive','{}'::jsonb);
  v_adaptive_context := coalesce(p_payload->'v6_adaptive_context','{}'::jsonb);
  v_input_hash := encode(
    digest(
      convert_to(
        jsonb_build_object(
          'market',v_market_payload,
          'features',v_feature_payload,
          'strategy',p_payload->'strategy',
          'account_state',v_account_state
        )::text,
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );

  v_agent := v_agent_result #> '{agents,market}';
  if v_agent is not null and v_agent <> 'null'::jsonb then
    insert into public.agent_runs(
      experiment_id,market_snapshot_id,agent_role,provider,model_id,prompt_version,
      input_context_hash,output_json,decision,confidence,evidence,is_valid
    )
    values(
      v_experiment.id,v_market.id,'market_intelligence',v_provider,v_model_id,'v1.4',
      v_input_hash,v_agent,
      coalesce(v_agent->>'bias',v_agent->>'decision',v_agent->>'verdict'),
      nullif(v_agent->>'confidence','')::numeric,
      coalesce(v_agent->'evidence','[]'::jsonb),
      true
    )
    returning id into v_agent_market_id;
  end if;

  v_agent := v_agent_result #> '{agents,proposal}';
  if v_agent is not null and v_agent <> 'null'::jsonb then
    insert into public.agent_runs(
      experiment_id,market_snapshot_id,agent_role,provider,model_id,prompt_version,
      input_context_hash,output_json,decision,confidence,evidence,is_valid
    )
    values(
      v_experiment.id,v_market.id,'trade_strategist',v_provider,v_model_id,'v1.4',
      v_input_hash,v_agent,
      coalesce(v_agent->>'bias',v_agent->>'decision',v_agent->>'verdict'),
      nullif(v_agent->>'confidence','')::numeric,
      coalesce(v_agent->'evidence','[]'::jsonb),
      (v_agent_result->>'reason' <> 'strategist_used_unapproved_numeric_levels')
    )
    returning id into v_agent_strategist_id;
  end if;

  v_agent := v_agent_result #> '{agents,critic}';
  if v_agent is not null and v_agent <> 'null'::jsonb then
    insert into public.agent_runs(
      experiment_id,market_snapshot_id,agent_role,provider,model_id,prompt_version,
      input_context_hash,output_json,decision,confidence,evidence,is_valid
    )
    values(
      v_experiment.id,v_market.id,'risk_advocate',v_provider,v_model_id,'v1.4',
      v_input_hash,v_agent,
      coalesce(v_agent->>'bias',v_agent->>'decision',v_agent->>'verdict'),
      nullif(v_agent->>'confidence','')::numeric,
      coalesce(v_agent->'evidence','[]'::jsonb),
      true
    )
    returning id into v_agent_risk_id;
  end if;

  insert into public.consensus_decisions(
    experiment_id,market_snapshot_id,market_agent_run_id,strategist_agent_run_id,
    risk_agent_run_id,outcome,full_consensus,reason
  )
  values(
    v_experiment.id,v_market.id,v_agent_market_id,v_agent_strategist_id,v_agent_risk_id,
    v_side,true,
    coalesce(v_agent_result->>'consensus_reason',v_agent_result->>'reason','full consensus')
  )
  returning * into v_consensus;

  insert into public.risk_evaluations(
    experiment_id,consensus_decision_id,approved,checks,proposed_entry,stop_loss,
    take_profit_1,take_profit_2,risk_reward,risk_fraction,position_size,
    max_exposure_after,spread_bps,slippage_bps,rejection_reason
  )
  values(
    v_experiment.id,
    v_consensus.id,
    true,
    jsonb_build_object(
      'reason',v_risk_payload->>'reason',
      'numeric_levels_validated',true,
      'full_consensus',true,
      'account_state',v_account_state,
      'v6_adaptive',v_adaptive,
      'deterministic_risk_engine','supreme'
    ),
    nullif(v_risk_payload->>'entry','')::numeric,
    nullif((v_agent_result #>> '{agents,proposal,stop_loss}'),'')::numeric,
    nullif((v_agent_result #>> '{agents,proposal,take_profit_1}'),'')::numeric,
    nullif((v_agent_result #>> '{agents,proposal,take_profit_2}'),'')::numeric,
    nullif(v_risk_payload->>'risk_reward','')::numeric,
    coalesce(nullif(p_payload->>'risk_fraction','')::numeric,0.0025),
    (v_fill->>'quantity')::numeric,
    nullif(v_risk_payload->>'projected_exposure','')::numeric,
    nullif(p_payload #>> '{feature_snapshot,primary,spread_bps}','')::numeric,
    coalesce(nullif(v_account_state->>'slippage_bps','')::numeric,2),
    null
  )
  returning * into v_risk;

  if jsonb_typeof(v_adaptive)='object' then
    if (v_adaptive ? 'portfolio') then
      insert into public.marl_action_logs(
        experiment_id,market_snapshot_id,signal_snapshot_id,agent_role,policy_version,
        state_json,action_json,projected_action_json,shadow_mode,vetoed_by_risk
      )
      values(
        v_experiment.id,v_market.id,v_signal.id,'portfolio',
        coalesce(v_adaptive #>> '{portfolio,policy_version}','unknown'),
        v_adaptive_context,v_adaptive->'portfolio',v_adaptive->'portfolio',true,false
      )
      on conflict (signal_snapshot_id,agent_role) do nothing;
    end if;
    if (v_adaptive ? 'dynamic_risk') then
      insert into public.marl_action_logs(
        experiment_id,market_snapshot_id,signal_snapshot_id,agent_role,policy_version,
        state_json,action_json,projected_action_json,shadow_mode,vetoed_by_risk
      )
      values(
        v_experiment.id,v_market.id,v_signal.id,'dynamic_risk',
        coalesce(v_adaptive #>> '{dynamic_risk,policy_version}','unknown'),
        v_adaptive_context,v_adaptive->'dynamic_risk',v_adaptive->'dynamic_risk',true,false
      )
      on conflict (signal_snapshot_id,agent_role) do nothing;
    end if;
  end if;

  insert into public.paper_trades(
    experiment_id,consensus_decision_id,risk_evaluation_id,strategy_signal_snapshot_id,
    symbol,side,status,requested_entry,actual_entry,stop_loss,take_profit_1,take_profit_2,
    position_size,notional_value,estimated_fees,realized_fees,
    estimated_slippage,realized_slippage,opened_at
  )
  values(
    v_experiment.id,v_consensus.id,v_risk.id,v_signal.id,
    v_symbol,v_side,'open',
    (v_fill->>'requested_price')::numeric,
    (v_fill->>'fill_price')::numeric,
    (v_agent_result #>> '{agents,proposal,stop_loss}')::numeric,
    nullif(v_agent_result #>> '{agents,proposal,take_profit_1}','')::numeric,
    nullif(v_agent_result #>> '{agents,proposal,take_profit_2}','')::numeric,
    (v_fill->>'quantity')::numeric,
    (v_fill->>'fill_price')::numeric * (v_fill->>'quantity')::numeric,
    coalesce((v_fill->>'fee')::numeric,0),
    coalesce((v_fill->>'fee')::numeric,0),
    coalesce((v_fill->>'slippage_cost')::numeric,0),
    coalesce((v_fill->>'slippage_cost')::numeric,0),
    coalesce(nullif(p_payload->>'observed_at','')::timestamptz,now())
  )
  returning * into v_trade;

  insert into public.trade_events(
    experiment_id,trade_id,event_type,event_time,payload
  )
  values(
    v_experiment.id,v_trade.id,'PAPER_OPEN',now(),
    jsonb_build_object(
      'fill',v_fill,
      'account_state_at_entry',v_account_state,
      'v6_adaptive_context',v_adaptive_context,
      'v6_adaptive',v_adaptive,
      'signal_snapshot_id',v_signal.id,
      'provider',v_provider,
      'model_id',v_model_id,
      'paper_only',true
    )
  );

  return jsonb_build_object(
    'status','OPENED',
    'trade_id',v_trade.id,
    'consensus_id',v_consensus.id,
    'risk_evaluation_id',v_risk.id,
    'strategy_signal_snapshot_id',v_signal.id,
    'paper_only',true
  );
exception
  when unique_violation then
    select *
      into v_existing_trade
    from public.paper_trades
    where strategy_signal_snapshot_id=v_signal.id
    limit 1;
    if v_existing_trade.id is not null then
      return jsonb_build_object(
        'status','EXISTING',
        'trade_id',v_existing_trade.id,
        'strategy_signal_snapshot_id',v_signal.id,
        'paper_only',true
      );
    end if;
    raise;
end;
$$;

revoke all on function public.triconsensus_open_paper_trade(text,jsonb)
  from public, anon, authenticated;
grant execute on function public.triconsensus_open_paper_trade(text,jsonb)
  to anon, service_role;

create or replace function public.triconsensus_close_paper_trade(
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
  v_open_payload jsonb := '{}'::jsonb;
  v_adaptive jsonb := '{}'::jsonb;
  v_actual_exit numeric;
  v_requested_exit numeric;
  v_close_fee numeric;
  v_close_slippage numeric;
  v_total_fees numeric;
  v_total_slippage numeric;
  v_gross numeric;
  v_net numeric;
  v_mae numeric;
  v_mfe numeric;
  v_notional numeric;
  v_net_return numeric;
  v_max_drawdown numeric;
  v_reward numeric;
  v_policy_version text;
  v_reward_payload jsonb;
  v_reward_checksum text;
  v_reward_id uuid;
  v_closed_at timestamptz;
  v_duration bigint;
begin
  if p_token is null
     or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  if coalesce((p_payload->>'paper_only')::boolean,false) is not true
     or coalesce((p_payload->>'live_trading')::boolean,true) is not false
     or coalesce((p_payload->>'v6_enforcement')::boolean,true) is not false then
    raise exception 'paper-only close contract violation';
  end if;

  if nullif(p_payload->>'trade_id','') is null then
    raise exception 'trade_id required';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('triconsensus-paper-close:' || (p_payload->>'trade_id'), 0)
  );

  select *
    into v_trade
  from public.paper_trades
  where id=(p_payload->>'trade_id')::uuid
  for update;

  if v_trade.id is null then
    raise exception 'paper trade not found';
  end if;

  select *
    into v_experiment
  from public.experiments
  where id=v_trade.experiment_id;

  if v_experiment.version <> 'V5.1-PAPER'
     or v_experiment.execution_mode <> 'paper'
     or v_experiment.config_hash <> 'b76546273434b348543fb4cd92d86ac0842e8b48c374fcaa83f37c8e08cfc2d0' then
    raise exception 'paper trade does not belong to frozen V5.1 experiment';
  end if;

  if v_trade.status='closed' then
    select id into v_reward_id
    from public.marl_reward_memory
    where trade_id=v_trade.id
    limit 1;
    return jsonb_build_object(
      'status','ALREADY_CLOSED',
      'trade_id',v_trade.id,
      'reward_id',v_reward_id,
      'paper_only',true
    );
  end if;

  if v_trade.status <> 'open' then
    raise exception 'paper trade is not open';
  end if;

  v_requested_exit := (p_payload->>'requested_exit')::numeric;
  v_actual_exit := (p_payload->>'actual_exit')::numeric;
  v_close_fee := greatest(0,coalesce((p_payload->>'close_fee')::numeric,0));
  v_close_slippage := greatest(0,coalesce((p_payload->>'close_slippage')::numeric,0));
  v_mae := coalesce((p_payload->>'mae')::numeric,v_trade.mae,0);
  v_mfe := coalesce((p_payload->>'mfe')::numeric,v_trade.mfe,0);
  v_closed_at := coalesce(nullif(p_payload->>'closed_at','')::timestamptz,now());

  if v_requested_exit <= 0 or v_actual_exit <= 0 then
    raise exception 'invalid close price';
  end if;

  v_total_fees := coalesce(v_trade.realized_fees,0) + v_close_fee;
  v_total_slippage := coalesce(v_trade.realized_slippage,0) + v_close_slippage;

  if v_trade.side='LONG' then
    v_gross := (v_actual_exit - v_trade.actual_entry) * v_trade.position_size;
  elsif v_trade.side='SHORT' then
    v_gross := (v_trade.actual_entry - v_actual_exit) * v_trade.position_size;
  else
    raise exception 'invalid paper side';
  end if;
  v_net := v_gross - v_total_fees;
  v_duration := greatest(
    0,
    floor(extract(epoch from (v_closed_at - v_trade.opened_at)))::bigint
  );

  update public.paper_trades
  set status='closed',
      requested_exit=v_requested_exit,
      actual_exit=v_actual_exit,
      realized_fees=v_total_fees,
      realized_slippage=v_total_slippage,
      pnl_gross=v_gross,
      pnl_net=v_net,
      mae=v_mae,
      mfe=v_mfe,
      closed_at=v_closed_at,
      close_reason=coalesce(nullif(p_payload->>'close_reason',''),'UNKNOWN'),
      trade_duration_seconds=v_duration
  where id=v_trade.id;

  insert into public.trade_events(
    experiment_id,trade_id,event_type,event_time,payload
  )
  values(
    v_trade.experiment_id,v_trade.id,'PAPER_CLOSE',v_closed_at,
    jsonb_build_object(
      'reason',coalesce(nullif(p_payload->>'close_reason',''),'UNKNOWN'),
      'fill',jsonb_build_object(
        'requested_price',v_requested_exit,
        'fill_price',v_actual_exit,
        'quantity',v_trade.position_size,
        'fee',v_close_fee,
        'slippage_cost',v_close_slippage
      ),
      'pnl',jsonb_build_object(
        'gross',v_gross,
        'fees',v_total_fees,
        'net',v_net
      ),
      'paper_only',true
    )
  );

  select coalesce(payload,'{}'::jsonb)
    into v_open_payload
  from public.trade_events
  where trade_id=v_trade.id
    and event_type='PAPER_OPEN'
  order by event_time asc
  limit 1;

  v_adaptive := coalesce(v_open_payload->'v6_adaptive','{}'::jsonb);
  if jsonb_typeof(v_adaptive)='object' and v_adaptive <> '{}'::jsonb then
    v_notional := greatest(coalesce(v_trade.notional_value,0),0.000000000001);
    v_net_return := v_net / v_notional;
    v_max_drawdown := least(
      1,
      abs(least(v_mae,0)) / v_notional
    );
    v_reward := v_net_return - (2.0 * v_max_drawdown);
    v_policy_version := coalesce(
      v_adaptive #>> '{portfolio,policy_version}',
      v_adaptive #>> '{dynamic_risk,policy_version}',
      'v6-shadow'
    );

    v_reward_payload := jsonb_build_object(
      'experiment_id',v_trade.experiment_id,
      'trade_id',v_trade.id,
      'symbol',v_trade.symbol,
      'policy_version',v_policy_version,
      'state_json',
        coalesce(v_open_payload->'v6_adaptive_context','{}'::jsonb)
        || jsonb_build_object(
          'account_state_at_entry',coalesce(v_open_payload->'account_state_at_entry','{}'::jsonb),
          'close_reason',coalesce(nullif(p_payload->>'close_reason',''),'UNKNOWN'),
          'trade_duration_seconds',v_duration,
          'mae',v_mae,
          'mfe',v_mfe
        ),
      'action_json',v_adaptive,
      'net_return',v_net_return,
      'max_drawdown',v_max_drawdown,
      'reward',v_reward,
      'terminal',true
    );
    v_reward_checksum := encode(
      digest(convert_to(v_reward_payload::text,'UTF8'),'sha256'),
      'hex'
    );

    insert into public.marl_reward_memory(
      experiment_id,trade_id,symbol,policy_version,state_json,action_json,
      net_return,max_drawdown,reward,terminal,checksum
    )
    values(
      v_trade.experiment_id,v_trade.id,v_trade.symbol,v_policy_version,
      v_reward_payload->'state_json',v_adaptive,
      v_net_return,v_max_drawdown,v_reward,true,v_reward_checksum
    )
    on conflict (trade_id) do nothing
    returning id into v_reward_id;

    if v_reward_id is null then
      select id into v_reward_id
      from public.marl_reward_memory
      where trade_id=v_trade.id
      limit 1;
    end if;
  end if;

  return jsonb_build_object(
    'status','CLOSED',
    'trade_id',v_trade.id,
    'pnl_gross',v_gross,
    'pnl_net',v_net,
    'reward_id',v_reward_id,
    'paper_only',true
  );
end;
$$;

revoke all on function public.triconsensus_close_paper_trade(text,jsonb)
  from public, anon, authenticated;
grant execute on function public.triconsensus_close_paper_trade(text,jsonb)
  to anon, service_role;

