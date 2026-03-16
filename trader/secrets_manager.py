"""
Supabase Vault Secrets Manager + Persistence Layer

Responsibilities:
  1. load_secrets()          — fetch all secrets from Supabase Vault (encrypted at rest
                               via pgsodium AES-256-GCM) and inject into os.environ.
  2. persist_trade()         — write a closed trade to Supabase `trades` table.
  3. persist_equity()        — append an equity-curve point every 5 s.
  4. persist_bot_state()     — upsert the latest bot state snapshot (single row).

Security model:
  Secrets are encrypted at rest by Supabase Vault (pgsodium). They are NEVER stored
  as plaintext in the database — only decrypted in memory when read through
  vault.decrypted_secrets. Backups and replication streams only see ciphertext.

  Only SUPABASE_URL + SUPABASE_SERVICE_KEY need to be in .env.
  The service_role key is required to read secrets from the Vault.
  The anon key is sufficient for trades / equity / bot_state persistence.

Fallback:
  If Supabase is unreachable the bot continues using whatever is already in
  os.environ / .env.  A warning is logged but nothing crashes.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Supabase connection (module-level singleton) ───────────────────────────
_client = None
_client_lock = threading.Lock()
_connected = False
_throttle_lock = threading.Lock()

# Valid env var name: uppercase/lowercase letters, digits, underscore
_VALID_ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _get_client(require_service_role: bool = False):
    """
    Return cached Supabase client, or None if not configured.
    require_service_role=True: only returns client if service_role key is available
    (needed for Vault access which has strict RLS).
    """
    global _client, _connected
    if _client is not None:
        if require_service_role and not os.getenv("SUPABASE_SERVICE_KEY", "").strip():
            return None
        return _client
    with _client_lock:
        if _client is not None:
            if require_service_role and not os.getenv("SUPABASE_SERVICE_KEY", "").strip():
                return None
            return _client
        url = os.getenv("SUPABASE_URL", "").strip()
        # Prefer service_role key; fall back to anon key for non-secret operations
        key = (os.getenv("SUPABASE_SERVICE_KEY", "")
               or os.getenv("SUPABASE_ANON_KEY", "")).strip()
        if not url or not key:
            return None
        try:
            from supabase import create_client
            _client = create_client(url, key)
            _connected = True
            key_type = "service_role" if os.getenv("SUPABASE_SERVICE_KEY") else "anon"
            logger.info("Supabase connected [%s]: %s",
                        key_type, url.split("//")[-1].split(".")[0])
        except ImportError:
            logger.warning("supabase-py not installed — run: pip install supabase")
        except Exception as e:
            logger.warning("Supabase connection failed: %s", e)
        if require_service_role and not os.getenv("SUPABASE_SERVICE_KEY", "").strip():
            return None
        return _client


# ── 1. Vault secret loading ──────────────────────────────────────────────────

def load_secrets() -> bool:
    """
    Fetch all secrets from Supabase Vault via the get_vault_secrets() RPC
    function and inject them into os.environ.

    Call this once at process startup, BEFORE config.py is imported, so every
    os.getenv() call in config picks up the Vault-stored values.

    Requires service_role key (Vault RPC functions are restricted).
    Falls back gracefully if only anon key is available.

    Returns True if at least one secret was loaded.
    """
    client = _get_client(require_service_role=True)
    if client is None:
        return False
    try:
        resp = client.rpc("get_vault_secrets").execute()
        if not isinstance(resp.data, list):
            logger.warning("Vault RPC returned unexpected data type: %s", type(resp.data).__name__)
            return False
        loaded = 0
        for row in resp.data:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            secret = row.get("decrypted_secret")
            # Use `is not None` — a name like "0" is valid but falsy
            if name is not None and secret is not None:
                name = str(name).strip()
                secret = str(secret).strip()
                if not name or not secret:
                    continue
                # Validate env var name (prevent injection of malformed keys)
                if not _VALID_ENV_NAME.match(name):
                    logger.warning("Skipping invalid env var name from Vault: %r", name[:40])
                    continue
                # .env values take precedence over vault — don't overwrite
                # a non-empty value the user has explicitly set locally.
                if os.environ.get(name, "").strip():
                    continue
                # Restore real newlines in PEM keys stored with escaped \n
                os.environ[name] = secret.replace("\\n", "\n")
                loaded += 1
        if loaded:
            logger.info("Loaded %d secrets from Supabase Vault (encrypted at rest)", loaded)
        return loaded > 0
    except Exception as e:
        logger.warning("Failed to load Vault secrets — using .env fallback")
        return False


# ── 2. Trade persistence ────────────────────────────────────────────────────

def persist_trade(trade: dict) -> None:
    """
    Write a closed trade record to Supabase `trades` table (fire-and-forget).
    Called from _close_dex_position and portfolio.close_position.
    """
    client = _get_client()
    if client is None:
        return
    try:
        row = {
            "asset_id":    trade.get("asset_id", ""),
            "symbol":      trade.get("symbol", ""),
            "side":        trade.get("side", "long"),
            "entry_price": trade.get("entry_price"),
            "exit_price":  trade.get("exit_price"),
            "qty":         trade.get("qty"),
            "size_usd":    trade.get("size_usd"),
            "pnl_usd":     trade.get("pnl_usd"),
            "pnl_pct":     trade.get("pnl_pct"),
            "close_reason": trade.get("close_reason") or trade.get("reason", ""),
            "chain":       trade.get("chain", ""),
            "opened_at":   trade.get("opened_at"),
            "closed_at":   trade.get("closed_at"),
        }
        client.table("trades").insert(row).execute()
    except Exception as e:
        logger.debug("Supabase persist_trade error: %s", e)


# ── 3. Equity curve persistence ────────────────────────────────────────────

# Throttle: write at most once every 5 s (matches _live_dashboard_writer cadence)
_last_equity_write: float = 0.0


def persist_equity(equity: float, cash: float, dex_value: float) -> None:
    """
    Append an equity-curve data point to Supabase `equity_curve` table.
    Silently throttled to 5-second intervals (thread-safe).
    """
    global _last_equity_write
    with _throttle_lock:
        now = time.time()
        if now - _last_equity_write < 5:
            return
        _last_equity_write = now

    client = _get_client()
    if client is None:
        return
    try:
        client.table("equity_curve").insert({
            "ts":        datetime.now(timezone.utc).isoformat(),
            "equity":    round(equity, 4),
            "cash":      round(cash, 4),
            "dex_value": round(dex_value, 4),
        }).execute()
    except Exception as e:
        logger.debug("Supabase persist_equity error: %s", e)


# ── 4. Bot state snapshot ──────────────────────────────────────────────────

_last_state_write: float = 0.0


def persist_bot_state(state: dict) -> None:
    """
    Upsert the latest bot state into Supabase `bot_state` table (single row,
    id=1).  Dashboard or external tools can query this for a live view.
    Throttled to once every 5 s (thread-safe).
    """
    global _last_state_write
    with _throttle_lock:
        now = time.time()
        if now - _last_state_write < 5:
            return
        _last_state_write = now

    client = _get_client()
    if client is None:
        return
    try:
        client.table("bot_state").upsert({
            "id":         1,
            "state":      state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.debug("Supabase persist_bot_state error: %s", e)


# ── 5. Vault upsert helper (for setup scripts) ───────────────────────────

def upsert_secret(key: str, value: str, description: str = "") -> bool:
    """
    Store or update a secret in Supabase Vault (encrypted at rest).
    Uses the set_vault_secret() RPC function.
    Requires service_role key.
    """
    client = _get_client(require_service_role=True)
    if client is None:
        return False
    try:
        client.rpc("set_vault_secret", {
            "p_name":        key,
            "p_value":       value,
            "p_description": description,
        }).execute()
        logger.info("Vault secret upserted: %s", key)
        return True
    except Exception as e:
        logger.warning("Failed to upsert Vault secret %s: %s", key, e)
        return False


def is_connected() -> bool:
    """Return True if Supabase client is initialised and reachable."""
    return _get_client() is not None and _connected


def fetch_secret(name: str) -> Optional[str]:
    """
    Fetch a single named secret directly from Supabase Vault, bypassing the
    env-override check in load_secrets().

    Use this to force-refresh a specific key that may be stale in os.environ
    (e.g. BIRDEYE_API_KEY set incorrectly in .env but correct in the vault).
    Returns the decrypted value, or None if not found / vault unreachable.
    Requires service_role key.
    """
    client = _get_client(require_service_role=True)
    if not client:
        return None
    try:
        resp = client.rpc("get_vault_secrets").execute()
        for row in (resp.data or []):
            if isinstance(row, dict) and row.get("name") == name:
                val = str(row.get("decrypted_secret", "")).strip()
                return val if val else None
    except Exception as e:
        logger.debug("fetch_secret(%s) error: %s", name, e)
    return None
