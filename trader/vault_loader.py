"""
Secrets Loader — Fetches API keys from Supabase Vault.
=====================================================
Pulls secrets from Supabase Vault at startup, falls back to .env / os.getenv().

Vault secrets are stored in Supabase Dashboard → SQL Editor:
  SELECT vault.create_secret('your_api_key_value', 'BINANCE_API_KEY');
  SELECT vault.create_secret('your_secret_value',  'BINANCE_SECRET');
  ... etc for each key
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://iimijdyiaookufieauea.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlpbWlqZHlpYW9va3VmaWVhdWVhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MzU1NzkxOSwiZXhwIjoyMDg5MTMzOTE5fQ.70QayF31dIdGu16wAW-wkFHNA_cLQ6r9Ro_MRvcrouI",
)

# Keys we want to pull from Vault
SECRET_NAMES = [
    "PHANTOM_PRIVATE_KEY",
    "SOLANA_RPC_URL",
    "POLYMARKET_PRIVATE_KEY",
    "BINANCE_API_KEY",
    "BINANCE_SECRET",
    "COINBASE_API_KEY",
    "COINBASE_SECRET",
    "COINGECKO_API_KEY",
    "ALPHAVANTAGE_KEY",
    "FINNHUB_KEY",
]


def load_secrets() -> dict:
    """
    Fetch all secrets from Supabase Vault and inject into os.environ.
    Falls back to existing env vars if Vault is unreachable.
    Returns dict of loaded secret names.
    """
    loaded = {}

    try:
        import requests
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/get_secrets",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
            },
            json={},
            timeout=10,
        )

        if not resp.ok:
            # Try the direct vault.decrypted_secrets view instead
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/rpc/read_secrets",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                },
                params={},
                timeout=10,
            )

        if resp.ok:
            secrets = resp.json()
            if isinstance(secrets, list):
                for s in secrets:
                    name = s.get("name", "")
                    value = s.get("decrypted_secret", "") or s.get("secret", "")
                    if name in SECRET_NAMES and value:
                        os.environ[name] = value
                        loaded[name] = True
                        logger.debug("Vault: loaded %s", name)

            logger.info("Supabase Vault: %d secrets loaded", len(loaded))
        else:
            logger.warning("Supabase Vault returned %d: %s", resp.status_code, resp.text[:200])

    except ImportError:
        logger.warning("requests not installed — cannot fetch from Supabase Vault")
    except Exception as e:
        logger.warning("Supabase Vault fetch failed: %s", e)

    # Log which keys are available (from Vault or env)
    for name in SECRET_NAMES:
        val = os.getenv(name, "")
        if val and name not in loaded:
            loaded[name] = True  # Was already in env

    return loaded
