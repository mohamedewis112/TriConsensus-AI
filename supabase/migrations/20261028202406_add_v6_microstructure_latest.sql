create table public.v6_microstructure_latest (
  symbol text not null check (symbol in ('BTC/USDT','ETH/USDT')),
  venue text not null check (venue in ('bybit','binance')),
  captured_at timestamptz not null,
  exchange_timestamp_ms bigint not null,
  received_timestamp_ms bigint not null,
  sequence bigint,
  bid_depth numeric not null check (bid_depth >= 0),
  ask_depth numeric not null check (ask_depth >= 0),
  imbalance numeric not null check (imbalance between -1 and 1),
  spread_bps numeric not null check (spread_bps >= 0),
  payload jsonb not null default '{}'::jsonb,
  checksum text not null,
  updated_at timestamptz not null default now(),
  primary key (venue, symbol)
);

alter table public.v6_microstructure_latest enable row level security;
revoke all on table public.v6_microstructure_latest from public, anon, authenticated;
grant select, insert, update, delete on table public.v6_microstructure_latest to service_role;
