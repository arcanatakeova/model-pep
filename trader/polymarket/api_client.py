"""
Polymarket API Client
=====================
Unified REST client wrapping CLOB, Gamma, and Data APIs.

Production-grade with:
  - Per-endpoint sliding-window rate limiters
  - Circuit breakers per API base URL
  - Exponential backoff with jitter
  - Thread-safe HTTP with keep-alive
  - Request/response timing metrics
  - Structured order results and dry-run support
"""
from __future__ import annotations

import collections
import logging
import math
import random
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import OrderBookSnapshot, PolyMarket

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"

# Polymarket tick sizes
_VALID_TICK_SIZES = (0.01, 0.001, 0.0001)
_MIN_ORDER_SIZE_USDC = 5.0


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Sliding-window rate limiter.

    Tracks timestamps of recent requests in a deque and blocks (or rejects)
    when the window is full.  Thread-safe.
    """

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def acquire(self) -> bool:
        """Try to acquire a slot.  Returns True if allowed, False otherwise."""
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True
            return False

    def wait_if_needed(self) -> None:
        """Block until a request slot is available."""
        while True:
            now = time.monotonic()
            with self._lock:
                self._purge(now)
                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return
                # Compute how long to wait for the oldest entry to expire
                wait = self._timestamps[0] + self.window_seconds - now
            if wait > 0:
                time.sleep(wait + 0.01)  # small pad to avoid spin


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Circuit breaker for API endpoints.

    After ``failure_threshold`` consecutive failures the breaker *opens* and
    all calls are rejected for ``recovery_time`` seconds.  After the recovery
    period the breaker moves to *half-open* — the next call is allowed through;
    if it succeeds the breaker resets, otherwise it re-opens.
    """

    _STATE_CLOSED = "closed"
    _STATE_OPEN = "open"
    _STATE_HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_time: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self._consecutive_failures = 0
        self._state = self._STATE_CLOSED
        self._opened_at: float = 0.0
        self._lock = threading.Lock()

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._state = self._STATE_CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._state = self._STATE_OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "CircuitBreaker OPEN after %d consecutive failures",
                    self._consecutive_failures,
                )

    def is_open(self) -> bool:
        """True means *stop calling* — the endpoint is unhealthy."""
        with self._lock:
            if self._state == self._STATE_CLOSED:
                return False
            if self._state == self._STATE_OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed >= self.recovery_time:
                    self._state = self._STATE_HALF_OPEN
                    logger.info("CircuitBreaker moving to HALF-OPEN (probing)")
                    return False  # allow one probe
                return True
            # half-open — allow the probe through
            return False


# ---------------------------------------------------------------------------
# PolymarketAPIClient
# ---------------------------------------------------------------------------

class PolymarketAPIClient:
    """Unified REST client for all Polymarket APIs."""

    def __init__(self, private_key: str = "", chain_id: int = 137):
        self.private_key = private_key
        self.chain_id = chain_id
        self._client = None

        # HTTP session with keep-alive
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "Connection": "keep-alive",
        })
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0,  # we handle retries ourselves
        )
        self._session.mount("https://", adapter)

        # Cache
        self._cache: dict = {}
        self._cache_ttl = 120
        self._cache_lock = threading.Lock()

        # Request lock (thread safety for _get)
        self._request_lock = threading.Lock()

        # Per-API rate limiters
        self._rate_limiters: dict[str, RateLimiter] = {
            CLOB_BASE: RateLimiter(max_requests=60, window_seconds=60.0),
            GAMMA_BASE: RateLimiter(max_requests=120, window_seconds=60.0),
            DATA_BASE: RateLimiter(max_requests=60, window_seconds=60.0),
        }

        # Per-API circuit breakers
        self._circuit_breakers: dict[str, CircuitBreaker] = {
            CLOB_BASE: CircuitBreaker(failure_threshold=5, recovery_time=60.0),
            GAMMA_BASE: CircuitBreaker(failure_threshold=5, recovery_time=60.0),
            DATA_BASE: CircuitBreaker(failure_threshold=5, recovery_time=60.0),
        }

        # Timing metrics (simple counters; use EngineMetrics for aggregation)
        self._metrics_lock = threading.Lock()
        self._total_requests = 0
        self._total_errors = 0
        self._total_latency_ms = 0.0

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

    # -- helpers --------------------------------------------------------------

    def _base_for_url(self, url: str) -> str:
        """Extract API base URL for rate-limiter / circuit-breaker lookup."""
        for base in (CLOB_BASE, GAMMA_BASE, DATA_BASE):
            if url.startswith(base):
                return base
        return ""

    def _record_metric(self, latency_ms: float, error: bool = False) -> None:
        with self._metrics_lock:
            self._total_requests += 1
            self._total_latency_ms += latency_ms
            if error:
                self._total_errors += 1

    def get_request_metrics(self) -> dict:
        """Snapshot of request-level metrics."""
        with self._metrics_lock:
            total = self._total_requests
            return {
                "total_requests": total,
                "total_errors": self._total_errors,
                "avg_latency_ms": (
                    round(self._total_latency_ms / total, 2) if total else 0.0
                ),
            }

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

    def get_market_by_id(self, condition_id: str) -> Optional[PolyMarket]:
        """Fetch a single market by its condition ID from Gamma API."""
        cache_key = f"market_id_{condition_id}"
        cached = self._cached(cache_key, ttl=120)
        if cached is not None:
            return cached

        data = self._get(f"{GAMMA_BASE}/markets/{condition_id}")
        if not data:
            # Fallback: search by conditionId param
            data = self._get(f"{GAMMA_BASE}/markets", params={
                "conditionId": condition_id, "limit": 1,
            })
            if isinstance(data, list) and data:
                data = data[0]
            elif isinstance(data, dict) and "data" in data:
                items = data["data"]
                data = items[0] if items else None
        if not data or not isinstance(data, dict):
            return None
        market = self._parse_market(data)
        if market:
            self._store(cache_key, market)
        return market

    def get_market_by_slug(self, slug: str) -> Optional[PolyMarket]:
        """Fetch a single market by its slug from Gamma API."""
        cache_key = f"market_slug_{slug}"
        cached = self._cached(cache_key, ttl=120)
        if cached is not None:
            return cached

        data = self._get(f"{GAMMA_BASE}/markets", params={
            "slug": slug, "limit": 1,
        })
        page = data if isinstance(data, list) else (data or {}).get("data", [])
        if not page:
            return None
        market = self._parse_market(page[0])
        if market:
            self._store(cache_key, market)
        return market

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

    def get_prices_batch(self, token_ids: list[str]) -> dict[str, float]:
        """Batch price lookup for multiple tokens.

        Returns a mapping of ``{token_id: midpoint_price}``.
        Tokens that fail to resolve are omitted from the result.
        """
        results: dict[str, float] = {}
        for tid in token_ids:
            mid = self.get_midpoint(tid)
            if mid is not None:
                results[tid] = mid
        return results

    def check_order_status(self, order_id: str) -> dict:
        """Poll order fill status from CLOB API.

        Returns a dict with at least ``order_id``, ``status``, and
        ``filled_size`` keys (or an error dict on failure).
        """
        data = self._get(f"{CLOB_BASE}/order/{order_id}")
        if not data:
            return {"order_id": order_id, "status": "unknown", "error": "no_response"}
        return {
            "order_id": order_id,
            "status": data.get("status", "unknown"),
            "filled_size": float(data.get("size_matched", 0)),
            "original_size": float(data.get("original_size", 0)),
            "price": float(data.get("price", 0)),
            "side": data.get("side", ""),
            "raw": data,
        }

    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        *,
        dry_run: bool = False,
        tick_size: float = 0.01,
    ) -> Optional[dict]:
        """Place a limit order on Polymarket.

        Parameters
        ----------
        token_id : str
            CLOB token ID.
        side : str
            "BUY" or "SELL".
        price : float
            Limit price, must be a multiple of *tick_size* and in (0, 1).
        size : float
            Number of shares.  ``size * price`` must be >= $5.
        dry_run : bool
            If True, validate the order without submitting.
        tick_size : float
            Market tick size (default 0.01).

        Returns
        -------
        dict with keys: ``order_id``, ``status``, ``filled_size``, ``paper``,
        ``dry_run``, ``error``, ``error_category``.
        """
        result: dict = {
            "order_id": None,
            "status": "pending",
            "filled_size": 0.0,
            "paper": False,
            "dry_run": dry_run,
            "error": None,
            "error_category": None,
            "token_id": token_id,
            "side": side,
            "price": price,
            "size": size,
        }

        # -- Validation -------------------------------------------------------

        side = side.upper()
        if side not in ("BUY", "SELL"):
            result.update(error=f"Invalid side: {side}", error_category="invalid_params", status="rejected")
            return result

        if price <= 0 or price >= 1.0:
            result.update(error=f"Price {price} out of range (0, 1)", error_category="invalid_params", status="rejected")
            return result

        # Tick-size validation: round to nearest tick
        if tick_size > 0:
            rounded_price = round(round(price / tick_size) * tick_size, 6)
            if abs(rounded_price - price) > 1e-9:
                logger.debug("Price %.6f rounded to tick %.6f (tick_size=%.4f)", price, rounded_price, tick_size)
                price = rounded_price
                result["price"] = price

        order_value = size * price
        if order_value < _MIN_ORDER_SIZE_USDC:
            result.update(
                error=f"Order value ${order_value:.2f} below minimum ${_MIN_ORDER_SIZE_USDC}",
                error_category="invalid_params",
                status="rejected",
            )
            return result

        if dry_run:
            result["status"] = "dry_run_ok"
            logger.info(
                "POLY DRY-RUN | %s token=%s | p=%.4f | size=%.2f | value=$%.2f",
                side, token_id[:8], price, size, order_value,
            )
            return result

        # -- Paper mode -------------------------------------------------------

        if not self._client:
            logger.info(
                "POLY PAPER | %s token=%s | p=%.4f | size=%.2f",
                side, token_id[:8], price, size,
            )
            result.update(paper=True, status="paper_filled", order_id="paper")
            return result

        # -- Live order -------------------------------------------------------

        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side == "BUY" else SELL
            order_args = OrderArgs(
                token_id=token_id,
                price=round(price, 4),
                size=round(size, 2),
                side=order_side,
            )
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed)
            order_id = (resp or {}).get("orderID", "?")
            logger.info(
                "Polymarket ORDER placed: %s @ %.4f size=%.2f (id: %s)",
                side, price, size, order_id,
            )
            result.update(
                order_id=order_id,
                status=(resp or {}).get("status", "submitted"),
                filled_size=float((resp or {}).get("size_matched", 0)),
            )
            return result

        except ImportError as e:
            result.update(error=str(e), error_category="dependency", status="error")
            logger.error("Polymarket order dependency error: %s", e)
            return result
        except Exception as e:
            err_str = str(e).lower()
            if "insufficient" in err_str or "balance" in err_str:
                category = "insufficient_funds"
            elif "auth" in err_str or "credential" in err_str or "signature" in err_str:
                category = "auth_error"
            elif "invalid" in err_str or "param" in err_str:
                category = "invalid_params"
            else:
                category = "network"
            result.update(error=str(e), error_category=category, status="error")
            logger.error("Polymarket order failed (%s): %s", category, e)
            return result

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

    def get_trade_history(self, condition_id: str, since_ts: int = 0) -> list[dict]:
        """Fetch trade history for a market, optionally since a Unix timestamp.

        Returns a list of trade dicts (newest first).
        """
        params: dict = {"market": condition_id, "limit": 200}
        if since_ts > 0:
            params["since"] = since_ts
        data = self._get(f"{DATA_BASE}/trades", params=params)
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

    # ─── Health Check ──────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """Ping all three APIs and return their status.

        Returns a dict like::

            {
                "clob": {"ok": True, "latency_ms": 123.4},
                "gamma": {"ok": True, "latency_ms": 89.1},
                "data": {"ok": False, "latency_ms": 0, "error": "timeout"},
                "all_healthy": False,
            }
        """
        result: dict = {}
        checks = [
            ("clob", f"{CLOB_BASE}/tick-size", {"token_id": "0"}),
            ("gamma", f"{GAMMA_BASE}/markets", {"limit": 1}),
            ("data", f"{DATA_BASE}/leaderboard", {"limit": 1}),
        ]
        for name, url, params in checks:
            t0 = time.monotonic()
            try:
                resp = self._session.get(url, params=params, timeout=8)
                latency = (time.monotonic() - t0) * 1000
                result[name] = {
                    "ok": resp.ok,
                    "status_code": resp.status_code,
                    "latency_ms": round(latency, 1),
                }
            except Exception as e:
                latency = (time.monotonic() - t0) * 1000
                result[name] = {
                    "ok": False,
                    "latency_ms": round(latency, 1),
                    "error": str(e),
                }
        result["all_healthy"] = all(v.get("ok") for v in result.values() if isinstance(v, dict))
        return result

    # ─── Market Parsing ────────────────────────────────────────────────────

    def _parse_market(self, m: dict) -> Optional[PolyMarket]:
        """Parse a raw API response into a PolyMarket dataclass.

        Handles missing fields, wrong types, and null values gracefully.
        """
        try:
            tokens = m.get("tokens", [])
            if not isinstance(tokens, list):
                tokens = []
            yes_tok = next((t for t in tokens if isinstance(t, dict) and t.get("outcome", "").upper() == "YES"), {})
            no_tok = next((t for t in tokens if isinstance(t, dict) and t.get("outcome", "").upper() == "NO"), {})

            outcome_prices = m.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                # Sometimes returned as JSON string
                try:
                    import json
                    outcome_prices = json.loads(outcome_prices)
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = []

            if outcome_prices and isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                yes_price = _safe_float(outcome_prices[0], 0.5)
                no_price = _safe_float(outcome_prices[1], 0.5)
            else:
                yes_price = _safe_float(
                    yes_tok.get("price") or m.get("bestBid"), 0.5
                )
                no_price = _safe_float(no_tok.get("price"), 0.5)

            clob_ids = m.get("clobTokenIds", [])
            if isinstance(clob_ids, str):
                try:
                    import json
                    clob_ids = json.loads(clob_ids)
                except (json.JSONDecodeError, TypeError):
                    clob_ids = []

            yes_token_id = clob_ids[0] if clob_ids else yes_tok.get("token_id", "")
            no_token_id = clob_ids[1] if len(clob_ids) > 1 else no_tok.get("token_id", "")

            rewards = m.get("rewards") or {}
            if isinstance(rewards, dict):
                rates = rewards.get("rates") or {}
                yes_rate = _safe_float(rates.get("yes", 0), 0.0)
                no_rate = _safe_float(rates.get("no", 0), 0.0)
            else:
                yes_rate = no_rate = 0.0

            condition_id = m.get("conditionId") or m.get("condition_id", "")
            if not condition_id:
                logger.debug("Polymarket parse: skipping market with empty condition_id")
                return None

            return PolyMarket(
                condition_id=condition_id,
                question=str(m.get("question", "")),
                slug=str(m.get("slug") or m.get("market_slug", "")),
                end_date=str(m.get("endDate") or m.get("end_date_iso", "")),
                active=bool(m.get("active")),
                accepting_orders=bool(m.get("acceptingOrders", m.get("accepting_orders", True))),
                volume_24h=_safe_float(m.get("volume24hr") or m.get("volume_24h"), 0.0),
                volume_total=_safe_float(m.get("volume") or m.get("volumeNum"), 0.0),
                yes_token_id=str(yes_token_id or ""),
                no_token_id=str(no_token_id or ""),
                yes_price=yes_price,
                no_price=no_price,
                yes_reward_rate=yes_rate,
                no_reward_rate=no_rate,
                tags=m.get("tags", []) if isinstance(m.get("tags"), list) else [],
                event_id=str(m.get("eventId") or m.get("event_id", "")),
                description=str(m.get("description", "")),
                resolution_source=str(m.get("resolutionSource") or m.get("resolution_source", "")),
                open_interest=_safe_float(m.get("openInterest") or m.get("open_interest"), 0.0),
                best_bid=_safe_float(m.get("bestBid") or m.get("best_bid"), 0.0),
                best_ask=_safe_float(m.get("bestAsk") or m.get("best_ask"), 0.0),
                tick_size=_safe_float(m.get("minimum_tick_size") or m.get("tick_size"), 0.01),
            )
        except Exception as e:
            logger.debug("Polymarket parse error: %s", e)
            return None

    def _parse_orderbook(self, data: dict, token_id: str = "") -> Optional[OrderBookSnapshot]:
        """Parse raw orderbook response into an OrderBookSnapshot.

        Expected shape::

            {
                "bids": [{"price": "0.55", "size": "100"}, ...],
                "asks": [{"price": "0.57", "size": "80"}, ...],
                ...
            }
        """
        try:
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])

            bids: list[tuple[float, float]] = []
            for b in raw_bids:
                if isinstance(b, dict):
                    bids.append((_safe_float(b.get("price"), 0), _safe_float(b.get("size"), 0)))
                elif isinstance(b, (list, tuple)) and len(b) >= 2:
                    bids.append((float(b[0]), float(b[1])))
            bids.sort(key=lambda x: x[0], reverse=True)  # best bid first

            asks: list[tuple[float, float]] = []
            for a in raw_asks:
                if isinstance(a, dict):
                    asks.append((_safe_float(a.get("price"), 0), _safe_float(a.get("size"), 0)))
                elif isinstance(a, (list, tuple)) and len(a) >= 2:
                    asks.append((float(a[0]), float(a[1])))
            asks.sort(key=lambda x: x[0])  # best ask first

            best_bid = bids[0][0] if bids else 0.0
            best_ask = asks[0][0] if asks else 0.0
            mid = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0
            spread = (best_ask - best_bid) if (best_bid > 0 and best_ask > 0) else 0.0

            # Depth within 10% of mid
            bid_depth = sum(s for p, s in bids if mid > 0 and p >= mid * 0.9)
            ask_depth = sum(s for p, s in asks if mid > 0 and p <= mid * 1.1)
            total_depth = bid_depth + ask_depth
            imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0

            now_iso = datetime.now(timezone.utc).isoformat()

            return OrderBookSnapshot(
                token_id=token_id or data.get("asset_id", ""),
                timestamp=now_iso,
                bids=bids,
                asks=asks,
                mid_price=round(mid, 6),
                spread=round(spread, 6),
                bid_depth_10pct=round(bid_depth, 2),
                ask_depth_10pct=round(ask_depth, 2),
                imbalance=round(imbalance, 4),
            )
        except Exception as e:
            logger.debug("Orderbook parse error: %s", e)
            return None

    # ─── HTTP / Cache ──────────────────────────────────────────────────────

    def _get(
        self,
        url: str,
        params: dict = None,
        cache_ttl: int = 60,
    ) -> Optional[dict | list]:
        """GET with caching, rate limiting, circuit breaking, and retry.

        Thread-safe.  Uses exponential backoff with jitter on retries.
        """
        cache_key = f"{url}:{params}"
        cached = self._cached(cache_key, ttl=cache_ttl)
        if cached is not None:
            return cached

        base = self._base_for_url(url)

        # Circuit breaker check
        cb = self._circuit_breakers.get(base)
        if cb and cb.is_open():
            logger.warning("CircuitBreaker open for %s — skipping request", base)
            return None

        # Rate limiter
        rl = self._rate_limiters.get(base)
        if rl:
            rl.wait_if_needed()

        max_retries = 3
        for attempt in range(max_retries):
            t0 = time.monotonic()
            try:
                with self._request_lock:
                    resp = self._session.get(url, params=params, timeout=10)
                latency_ms = (time.monotonic() - t0) * 1000
                self._record_metric(latency_ms)

                if resp.status_code == 429:
                    # Rate limited by server — back off
                    backoff = _backoff_seconds(attempt, base_sec=2.0, max_sec=15.0)
                    logger.debug("429 from %s, backing off %.1fs", url, backoff)
                    if cb:
                        cb.record_failure()
                    time.sleep(backoff)
                    continue

                if resp.status_code >= 500:
                    # Server error — retryable
                    if cb:
                        cb.record_failure()
                    self._record_metric(0, error=True)
                    backoff = _backoff_seconds(attempt, base_sec=1.0, max_sec=10.0)
                    logger.debug("Server error %d from %s, retrying in %.1fs", resp.status_code, url, backoff)
                    time.sleep(backoff)
                    continue

                if resp.ok:
                    data = resp.json()
                    if cb:
                        cb.record_success()
                    self._store(cache_key, data)
                    return data
                else:
                    # 4xx (non-429) — do not retry
                    self._record_metric(latency_ms, error=True)
                    logger.debug("Client error %d from %s", resp.status_code, url)
                    if cb:
                        cb.record_failure()
                    return None

            except requests.exceptions.Timeout:
                latency_ms = (time.monotonic() - t0) * 1000
                self._record_metric(latency_ms, error=True)
                if cb:
                    cb.record_failure()
                logger.debug("Timeout for %s (attempt %d)", url, attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(_backoff_seconds(attempt, base_sec=1.0, max_sec=8.0))
            except requests.exceptions.ConnectionError:
                self._record_metric(0, error=True)
                if cb:
                    cb.record_failure()
                logger.debug("Connection error for %s (attempt %d)", url, attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(_backoff_seconds(attempt, base_sec=2.0, max_sec=15.0))
            except Exception as e:
                self._record_metric(0, error=True)
                logger.debug("Polymarket API error: %s", e)
                if cb:
                    cb.record_failure()
                if attempt < max_retries - 1:
                    time.sleep(_backoff_seconds(attempt))

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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default: float = 0.0) -> float:
    """Convert *val* to float, returning *default* on any failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _backoff_seconds(attempt: int, base_sec: float = 1.0, max_sec: float = 10.0) -> float:
    """Exponential backoff with full jitter."""
    exp = base_sec * (2 ** attempt)
    capped = min(exp, max_sec)
    return random.uniform(0.0, capped)
