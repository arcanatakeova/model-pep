-- ═══════════════════════════════════════════════════════════════════════════
--  AI Trader — Supabase Schema
--  Run this ONCE in your Supabase SQL editor (Dashboard → SQL Editor → New query)
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── Helper: auto-update updated_at ─────────────────────────────────────────
create or replace function update_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ─── 1. Secrets vault ────────────────────────────────────────────────────────
-- Stores all private keys / API keys — never needs to live on disk.
-- REQUIRES service_role key to read/write (anon key is blocked by RLS).
-- Get service_role key: Supabase dashboard → Settings → API → service_role

create table if not exists secrets (
    id          uuid primary key default gen_random_uuid(),
    key         text unique not null,
    value       text not null,
    description text default '',
    created_at  timestamptz default now(),
    updated_at  timestamptz default now()
);

alter table secrets enable row level security;

drop policy if exists "service_role_only_secrets" on secrets;
create policy "service_role_only_secrets"
    on secrets
    using (auth.role() = 'service_role');

drop trigger if exists secrets_updated_at on secrets;
create trigger secrets_updated_at
    before update on secrets
    for each row execute function update_updated_at();

-- ─── 2. Trades ───────────────────────────────────────────────────────────────
-- Full history of every closed trade. Uses anon key — bot inserts only.

create table if not exists trades (
    id           uuid primary key default gen_random_uuid(),
    asset_id     text,
    symbol       text,
    side         text,
    entry_price  double precision,
    exit_price   double precision,
    qty          double precision,
    size_usd     double precision,
    pnl_usd      double precision,
    pnl_pct      double precision,
    close_reason text,
    chain        text,
    opened_at    timestamptz,
    closed_at    timestamptz default now()
);

alter table trades enable row level security;

drop policy if exists "anon_insert_trades" on trades;
drop policy if exists "anon_select_trades" on trades;
create policy "anon_insert_trades" on trades for insert to anon with check (true);
create policy "anon_select_trades" on trades for select to anon using (true);

create index if not exists trades_closed_at_idx on trades (closed_at desc);
create index if not exists trades_symbol_idx    on trades (symbol);

-- ─── 3. Equity curve ─────────────────────────────────────────────────────────
-- Time-series written every 5 seconds. Uses anon key.

create table if not exists equity_curve (
    ts         timestamptz primary key default now(),
    equity     double precision,
    cash       double precision,
    dex_value  double precision
);

alter table equity_curve enable row level security;

drop policy if exists "anon_insert_equity" on equity_curve;
drop policy if exists "anon_select_equity" on equity_curve;
create policy "anon_insert_equity" on equity_curve for insert to anon with check (true);
create policy "anon_select_equity" on equity_curve for select to anon using (true);

-- ─── 4. Bot state snapshot ───────────────────────────────────────────────────
-- Single-row upsert (id=1) every 5 s. Uses anon key.

create table if not exists bot_state (
    id         int primary key default 1,
    state      jsonb,
    updated_at timestamptz default now()
);

alter table bot_state enable row level security;

drop policy if exists "anon_upsert_bot_state" on bot_state;
drop policy if exists "anon_select_bot_state" on bot_state;
create policy "anon_upsert_bot_state" on bot_state for all to anon using (true) with check (true);
create policy "anon_select_bot_state" on bot_state for select to anon using (true);

-- ═══════════════════════════════════════════════════════════════════════════
--  AFTER running this schema:
--
--  1. Get service_role key from: Settings → API → service_role (secret)
--     Add to .env:  SUPABASE_SERVICE_KEY=eyJ...
--
--  2. Insert your secrets (using service_role key or Supabase Table Editor):
--
-- insert into secrets (key, value, description) values
--   ('PHANTOM_PRIVATE_KEY',  'your_base58_key',   'Phantom wallet'),
--   ('BIRDEYE_API_KEY',      'your_key',           'Birdeye Solana data'),
--   ('FINNHUB_KEY',          'your_key',           'Finnhub stocks'),
--   ('COINBASE_KEY_NAME',    'organizations/...',  'Coinbase CDP key name'),
--   ('COINBASE_SECRET',      '-----BEGIN EC PRIVATE KEY-----\nMHQ...\n-----END EC PRIVATE KEY-----', 'Coinbase private key')
-- on conflict (key) do update set value = excluded.value, updated_at = now();
-- ═══════════════════════════════════════════════════════════════════════════
