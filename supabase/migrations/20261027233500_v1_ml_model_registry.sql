create table public.ml_models (
  id uuid primary key default gen_random_uuid(),
  experiment_version text not null default 'V1',
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  timeframe text not null default '15m' check (timeframe = '15m'),
  model_family text not null check (model_family in ('lightgbm','xgboost','catboost')),
  model_version text not null,
  artifact_path text not null,
  artifact_sha256 text not null,
  feature_columns jsonb not null,
  target_config jsonb not null,
  training_window jsonb not null,
  oos_metrics jsonb not null,
  dataset_rows integer not null check (dataset_rows > 0),
  created_at timestamptz not null default now(),
  unique (symbol, model_version)
);

create index idx_ml_models_symbol_created
  on public.ml_models(symbol, created_at desc);

alter table public.ml_models enable row level security;

revoke all on public.ml_models from anon, authenticated;
grant select, insert on public.ml_models to service_role;

create policy "deny_client_access_ml_models"
on public.ml_models
as restrictive
for all
to anon, authenticated
using (false)
with check (false);

create trigger trg_ml_models_immutable
before update or delete on public.ml_models
for each row execute function internal.reject_mutation();

insert into storage.buckets (
  id,
  name,
  public,
  file_size_limit,
  allowed_mime_types
)
values (
  'ml-models',
  'ml-models',
  false,
  52428800,
  array['application/octet-stream','application/json']
)
on conflict (id) do nothing;
