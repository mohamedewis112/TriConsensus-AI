create or replace function public.triconsensus_monitor_paper_trade(
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
  v_trade public.paper_trades%rowtype;
  v_experiment public.experiments%rowtype;
  v_mae numeric;
  v_mfe numeric;
begin
  if p_token is null
     or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  if coalesce((p_payload->>'paper_only')::boolean,false) is not true
     or coalesce((p_payload->>'live_trading')::boolean,true) is not false
     or coalesce((p_payload->>'v6_enforcement')::boolean,true) is not false then
    raise exception 'paper-only monitor contract violation';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('triconsensus-paper-monitor:' || (p_payload->>'trade_id'), 0)
  );

  select * into v_trade
  from public.paper_trades
  where id=(p_payload->>'trade_id')::uuid
  for update;

  if v_trade.id is null then
    raise exception 'paper trade not found';
  end if;

  select * into v_experiment
  from public.experiments
  where id=v_trade.experiment_id;

  if v_experiment.version <> 'V5.1-PAPER'
     or v_experiment.execution_mode <> 'paper'
     or v_experiment.config_hash <> frozen_experiment_hash then
    raise exception 'paper trade does not belong to frozen V5.1 experiment';
  end if;

  if v_trade.status='closed' then
    return jsonb_build_object(
      'status','ALREADY_CLOSED',
      'trade_id',v_trade.id,
      'paper_only',true
    );
  end if;

  if v_trade.status <> 'open' then
    raise exception 'paper trade is not open';
  end if;

  v_mae := coalesce((p_payload->>'mae')::numeric,v_trade.mae,0);
  v_mfe := coalesce((p_payload->>'mfe')::numeric,v_trade.mfe,0);

  update public.paper_trades
  set mae=least(coalesce(mae,0),v_mae,0),
      mfe=greatest(coalesce(mfe,0),v_mfe,0)
  where id=v_trade.id
  returning * into v_trade;

  return jsonb_build_object(
    'status','MONITORED',
    'trade_id',v_trade.id,
    'mae',v_trade.mae,
    'mfe',v_trade.mfe,
    'paper_only',true
  );
end;
$$;

revoke all on function public.triconsensus_monitor_paper_trade(text,jsonb)
  from public, anon, authenticated;
grant execute on function public.triconsensus_monitor_paper_trade(text,jsonb)
  to anon, service_role;

-- Retire the older monitor/close gateway from anonymous runtime access.
revoke execute on function public.triconsensus_runtime_monitor_paper_trade(text,jsonb)
  from anon, authenticated;

