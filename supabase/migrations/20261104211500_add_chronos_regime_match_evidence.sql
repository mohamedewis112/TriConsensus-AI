create table if not exists internal.chronos_regime_match_episodes (
  id uuid primary key default gen_random_uuid(),
  candle_start_ms bigint not null unique,
  generated_at timestamptz not null,
  verdict text not null check (verdict like 'REGIME_MATCH_%'),
  report jsonb not null,
  created_at timestamptz not null default now()
);

alter table internal.chronos_regime_match_episodes enable row level security;
revoke all on table internal.chronos_regime_match_episodes from public, anon, authenticated;

create or replace function public.triconsensus_chronos_record_episode(
  p_token text,
  p_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, internal, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';
  v_candle bigint;
  v_id uuid;
  v_duplicate boolean := false;
begin
  if p_token is null
     or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  if coalesce((p_payload->>'research_only')::boolean, false) is not true
     or coalesce((p_payload->>'paper_trading_authority')::boolean, true) is not false
     or coalesce((p_payload->>'live_trading_authority')::boolean, true) is not false then
    raise exception 'chronos research-only contract violation';
  end if;

  if coalesce((p_payload #>> '{gate_before,matched}')::boolean, false) is not true
     or coalesce((p_payload #>> '{gate_after,matched}')::boolean, false) is not true
     or p_payload #>> '{gate_before,target,species}' <> 'trend_breakout'
     or p_payload #>> '{gate_before,target,side}' <> 'SHORT'
     or p_payload #>> '{gate_before,target,regime_key}' <> 'trend_down|mid_vol|momentum_mixed'
     or p_payload->>'verdict' not like 'REGIME_MATCH_%'
     or jsonb_typeof(p_payload->'ab') <> 'object' then
    raise exception 'invalid chronos matched episode';
  end if;

  v_candle := (p_payload #>> '{gate_before,latest_closed_candle_start_ms}')::bigint;
  if v_candle is null or v_candle <= 0 then
    raise exception 'invalid chronos candle key';
  end if;

  insert into internal.chronos_regime_match_episodes(
    candle_start_ms, generated_at, verdict, report
  )
  values(
    v_candle,
    coalesce(nullif(p_payload->>'generated_at','')::timestamptz, now()),
    p_payload->>'verdict',
    p_payload
  )
  on conflict (candle_start_ms) do nothing
  returning id into v_id;

  if v_id is null then
    v_duplicate := true;
    select id into v_id
    from internal.chronos_regime_match_episodes
    where candle_start_ms = v_candle;
  end if;

  return jsonb_build_object(
    'persisted', true,
    'duplicate', v_duplicate,
    'episode_id', v_id,
    'candle_start_ms', v_candle,
    'research_only', true,
    'paper_trading_authority', false,
    'live_trading_authority', false
  );
end;
$$;

create or replace function public.triconsensus_chronos_list_episodes(
  p_token text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, internal, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';
  v_episodes jsonb;
begin
  if p_token is null
     or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  select coalesce(jsonb_agg(report order by candle_start_ms), '[]'::jsonb)
    into v_episodes
  from (
    select candle_start_ms, report
    from internal.chronos_regime_match_episodes
    order by candle_start_ms desc
    limit 100
  ) q;

  return jsonb_build_object(
    'episodes', v_episodes,
    'research_only', true,
    'paper_trading_authority', false,
    'live_trading_authority', false
  );
end;
$$;

revoke all on function public.triconsensus_chronos_record_episode(text,jsonb)
  from public, anon, authenticated;
revoke all on function public.triconsensus_chronos_list_episodes(text)
  from public, anon, authenticated;
grant execute on function public.triconsensus_chronos_record_episode(text,jsonb)
  to service_role;
grant execute on function public.triconsensus_chronos_list_episodes(text)
  to service_role;
