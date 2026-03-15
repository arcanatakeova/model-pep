-- ═══════════════════════════════════════════════════════════════════════════
--  AI Trader — Supabase Schema
--  Run this once in your Supabase SQL editor to set up all required tables.
-- ═══════════════════════════════════════════════════════════════════════════

-- ─── 1. Secrets vault ────────────────────────────────────────────────────────
-- Stores all private keys / API keys so they never live on disk.
-- Only the SERVICE_ROLE key can read these rows (RLS blocks anon access).

create table if not exists secrets (
    id          uuid primary key default gen_random_uuid(),
    key         text unique not null,
    value       text not null,
    description text default '',
    created_at  timestamptz default now(),
    updated_at  timestamptz default now()
);

alter table secrets enable row level security;

-- Block all access except via the service role (backend only)
create policy "service_role_only_secrets"
    on secrets
    using (auth.role() = 'service_role');

-- Auto-update updated_at on change
create or replace function update_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger secrets_updated_at
    before update on secrets
    for each row execute function update_updated_at();

-- ─── 2. Trades ───────────────────────────────────────────────────────────────
-- Full history of every closed trade. Append-only.

create table if not exists trades (
    id           uuid primary key default gen_random_uuid(),
    asset_id     text,
    symbol       text,
    side         text,                       -- 'long' | 'short'
    entry_price  double precision,
    exit_price   double precision,
    qty          double precision,
    size_usd     double precision,
    pnl_usd      double precision,
    pnl_pct      double precision,
    close_reason text,
    chain        text,                       -- 'solana' | '' (CEX)
    opened_at    timestamptz,
    closed_at    timestamptz default now()
);

alter table trades enable row level security;

create policy "service_role_only_trades"
    on trades
    using (auth.role() = 'service_role');

create index if not exists trades_closed_at_idx on trades (closed_at desc);
create index if not exists trades_symbol_idx    on trades (symbol);

-- ─── 3. Equity curve ─────────────────────────────────────────────────────────
-- Time-series of portfolio equity written every 5 seconds while the bot runs.

create table if not exists equity_curve (
    ts         timestamptz primary key default now(),
    equity     double precision,
    cash       double precision,
    dex_value  double precision
);

alter table equity_curve enable row level security;

create policy "service_role_only_equity"
    on equity_curve
    using (auth.role() = 'service_role');

-- Partition hint: if this table grows large, consider a TimescaleDB hypertable
-- or a periodic cron job to delete rows older than 30 days.

-- ─── 4. Bot state snapshot ───────────────────────────────────────────────────
-- Single-row table holding the latest bot_state.json payload.
-- Updated via upsert (id=1) every 5 seconds.

create table if not exists bot_state (
    id         int primary key default 1,
    state      jsonb,
    updated_at timestamptz default now()
);

alter table bot_state enable row level security;

create policy "service_role_only_bot_state"
    on bot_state
    using (auth.role() = 'service_role');

-- ═══════════════════════════════════════════════════════════════════════════
--  INITIAL SECRETS SETUP
--  After running the schema, insert your secrets here (then delete this block
--  from any version-controlled copy — or use the Supabase dashboard UI).
-- ═══════════════════════════════════════════════════════════════════════════
--
-- insert into secrets (key, value, description) values
--   ('PHANTOM_PRIVATE_KEY',  'your_base58_key',          'Phantom wallet private key'),
--   ('COINBASE_KEY_NAME',    'organizations/xxx/...',     'Coinbase CDP key name'),
--   ('COINBASE_SECRET',      '-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----', 'Coinbase CDP private key'),
--   ('BIRDEYE_API_KEY',      'your_birdeye_key',          'Birdeye real-time Solana data'),
--   ('FINNHUB_KEY',          'your_finnhub_key',          'Finnhub stock quotes'),
--   ('SOLANA_RPC_URL',       'https://mainnet.helius-rpc.com/?api-key=xxx', 'Helius RPC')
-- on conflict (key) do update set value = excluded.value, updated_at = now();
