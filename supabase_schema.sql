
-- Basic schema for storing aggregates and HL results

create table if not exists public.candles_minute (
  id bigserial primary key,
  symbol text not null,
  ts timestamptz not null,
  open numeric(18,6) not null,
  high numeric(18,6) not null,
  low  numeric(18,6) not null,
  close numeric(18,6) not null,
  volume bigint,
  vwap numeric(18,6),
  trades bigint,
  day date generated always as (date(ts at time zone 'America/New_York')) stored
);
create index if not exists idx_candles_minute_symbol_ts on public.candles_minute(symbol, ts);

create table if not exists public.candles_second (
  id bigserial primary key,
  symbol text not null,
  ts timestamptz not null,
  open numeric(18,6) not null,
  high numeric(18,6) not null,
  low  numeric(18,6) not null,
  close numeric(18,6) not null,
  volume bigint,
  vwap numeric(18,6),
  trades bigint,
  day date generated always as (date(ts at time zone 'America/New_York')) stored
);
create index if not exists idx_candles_second_symbol_ts on public.candles_second(symbol, ts);

create table if not exists public.hl_occurrences (
  id bigserial primary key,
  symbol text not null,
  a0_time timestamptz not null,
  a0_low numeric(18,6) not null,
  a1_time timestamptz,
  a1_low numeric(18,6),
  a2_time timestamptz,
  a2_low numeric(18,6),
  entry_time timestamptz,
  entry_price numeric(18,6),
  exit_time timestamptz,
  exit_price numeric(18,6),
  outcome text check (outcome in ('take_profit','stop_loss','timeout','none')) default 'none',
  profit numeric(18,6) default 0,
  params jsonb,
  day date generated always as (date(a0_time at time zone 'America/New_York')) stored
);
create index if not exists idx_hl_occ_symbol_day on public.hl_occurrences(symbol, day);
create index if not exists idx_hl_occ_day on public.hl_occurrences(day);

create table if not exists public.backtest_runs (
  id bigserial primary key,
  symbol text not null,
  start_date date not null,
  end_date date not null,
  created_at timestamptz default now(),
  parameters jsonb,
  summary jsonb
);

create table if not exists public.fine_tune_results (
  id bigserial primary key,
  symbol text not null,
  start_date date not null,
  end_date date not null,
  created_at timestamptz default now(),
  params jsonb,
  metrics jsonb
);
