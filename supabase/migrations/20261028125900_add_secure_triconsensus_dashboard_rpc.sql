create or replace function public.triconsensus_dashboard_snapshot(p_token text)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  expected_hash constant text := 'bb1b662a3aa81647d1b0f7828c157d648fc0f9a89f0280cd5cd9a1d9b3213933';
begin
  if p_token is null or encode(digest(p_token, 'sha256'), 'hex') <> expected_hash then
    raise exception 'unauthorized' using errcode = '42501';
  end if;

  return jsonb_build_object(
    'generated_at', now(),
    'experiment', coalesce((
      select to_jsonb(e) from (
        select id, version, status, execution_mode, config_hash
        from public.experiments
        where version = 'V5.1-PAPER'
        order by created_at desc
        limit 1
      ) e
    ), '{}'::jsonb),
    'prospective', coalesce((
      select jsonb_agg(to_jsonb(v) order by v.symbol)
      from public.v51_forward_signal_summary_by_capture_mode v
      where v.capture_mode = 'prospective_live'
    ), '[]'::jsonb),
    'latest_signals', coalesce((
      select jsonb_agg(to_jsonb(s) order by s.symbol)
      from (
        select distinct on (symbol)
          symbol, context_timestamp, feature_value, low_threshold, high_threshold,
          signal, reason, created_at
        from public.strategy_signal_snapshots
        where payload->>'recorder' = 'supabase_pg_cron_v1'
        order by symbol, context_timestamp desc
      ) s
    ), '[]'::jsonb),
    'reconstructed', coalesce((
      select jsonb_agg(to_jsonb(v) order by v.symbol)
      from public.v51_forward_rule_summary_by_capture_mode v
      where v.capture_mode = 'reconstructed_oos'
    ), '[]'::jsonb),
    'gate', coalesce((
      select jsonb_agg(to_jsonb(v) order by v.symbol)
      from public.v51_forward_gate_precheck v
      where v.capture_mode = 'reconstructed_oos'
    ), '[]'::jsonb),
    'health', coalesce((
      select to_jsonb(h) from (
        select status, checked_at, checks
        from public.strategy_forward_health_checks
        order by checked_at desc
        limit 1
      ) h
    ), '{}'::jsonb),
    'execution_counts', jsonb_build_object(
      'agent_runs', (select count(*) from public.agent_runs),
      'consensus_decisions', (select count(*) from public.consensus_decisions),
      'risk_evaluations', (select count(*) from public.risk_evaluations),
      'paper_trades', (select count(*) from public.paper_trades),
      'trade_events', (select count(*) from public.trade_events)
    ),
    'paper_trades', coalesce((
      select jsonb_agg(to_jsonb(t) order by t.created_at desc)
      from (
        select * from public.paper_trades order by created_at desc limit 100
      ) t
    ), '[]'::jsonb),
    'markets', jsonb_build_object(
      'BTCUSDT', jsonb_build_object(
        'ticker', jsonb_build_object(
          'symbol', 'BTCUSDT',
          'last_price', coalesce((select close_price::float8 from public.market_candles_15m where symbol='BTCUSDT' order by open_time desc limit 1), 0),
          'funding_rate', coalesce((select funding_rate::float8 from public.market_funding_history where symbol='BTCUSDT' order by funding_time desc limit 1), 0),
          'open_interest', coalesce((select open_interest::float8 from public.market_open_interest_history where symbol='BTCUSDT' order by observed_at desc limit 1), 0)
        ),
        'latest_ratio', coalesce((select long_short_ratio::float8 from public.market_binance_global_ratio_5m where symbol='BTCUSDT' order by observed_at desc limit 1), 0),
        'market_data_age_ms', coalesce((select (extract(epoch from (now() - collected_at))*1000)::bigint from public.market_candles_15m where symbol='BTCUSDT' order by open_time desc limit 1), 0),
        'candles', jsonb_build_object(
          '15m', coalesce((
            select jsonb_agg(jsonb_build_object(
              'start_ms', (extract(epoch from c.open_time)*1000)::bigint,
              'open', c.open_price::float8, 'high', c.high_price::float8,
              'low', c.low_price::float8, 'close', c.close_price::float8,
              'volume', c.volume::float8
            ) order by c.open_time)
            from (
              select * from public.market_candles_15m
              where symbol='BTCUSDT' order by open_time desc limit 96
            ) c
          ), '[]'::jsonb)
        )
      ),
      'ETHUSDT', jsonb_build_object(
        'ticker', jsonb_build_object(
          'symbol', 'ETHUSDT',
          'last_price', coalesce((select close_price::float8 from public.market_candles_15m where symbol='ETHUSDT' order by open_time desc limit 1), 0),
          'funding_rate', coalesce((select funding_rate::float8 from public.market_funding_history where symbol='ETHUSDT' order by funding_time desc limit 1), 0),
          'open_interest', coalesce((select open_interest::float8 from public.market_open_interest_history where symbol='ETHUSDT' order by observed_at desc limit 1), 0)
        ),
        'latest_ratio', coalesce((select long_short_ratio::float8 from public.market_binance_global_ratio_5m where symbol='ETHUSDT' order by observed_at desc limit 1), 0),
        'market_data_age_ms', coalesce((select (extract(epoch from (now() - collected_at))*1000)::bigint from public.market_candles_15m where symbol='ETHUSDT' order by open_time desc limit 1), 0),
        'candles', jsonb_build_object(
          '15m', coalesce((
            select jsonb_agg(jsonb_build_object(
              'start_ms', (extract(epoch from c.open_time)*1000)::bigint,
              'open', c.open_price::float8, 'high', c.high_price::float8,
              'low', c.low_price::float8, 'close', c.close_price::float8,
              'volume', c.volume::float8
            ) order by c.open_time)
            from (
              select * from public.market_candles_15m
              where symbol='ETHUSDT' order by open_time desc limit 96
            ) c
          ), '[]'::jsonb)
        )
      )
    )
  );
end;
$$;

revoke all on function public.triconsensus_dashboard_snapshot(text) from public;
grant execute on function public.triconsensus_dashboard_snapshot(text) to anon, authenticated, service_role;
