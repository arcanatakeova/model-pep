-- ═══════════════════════════════════════════════════════════════════════════
--  AI Trader — Supabase Schema
--  Run this ONCE in your Supabase SQL editor (Dashboard → SQL Editor → New query)
--
--  Uses Supabase Vault for secrets — encrypted at rest via pgsodium (AES-256-GCM).
--  Secrets are NEVER stored as plaintext in the database; only decrypted in memory.
-- ═══════════════════════════════════════════════════════════════════════════


-- ─── 1. Vault RPC wrappers ──────────────────────────────────────────────────
-- PostgREST can only access the `public` schema, but Vault lives in `vault.*`.
-- These thin wrappers let the bot call vault functions via supabase.rpc().
-- Both are SECURITY DEFINER + restricted to service_role only.

-- Read all decrypted secrets (service_role only)
create or replace function get_vault_secrets()
returns table (name text, decrypted_secret text)
language sql security definer
as $$
    select name, decrypted_secret from vault.decrypted_secrets;
$$;

revoke execute on function get_vault_secrets from public, anon, authenticated;
grant execute on function get_vault_secrets to service_role;

-- Upsert a secret (service_role only)
create or replace function set_vault_secret(
    p_name text,
    p_value text,
    p_description text default ''
) returns uuid
language plpgsql security definer
as $$
declare
    existing_id uuid;
begin
    select id into existing_id from vault.secrets where name = p_name;
    if existing_id is not null then
        perform vault.update_secret(existing_id, p_value, p_name, p_description);
        return existing_id;
    else
        return vault.create_secret(p_value, p_name, p_description);
    end if;
end;
$$;

revoke execute on function set_vault_secret from public, anon, authenticated;
grant execute on function set_vault_secret to service_role;


-- ─── 2. Trades ───────────────────────────────────────────────────────────────
-- Full history of every closed trade. Append-only. Anon key can insert+read.

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

-- Writes: service_role only (bot process). Reads: anon OK (dashboard).
drop policy if exists "anon_insert_trades" on trades;
drop policy if exists "anon_select_trades" on trades;
drop policy if exists "service_role_write_trades" on trades;
create policy "service_role_write_trades" on trades for insert to service_role with check (true);
create policy "anon_select_trades" on trades for select to anon using (true);

create index if not exists trades_closed_at_idx on trades (closed_at desc);
create index if not exists trades_symbol_idx    on trades (symbol);


-- ─── 3. Equity curve ─────────────────────────────────────────────────────────
-- Time-series written every 5 seconds while the bot runs.

create table if not exists equity_curve (
    ts         timestamptz primary key default now(),
    equity     double precision,
    cash       double precision,
    dex_value  double precision
);

alter table equity_curve enable row level security;

-- Writes: service_role only. Reads: anon OK (dashboard charts).
drop policy if exists "anon_insert_equity" on equity_curve;
drop policy if exists "anon_select_equity" on equity_curve;
drop policy if exists "service_role_write_equity" on equity_curve;
create policy "service_role_write_equity" on equity_curve for insert to service_role with check (true);
create policy "anon_select_equity" on equity_curve for select to anon using (true);


-- ─── 4. Bot state snapshot ───────────────────────────────────────────────────
-- Single-row upsert (id=1) every 5 s.

create table if not exists bot_state (
    id         int primary key default 1,
    state      jsonb,
    updated_at timestamptz default now()
);

alter table bot_state enable row level security;

-- Writes: service_role only. Reads: anon OK (dashboard).
drop policy if exists "anon_upsert_bot_state" on bot_state;
drop policy if exists "anon_select_bot_state" on bot_state;
drop policy if exists "service_role_write_bot_state" on bot_state;
create policy "service_role_write_bot_state" on bot_state for all to service_role using (true) with check (true);
create policy "anon_select_bot_state" on bot_state for select to anon using (true);


-- ═══════════════════════════════════════════════════════════════════════════
--  AFTER running this schema, store your secrets in the Vault:
--
--  Option A — via Supabase Dashboard:
--    Go to Project Settings → Vault → Add new secret
--    Name each secret exactly as the env var name (e.g. PHANTOM_PRIVATE_KEY)
--
--  Option B — via SQL (run in SQL Editor):
--
--  select set_vault_secret('PHANTOM_PRIVATE_KEY',  'your_base58_key',     'Phantom wallet');
--  select set_vault_secret('BIRDEYE_API_KEY',      'your_key',            'Birdeye Solana data');
--  select set_vault_secret('FINNHUB_KEY',          'your_key',            'Finnhub stocks');
--  select set_vault_secret('COINBASE_KEY_NAME',    'organizations/...',   'Coinbase CDP key name');
--  select set_vault_secret('COINBASE_SECRET',      '-----BEGIN EC....',   'Coinbase private key');
--  select set_vault_secret('SOLANA_RPC_URL',       'https://mainnet.helius-rpc.com/?api-key=xxx', 'Helius RPC');
--
--  Then add service_role key to .env:
--    SUPABASE_SERVICE_KEY=eyJ...  (from Settings → API → service_role)
-- ═══════════════════════════════════════════════════════════════════════════
