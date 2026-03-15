"""
Supabase Secrets Manager + Persistence Layer

Responsibilities:
  1. load_secrets()          — fetch all private keys from Supabase `secrets` table
                               and inject into os.environ before config is read.
  2. persist_trade()         — write a closed trade to Supabase `trades` table.
  3. persist_equity()        — append an equity-curve point every 5 s.
  4. persist_bot_state()     — upsert the latest bot state snapshot (single row).

Security model:
  Only SUPABASE_URL + SUPABASE_SERVICE_KEY need to be in .env (or the host
  environment).  Every other secret (PHANTOM_PRIVATE_KEY, COINBASE_KEY_NAME,
  BIRDEYE_API_KEY, …) lives encrypted in Supabase and is fetched at startup.

Fallback:
  If Supabase is unreachable the bot continues normally using whatever is
  already in os.environ / .env.  A warning is logged but nothing crashes.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Supabase connection (module-level singleton) ───────────────────────────
_client = None
_client_lock = threading.Lock()
_connected = False


def _get_client(require_service_role: bool = False):
    """
    Return cached Supabase client, or None if not configured.
    require_service_role=True: only returns client if service_role key is available
    (needed for the secrets table which has strict RLS).
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


# ── 1. Secret loading ──────────────────────────────────────────────────────

def load_secrets() -> bool:
    """
    Fetch all rows from the Supabase `secrets` table and inject them into
    os.environ.  Call this once at process startup, before config.py is
    imported, so every os.getenv() call in config picks up the Supabase values.

    Requires service_role key (secrets table has strict RLS).
    Falls back gracefully if only anon key is available.

    Returns True if at least one secret was loaded.
    """
    client = _get_client(require_service_role=True)
    if client is None:
        return False
    try:
        resp = client.table("secrets").select("key,value").execute()
        loaded = 0
        for row in (resp.data or []):
            k = (row.get("key") or "").strip()
            v = (row.get("value") or "").strip()
            if k:
                # Restore real newlines in PEM keys stored with escaped \n
                os.environ[k] = v.replace("\\n", "\n")
                loaded += 1
        if loaded:
            logger.info("Loaded %d secrets from Supabase vault", loaded)
        return loaded > 0
    except Exception as e:
        logger.warning("Failed to load Supabase secrets: %s — using .env fallback", e)
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
    Silently throttled to 5-second intervals.
    """
    global _last_equity_write
    now = time.time()
    if now - _last_equity_write < 5:
        return
    _last_equity_write = now

    client = _get_client()
    if client is None:
        return
    try:
        from datetime import datetime, timezone
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
    Throttled to once every 5 s.
    """
    global _last_state_write
    now = time.time()
    if now - _last_state_write < 5:
        return
    _last_state_write = now

    client = _get_client()
    if client is None:
        return
    try:
        from datetime import datetime, timezone
        client.table("bot_state").upsert({
            "id":         1,
            "state":      state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.debug("Supabase persist_bot_state error: %s", e)


# ── 5. Secret upsert helper (for setup scripts / dashboard) ───────────────

def upsert_secret(key: str, value: str, description: str = "") -> bool:
    """
    Store or update a single secret in Supabase.
    Useful for initial setup: call once per key from a setup script.
    """
    client = _get_client()
    if client is None:
        return False
    try:
        client.table("secrets").upsert({
            "key":         key,
            "value":       value,
            "description": description,
        }).execute()
        logger.info("Upserted secret: %s", key)
        return True
    except Exception as e:
        logger.warning("Failed to upsert secret %s: %s", key, e)
        return False


def is_connected() -> bool:
    """Return True if Supabase client is initialised and reachable."""
    return _get_client() is not None and _connected
