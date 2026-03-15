"""
Birdeye API — Real-Time Solana Token Intelligence
==================================================
Provides real-time prices, OHLCV, security analysis, and trending detection
for Solana tokens using the Birdeye REST API.

API key required: get one at https://birdeye.so (Starter plan recommended)
Set environment variable: BIRDEYE_API_KEY=your_key_here

CU budget notes (Starter = 5M CUs/mo):
  - /defi/price:            1 CU per token
  - /defi/multi_price:      1 CU per token (batch, very efficient)
  - /defi/token_security:   1 CU per call
  - /defi/ohlcv:            1 CU per call
  - /defi/trending_tokens:  1 CU per call
Use caching aggressively to stay within budget.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

BIRDEYE_BASE = "https://public-api.birdeye.so"
_CHAIN = "solana"


@dataclass
class BirdeyePrice:
    """Real-time token price data from Birdeye."""
    address: str
    price_usd: float
    price_change_24h_pct: float = 0.0
    volume_24h_usd: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    fetched_at: float = field(default_factory=time.time)


@dataclass
class BirdeyeSecurity:
    """Token security analysis from Birdeye."""
    address: str
    # Authorities
    mint_authority: Optional[str] = None    # None = disabled (good)
    freeze_authority: Optional[str] = None  # None = disabled (good)
    # Holder analysis
    creator_address: Optional[str] = None
    creator_balance_pct: float = 0.0        # % creator holds
    top10_holder_pct: float = 0.0           # % top 10 holders own
    # Supply / lock
    total_supply: float = 0.0
    circulating_supply: float = 0.0
    is_token_2022: bool = False
    # Risk flags derived
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class BirdeyeOHLCV:
    """OHLCV candle from Birdeye."""
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int   # unix seconds


class BirdeyeClient:
    """
    Birdeye API client with intelligent caching to minimise CU consumption.

    Price cache: 10s TTL (fresh enough for trading decisions)
    Security cache: 10min TTL (rarely changes)
    OHLCV cache: 30s TTL per resolution
    Trending cache: 60s TTL
    """

    # Cache TTLs in seconds
    _PRICE_TTL     = 3    # 3s for real-time price monitoring of held positions
    _SECURITY_TTL  = 600   # 10 minutes
    _OHLCV_TTL     = 30
    _TRENDING_TTL  = 60

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or config.BIRDEYE_API_KEY
        self._session = requests.Session()
        if self._api_key:
            self._session.headers.update({
                "X-API-KEY": self._api_key,
                "x-chain": _CHAIN,
            })
        self._price_cache:    dict[str, tuple[BirdeyePrice, float]] = {}
        self._security_cache: dict[str, tuple[BirdeyeSecurity, float]] = {}
        self._ohlcv_cache:    dict[str, tuple[list[BirdeyeOHLCV], float]] = {}
        self._trending_cache: tuple[list[dict], float] = ([], 0.0)

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    # ─── Price ─────────────────────────────────────────────────────────────────

    def get_price(self, mint_address: str) -> Optional[BirdeyePrice]:
        """Get real-time USD price for a single token."""
        if not self.enabled:
            return None
        now = time.time()
        if mint_address in self._price_cache:
            p, ts = self._price_cache[mint_address]
            if now - ts < self._PRICE_TTL:
                return p

        data = self._get("/defi/price", {"address": mint_address})
        if not data or not data.get("success"):
            return None

        d = data.get("data", {})
        price = BirdeyePrice(
            address=mint_address,
            price_usd=float(d.get("value", 0) or 0),
            price_change_24h_pct=float(d.get("priceChange24H", 0) or 0),
            volume_24h_usd=float(d.get("volume24H", 0) or 0),
            liquidity_usd=float(d.get("liquidity", 0) or 0),
            fetched_at=now,
        )
        self._price_cache[mint_address] = (price, now)
        self._evict_cache(self._price_cache, self._PRICE_TTL)
        return price

    def get_multi_price(self, mint_addresses: list[str]) -> dict[str, BirdeyePrice]:
        """
        Batch price fetch — 1 CU total regardless of token count.
        Returns dict[mint_address → BirdeyePrice].
        """
        if not self.enabled or not mint_addresses:
            return {}

        now = time.time()
        # Only fetch addresses that are stale
        stale = [m for m in mint_addresses
                 if m not in self._price_cache
                 or now - self._price_cache[m][1] >= self._PRICE_TTL]
        fresh = {m: self._price_cache[m][0]
                 for m in mint_addresses if m not in stale}

        if stale:
            # Birdeye multi_price: list_address is comma-separated
            data = self._get("/defi/multi_price",
                             {"list_address": ",".join(stale)})
            if data and data.get("success"):
                for addr, d in (data.get("data") or {}).items():
                    if not d:
                        continue
                    p = BirdeyePrice(
                        address=addr,
                        price_usd=float(d.get("value", 0) or 0),
                        price_change_24h_pct=float(d.get("priceChange24H", 0) or 0),
                        volume_24h_usd=float(d.get("volume24H", 0) or 0),
                        liquidity_usd=float(d.get("liquidity", 0) or 0),
                        fetched_at=now,
                    )
                    self._price_cache[addr] = (p, now)
                    fresh[addr] = p
            self._evict_cache(self._price_cache, self._PRICE_TTL)

        return fresh

    # ─── Token Overview ────────────────────────────────────────────────────────

    def get_token_overview(self, mint_address: str) -> Optional[dict]:
        """
        Full token overview: price, volume, liquidity, market cap, holder count.
        Richer than /defi/price — use for entry-point analysis.
        """
        if not self.enabled:
            return None
        data = self._get("/defi/token_overview", {"address": mint_address})
        if not data or not data.get("success"):
            return None
        return data.get("data")

    # ─── Security ──────────────────────────────────────────────────────────────

    def get_security(self, mint_address: str) -> Optional[BirdeyeSecurity]:
        """
        Token security analysis: mint/freeze authority, creator holdings,
        top-10 holder concentration, Token-2022 detection.
        """
        if not self.enabled:
            return None
        now = time.time()
        if mint_address in self._security_cache:
            s, ts = self._security_cache[mint_address]
            if now - ts < self._SECURITY_TTL:
                return s

        data = self._get("/defi/token_security", {"address": mint_address})
        if not data or not data.get("success"):
            return None

        d = data.get("data", {})
        flags = []

        mint_auth  = d.get("mintAuthority")    # None = disabled
        freeze_auth = d.get("freezeAuthority")  # None = disabled

        creator_pct = float(d.get("creatorPercentage", 0) or 0)
        top10_pct   = float(d.get("top10HolderPercent", 0) or 0)

        if mint_auth:
            flags.append("Mint authority active (can inflate supply)")
        if freeze_auth:
            flags.append("Freeze authority active (can freeze wallets)")
        if creator_pct > 0.20:
            flags.append(f"Creator holds {creator_pct:.0%} of supply")
        elif creator_pct > 0.10:
            flags.append(f"Creator holds {creator_pct:.0%}")
        if top10_pct > 0.70:
            flags.append(f"Top 10 holders own {top10_pct:.0%} (concentrated)")

        sec = BirdeyeSecurity(
            address=mint_address,
            mint_authority=mint_auth,
            freeze_authority=freeze_auth,
            creator_address=d.get("creatorAddress"),
            creator_balance_pct=creator_pct,
            top10_holder_pct=top10_pct,
            total_supply=float(d.get("totalSupply", 0) or 0),
            circulating_supply=float(d.get("circulatingSupply", 0) or 0),
            is_token_2022=bool(d.get("isToken2022", False)),
            risk_flags=flags,
        )
        self._security_cache[mint_address] = (sec, now)
        return sec

    # ─── OHLCV ─────────────────────────────────────────────────────────────────

    def get_ohlcv(self, mint_address: str, interval: str = "15m",
                  limit: int = 50) -> list[BirdeyeOHLCV]:
        """
        Fetch OHLCV candles for technical analysis.
        interval: "1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "8H", "12H", "1D", "3D", "1W", "1M"
        """
        if not self.enabled:
            return []
        cache_key = f"{mint_address}:{interval}"
        now = time.time()
        if cache_key in self._ohlcv_cache:
            candles, ts = self._ohlcv_cache[cache_key]
            if now - ts < self._OHLCV_TTL:
                return candles

        time_to = int(now)
        # Compute time_from based on interval and limit
        interval_secs = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
            "8H": 28800, "12H": 43200, "1D": 86400,
        }.get(interval, 900)
        time_from = time_to - (interval_secs * limit)

        data = self._get("/defi/ohlcv", {
            "address": mint_address,
            "address_type": "token",
            "type": interval,
            "time_from": time_from,
            "time_to": time_to,
        })
        candles = []
        if data and data.get("success"):
            for item in (data.get("data", {}).get("items") or []):
                try:
                    candles.append(BirdeyeOHLCV(
                        open=float(item.get("o", 0) or 0),
                        high=float(item.get("h", 0) or 0),
                        low=float(item.get("l", 0) or 0),
                        close=float(item.get("c", 0) or 0),
                        volume=float(item.get("v", 0) or 0),
                        timestamp=int(item.get("unixTime", 0)),
                    ))
                except Exception:
                    pass

        self._ohlcv_cache[cache_key] = (candles, now)
        return candles

    # ─── Trending ──────────────────────────────────────────────────────────────

    def get_trending_tokens(self, limit: int = 20,
                            min_liquidity: float = 10_000) -> list[dict]:
        """
        Fetch trending Solana tokens sorted by 24h volume.
        Returns raw dicts with address, symbol, price, volume, etc.
        """
        if not self.enabled:
            return []
        now = time.time()
        cached, ts = self._trending_cache
        if cached and now - ts < self._TRENDING_TTL:
            return cached

        data = self._get("/defi/trending_tokens", {
            "sort_by": "v24hUSD",
            "sort_type": "desc",
            "offset": 0,
            "limit": limit,
            "min_liquidity": min_liquidity,
        })
        tokens = []
        if data and data.get("success"):
            for t in (data.get("data", {}).get("tokens") or []):
                tokens.append({
                    "address": t.get("address", ""),
                    "symbol": t.get("symbol", ""),
                    "name": t.get("name", ""),
                    "price_usd": float(t.get("price", 0) or 0),
                    "volume_24h": float(t.get("v24hUSD", 0) or 0),
                    "price_change_24h": float(t.get("priceChange24H", 0) or 0),
                    "liquidity": float(t.get("liquidity", 0) or 0),
                    "market_cap": float(t.get("mc", 0) or 0),
                })
        self._trending_cache = (tokens, now)
        return tokens

    # ─── New Tokens ────────────────────────────────────────────────────────────

    def get_new_listings(self, limit: int = 20,
                         min_liquidity: float = 10_000) -> list[dict]:
        """
        Fetch newly listed Solana tokens.
        Sorted by listing time, filtered by minimum liquidity.
        """
        if not self.enabled:
            return []

        data = self._get("/defi/trending_tokens", {
            "sort_by": "listingTime",
            "sort_type": "desc",
            "offset": 0,
            "limit": limit,
            "min_liquidity": min_liquidity,
        })
        tokens = []
        if data and data.get("success"):
            for t in (data.get("data", {}).get("tokens") or []):
                tokens.append({
                    "address": t.get("address", ""),
                    "symbol": t.get("symbol", ""),
                    "name": t.get("name", ""),
                    "volume_24h": float(t.get("v24hUSD", 0) or 0),
                    "price_change_24h": float(t.get("priceChange24H", 0) or 0),
                    "liquidity": float(t.get("liquidity", 0) or 0),
                    "market_cap": float(t.get("mc", 0) or 0),
                    "listing_time": t.get("listingTime"),
                })
        return tokens

    # ─── HTTP ──────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict = None,
             timeout: int = 8) -> Optional[dict]:
        """Make a GET request to the Birdeye API."""
        if not self.enabled:
            return None
        url = f"{BIRDEYE_BASE}{path}"
        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("Birdeye rate limit — waiting %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code == 401:
                    logger.error("Birdeye: invalid API key")
                    return None
                if not resp.ok:
                    logger.debug("Birdeye %s → %d: %s",
                                 path, resp.status_code, resp.text[:100])
                    return None
                return resp.json()
            except Exception as e:
                logger.debug("Birdeye request error (%s): %s", path, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None

    # ─── Cache helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _evict_cache(cache: dict, ttl: float, max_size: int = 500):
        """Evict expired and overflow entries from a cache dict."""
        if len(cache) <= max_size:
            return
        now = time.time()
        expired = [k for k, (_, ts) in cache.items() if now - ts > ttl]
        for k in expired:
            del cache[k]
        # If still over limit, remove oldest
        if len(cache) > max_size:
            by_age = sorted(cache.items(), key=lambda x: x[1][1])
            for k, _ in by_age[:len(cache) - max_size]:
                del cache[k]
