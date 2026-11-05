create index if not exists idx_strategy_signal_snapshots_calibration
  on public.strategy_signal_snapshots (calibration_id);

create index if not exists idx_strategy_signal_snapshots_market
  on public.strategy_signal_snapshots (market_snapshot_id)
  where market_snapshot_id is not null;
