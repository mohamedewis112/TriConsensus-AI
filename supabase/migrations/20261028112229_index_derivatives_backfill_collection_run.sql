create index if not exists idx_derivatives_backfill_queue_collection_run
  on public.derivatives_backfill_queue (collection_run_id)
  where collection_run_id is not null;
