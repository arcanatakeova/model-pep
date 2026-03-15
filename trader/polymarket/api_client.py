"""
Polymarket API Client
=====================
Unified REST client wrapping CLOB, Gamma, and Data APIs.
Refactored from the original polymarket.py PolymarketTrader.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional

import requests

from .models import PolyMarket

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"


class PolymarketAPIClient:
    """Unified REST client for all Polymarket APIs."""

    def __init__(self, private_key: str = "", chain_id: int = 137):
        self.private_key = private_key
        self.chain_id = chain_id
        self._client = None
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._cache: dict = {}
        self._cache_ttl = 120
        self._cache_lock = threading.Lock()

        if private_key:
            self._init_clob_client()

    def _init_clob_client(self):
        """Initialize py-clob-client with L2 API credentials."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            import config as _cfg

            l1 = ClobClient(
                host=CLOB_BASE,
                key=self.private_key,
                chain_id=self.chain_id,
            )

            if _cfg.POLYMARKET_API_KEY and _cfg.POLYMARKET_API_SECRET and _cfg.POLYMARKET_PASSPHRASE:
                creds = ApiCreds(
                    api_key=_cfg.POLYMARKET_API_KEY,
                    api_secret=_cfg.POLYMARKET_API_SECRET,
                    api_passphrase=_cfg.POLYMARKET_PASSPHRASE,
                )
                logger.info("Polymarket: using cached L2 API credentials")
            else:
                raw = l1.create_or_derive_api_creds()
                creds = ApiCreds(
                    api_key=raw.get("apiKey", ""),
                    api_secret=raw.get("secret", ""),
                    api_passphrase=raw.get("passphrase", ""),
                )
                logger.info(
                    "Polymarket: derived L2 creds (set POLYMARKET_API_KEY/SECRET/PASSPHRASE "
                    "in .env to skip re-derivation). apiKey=%s", creds.api_key
                )

            self._client = ClobClient(
                host=CLOB_BASE,
                key=self.private_key,
                chain_id=self.chain_id,
                creds=creds,
            )
            logger.info("Polymarket CLOB client ready (live mode, L2 auth)")

        except ImportError:
            logger.warning("py-clob-client not installed -- Polymarket in data-only mode.")
        except Exception as e:
            logger.warning("Polymarket client init failed: %s", e)

    # ─── Gamma API (market discovery, no auth) ─────────────────────────────

    def get_active_markets(self, limit: int = 100, min_volume: float = 1000) -> list[PolyMarket]:
        """Fetch active markets sorted by 24h volume using Gamma API."""
        cache_key = f"markets_{limit}"
        cached = self._cached(cache_key, ttl=300)
        if cached is not None:
            return cached

        markets: list[PolyMarket] = []
        offset = 0
        page_size = 100

        while len(markets) < limit:
            data = self._get(f"{GAMMA_BASE}/markets", params={
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
                "limit": page_size,
                "offset": offset,
            })
            if not data:
                break
            page = data if isinstance(data, list) else data.get("data", [])
            if not page:
                break
            for m in page:
                parsed = self._parse_market(m)
                if parsed and parsed.active and parsed.accepting_orders:
                    markets.append(parsed)
            if len(page) < page_size:
                break
            offset += page_size

        markets = [m for m in markets if m.volume_24h >= min_volume]
        markets.sort(key=lambda m: m.volume_24h, reverse=True)

        self._store(cache_key, markets[:limit])
        logger.info("Polymarket: %d active markets loaded (Gamma API)", len(markets))
        return markets[:limit]

    def get_event(self, event_id: str) -> Optional[dict]:
        """Fetch event details from Gamma API."""
        return self._get(f"{GAMMA_BASE}/events/{event_id}")

    def get_related_markets(self, event_id: str) -> list[dict]:
        """Fetch markets belonging to the same event."""
        data = self._get(f"{GAMMA_BASE}/markets", params={
            "event_id": event_id, "limit": 50,
        })
        if isinstance(data, list):
            return data
        return (data or {}).get("data", [])

    def search_markets(self, query: str) -> list[dict]:
        """Search markets by query string."""
        data = self._get(f"{GAMMA_BASE}/markets", params={
            "search": query, "limit": 20, "active": "true",
        })
        if isinstance(data, list):
            return data
        return (data or {}).get("data", [])

    # ─── CLOB API (order book, trading) ────────────────────────────────────

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch order book for a token."""
        data = self._get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        return data or {}

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token."""
        data = self._get(f"{CLOB_BASE}/midpoint", params={"token_id": token_id})
        if data and "mid" in data:
            return float(data["mid"])
        return None

    def get_tick_size(self, token_id: str) -> float:
        """Get tick size for a token."""
        data = self._get(f"{CLOB_BASE}/tick-size", params={"token_id": token_id})
        if data and "minimum_tick_size" in data:
            return float(data["minimum_tick_size"])
        return 0.01

    def place_limit_order(self, token_id: str, side: str, price: float,
                          size: float) -> Optional[dict]:
        """Place a limit order on Polymarket."""
        if not self._client:
            logger.info(
                "POLY PAPER | %s token=%s | p=%.4f | size=%.2f",
                side, token_id[:8], price, size,
            )
            return {"paper": True, "side": side, "size": size,
                    "price": price, "token_id": token_id}

        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side.upper() == "BUY" else SELL
            order_args = OrderArgs(
                token_id=token_id,
                price=round(price, 4),
                size=round(size, 2),
                side=order_side,
            )
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed)
            order_id = (resp or {}).get("orderID", "?")
            logger.info("Polymarket ORDER placed: %s @ %.4f size=%.2f (id: %s)",
                        side, price, size, order_id)
            return resp
        except Exception as e:
            logger.error("Polymarket order failed: %s", e)
            return None

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel a specific order."""
        if not self._client:
            return None
        try:
            return self._client.cancel(order_id)
        except Exception as e:
            logger.warning("Cancel order failed: %s", e)
            return None

    def cancel_all_orders(self) -> Optional[dict]:
        """Cancel all open orders."""
        if not self._client:
            return None
        try:
            return self._client.cancel_all()
        except Exception as e:
            logger.warning("Cancel all orders failed: %s", e)
            return None

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        if not self._client:
            return []
        try:
            return self._client.get_orders() or []
        except Exception as e:
            logger.warning("Get open orders failed: %s", e)
            return []

    # ─── Data API (positions, leaderboard, no auth) ────────────────────────

    def get_leaderboard(self, limit: int = 50) -> list[dict]:
        """Fetch top traders from leaderboard."""
        data = self._get(f"{DATA_BASE}/leaderboard", params={
            "limit": limit, "window": "all",
        }, cache_ttl=1800)
        if isinstance(data, list):
            return data
        return (data or {}).get("data", [])

    def get_trader_positions(self, address: str) -> list[dict]:
        """Fetch positions for a specific trader."""
        data = self._get(f"{DATA_BASE}/positions", params={
            "user": address, "limit": 100,
        }, cache_ttl=300)
        if isinstance(data, list):
            return data
        return (data or {}).get("data", [])

    def get_market_trades(self, condition_id: str, limit: int = 100) -> list[dict]:
        """Fetch recent trades for a market."""
        data = self._get(f"{DATA_BASE}/trades", params={
            "market": condition_id, "limit": limit,
        })
        if isinstance(data, list):
            return data
        return (data or {}).get("data", [])

    def get_positions(self) -> list[dict]:
        """Fetch open Polymarket positions via CLOB client."""
        if not self._client:
            return []
        try:
            return self._client.get_positions() or []
        except Exception as e:
            logger.warning("Failed to fetch Polymarket positions: %s", e)
            return []

    # ─── Market Parsing ────────────────────────────────────────────────────

    def _parse_market(self, m: dict) -> Optional[PolyMarket]:
        """Parse a raw API response into a PolyMarket dataclass."""
        try:
            tokens = m.get("tokens", [])
            yes_tok = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), {})
            no_tok = next((t for t in tokens if t.get("outcome", "").upper() == "NO"), {})

            outcome_prices = m.get("outcomePrices", [])
            if outcome_prices and len(outcome_prices) >= 2:
                yes_price = float(outcome_prices[0] or 0.5)
                no_price = float(outcome_prices[1] or 0.5)
            else:
                yes_price = float(yes_tok.get("price") or m.get("bestBid") or 0.5)
                no_price = float(no_tok.get("price") or 0.5)

            clob_ids = m.get("clobTokenIds", [])
            yes_token_id = clob_ids[0] if clob_ids else yes_tok.get("token_id", "")
            no_token_id = clob_ids[1] if len(clob_ids) > 1 else no_tok.get("token_id", "")

            rewards = m.get("rewards") or {}
            if isinstance(rewards, dict):
                rates = rewards.get("rates") or {}
                yes_rate = float(rates.get("yes", 0) or 0)
                no_rate = float(rates.get("no", 0) or 0)
            else:
                yes_rate = no_rate = 0.0

            return PolyMarket(
                condition_id=m.get("conditionId") or m.get("condition_id", ""),
                question=m.get("question", ""),
                slug=m.get("slug") or m.get("market_slug", ""),
                end_date=m.get("endDate") or m.get("end_date_iso", ""),
                active=bool(m.get("active")),
                accepting_orders=bool(m.get("acceptingOrders", m.get("accepting_orders", True))),
                volume_24h=float(m.get("volume24hr") or m.get("volume_24h") or 0),
                volume_total=float(m.get("volume") or 0),
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                yes_price=yes_price,
                no_price=no_price,
                yes_reward_rate=yes_rate,
                no_reward_rate=no_rate,
                tags=m.get("tags", []),
                event_id=m.get("eventId") or m.get("event_id", ""),
                description=m.get("description", ""),
                resolution_source=m.get("resolutionSource", ""),
                open_interest=float(m.get("openInterest") or 0),
                best_bid=float(m.get("bestBid") or 0),
                best_ask=float(m.get("bestAsk") or 0),
            )
        except Exception as e:
            logger.debug("Polymarket parse error: %s", e)
            return None

    # ─── HTTP / Cache ──────────────────────────────────────────────────────

    def _get(self, url: str, params: dict = None,
             cache_ttl: int = 60) -> Optional[dict | list]:
        """GET with caching and retry logic."""
        cache_key = f"{url}:{params}"
        cached = self._cached(cache_key, ttl=cache_ttl)
        if cached is not None:
            return cached

        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params, timeout=10)
                if resp.status_code == 429:
                    time.sleep(2 + attempt * 2)
                    continue
                if resp.ok:
                    data = resp.json()
                    self._store(cache_key, data)
                    return data
            except Exception as e:
                logger.debug("Polymarket API error: %s", e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None

    def _cached(self, key: str, ttl: int = None):
        ttl = ttl or self._cache_ttl
        with self._cache_lock:
            if key in self._cache:
                val, ts = self._cache[key]
                if time.time() - ts < ttl:
                    return val
        return None

    def _store(self, key: str, val):
        with self._cache_lock:
            if len(self._cache) >= 200:
                oldest = sorted(self._cache, key=lambda k: self._cache[k][1])
                for k in oldest[:50]:
                    del self._cache[k]
            self._cache[key] = (val, time.time())
