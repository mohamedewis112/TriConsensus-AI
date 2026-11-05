begin;

create table public.market_binance_global_ratio_5m (
  symbol text not null check (symbol in ('BTCUSDT','ETHUSDT')),
  period_name text not null default '5m' check (period_name = '5m'),
  observed_at timestamptz not null,
  long_short_ratio numeric not null check (long_short_ratio > 0),
  long_account numeric not null check (long_account >= 0 and long_account <= 1),
  short_account numeric not null check (short_account >= 0 and short_account <= 1),
  source text not null check (source = 'binance_usdm_global_long_short_ratio'),
  ingested_at timestamptz not null default now(),
  primary key (symbol, period_name, observed_at)
);

create index idx_market_binance_global_ratio_symbol_time
  on public.market_binance_global_ratio_5m(symbol, observed_at desc);

alter table public.market_binance_global_ratio_5m enable row level security;

revoke all on public.market_binance_global_ratio_5m from anon, authenticated, service_role;
grant select, insert on public.market_binance_global_ratio_5m to service_role;

create policy "deny_client_access_market_binance_global_ratio_5m"
on public.market_binance_global_ratio_5m
as restrictive
for all
to anon, authenticated
using (false)
with check (false);

commit;
