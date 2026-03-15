-- Run this in Supabase Dashboard → SQL Editor
-- Creates a function to read secrets from Vault

-- 1. Create the read function (service_role only)
CREATE OR REPLACE FUNCTION read_secrets()
RETURNS TABLE (name text, decrypted_secret text)
LANGUAGE sql
SECURITY DEFINER
AS $$
  SELECT name, decrypted_secret
  FROM vault.decrypted_secrets
  WHERE name IN (
    'PHANTOM_PRIVATE_KEY',
    'SOLANA_RPC_URL',
    'POLYMARKET_PRIVATE_KEY',
    'BINANCE_API_KEY',
    'BINANCE_SECRET',
    'COINBASE_API_KEY',
    'COINBASE_SECRET',
    'COINGECKO_API_KEY',
    'ALPHAVANTAGE_KEY',
    'FINNHUB_KEY'
  );
$$;

-- 2. Revoke public access (only service_role can call this)
REVOKE ALL ON FUNCTION read_secrets() FROM PUBLIC;
REVOKE ALL ON FUNCTION read_secrets() FROM anon;
REVOKE ALL ON FUNCTION read_secrets() FROM authenticated;

-- 3. Add your secrets (replace with real values)
-- Run each line separately:

-- SELECT vault.create_secret('your_phantom_key_here', 'PHANTOM_PRIVATE_KEY');
-- SELECT vault.create_secret('https://mainnet.helius-rpc.com/?api-key=YOUR_KEY', 'SOLANA_RPC_URL');
-- SELECT vault.create_secret('your_coinbase_api_key', 'COINBASE_API_KEY');
-- SELECT vault.create_secret('your_coinbase_secret', 'COINBASE_SECRET');
-- SELECT vault.create_secret('your_coingecko_key', 'COINGECKO_API_KEY');
-- SELECT vault.create_secret('your_binance_key', 'BINANCE_API_KEY');
-- SELECT vault.create_secret('your_binance_secret', 'BINANCE_SECRET');
