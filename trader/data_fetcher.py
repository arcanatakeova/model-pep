"""
Data Fetcher - Retrieves market data from free public APIs + Binance WebSocket.

Sources:
  - CoinGecko       : crypto OHLCV, market cap, volume (free, no key)
  - CoinCap         : real-time crypto prices (free, no key)
  - CryptoCompare   : crypto historical OHLCV (free, no key)
  - ExchangeRate API: forex rates (free, no key)
  - Yahoo Finance   : stocks & ETFs via yfinance (unofficial, no key)
  - Binance WS      : real-time tick prices <50ms latency (free, no key)
  - Binance REST    : funding rates, order book (free, requires API key for trading)
"""
from __future__ import annotations
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import websocket  # websocket-client package

import requests
import pandas as pd

import config

logger = logging.getLogger(__name__)

# ─── Thread-safe in-memory cache with TTL eviction ────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_MAX_ENTRIES = 500          # Hard cap to prevent memory leak
_CACHE_EVICT_INTERVAL = 300       # Prune expired entries every 5 minutes
_cache_last_evict = 0.0


def _cached(key: str, ttl: int = config.DATA_CACHE_TTL):
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None:
            value, ts = entry
            if time.time() - ts < ttl:
                return value
    return None


def _store(key: str, value):
    global _cache_last_evict
    now = time.time()
    with _cache_lock:
        _cache[key] = (value, now)
        # Periodic eviction: remove expired + cap size
        if now - _cache_last_evict > _CACHE_EVICT_INTERVAL or len(_cache) > _CACHE_MAX_ENTRIES:
            _evict_cache(now)
            _cache_last_evict = now
    return value


def _evict_cache(now: float):
    """Remove expired entries and cap total size. Must be called under _cache_lock."""
    max_ttl = 3600  # Never keep anything older than 1 hour
    expired = [k for k, (_, ts) in _cache.items() if now - ts > max_ttl]
    for k in expired:
        del _cache[k]
    # If still over cap, evict oldest entries
    if len(_cache) > _CACHE_MAX_ENTRIES:
        sorted_keys = sorted(_cache, key=lambda k: _cache[k][1])
        for k in sorted_keys[:len(_cache) - _CACHE_MAX_ENTRIES]:
            del _cache[k]


def _get(url: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
    """HTTP GET with basic error handling, rate-limit backoff, and JSON validation."""
    for attempt in range(3):
        try:
            headers = {"Accept": "application/json", "User-Agent": "ai-trader/3.0"}
            if config.COINGECKO_API_KEY and "coingecko" in url:
                headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = 2 + attempt * 2
                logger.debug("Rate limited by %s, waiting %ds", url, wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                logger.debug("Server error %d from %s", resp.status_code, url)
                if attempt < 2:
                    time.sleep(1 + attempt)
                continue
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                logger.debug("Invalid JSON from %s", url)
                return None
        except requests.Timeout:
            logger.debug("Timeout fetching %s (attempt %d)", url, attempt + 1)
            if attempt < 2:
                time.sleep(1)
        except requests.RequestException as e:
            logger.debug("Request failed (%s): %s", url, e)
            if attempt < 2:
                time.sleep(1)
    return None

# ─── Binance WebSocket Real-Time Price Feed ────────────────────────────────────

class BinanceWebSocketFeed:
    """
    Maintains a persistent WebSocket connection to Binance for real-time
    trade prices on BTC, ETH, SOL, and BNB. Updates an in-memory price cache
    with <50ms latency — orders of magnitude faster than REST polling.

    Uses websocket-client library (proper RFC 6455 handshake, automatic
    ping/pong, clean reconnect) instead of manual raw socket implementation.

    Usage:
        ws = BinanceWebSocketFeed()
        ws.start()
        price = ws.get_price("BTC")   # instant, no HTTP call
    """

    _SYMBOLS = {
        "BTC": "btcusdt",
        "ETH": "ethusdt",
        "SOL": "solusdt",
        "BNB": "bnbusdt",
    }

    def __init__(self):
        self._prices:    dict[str, float] = {}
        self._volumes:   dict[str, float] = {}
        self._changes:   dict[str, float] = {}
        self._last_tick: dict[str, float] = {}
        self._lock       = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running    = False
        self._connected  = False
        self._ws: Optional[websocket.WebSocketApp] = None
        self._geo_blocked = False  # True after a 451 → switch to binance.us

    def start(self):
        """Launch WebSocket listener in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_forever, name="BinanceWS", daemon=True)
        self._thread.start()
        logger.info("BinanceWebSocketFeed started — streams: %s",
                    ", ".join(self._SYMBOLS.keys()))

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()

    def get_price(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._prices.get(symbol.upper())

    def get_24h_change(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._changes.get(symbol.upper())

    def get_volume(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._volumes.get(symbol.upper())

    def is_fresh(self, symbol: str, max_age_sec: float = 10.0) -> bool:
        ts = self._last_tick.get(symbol.upper(), 0)
        return (time.time() - ts) < max_age_sec

    def get_all_prices(self) -> dict[str, float]:
        with self._lock:
            return dict(self._prices)

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Internal ─────────────────────────────────────────────────────────────

    def _stream_names(self) -> str:
        return "/".join(f"{s}@miniTicker" for s in self._SYMBOLS.values())

    def _run_forever(self):
        """Reconnecting loop — websocket-client handles frame parsing & ping/pong."""
        backoff = 1
        while self._running:
            host = "stream.binance.us" if self._geo_blocked else "stream.binance.com"
            url  = f"wss://{host}:9443/stream?streams={self._stream_names()}"
            try:
                self._ws = websocket.WebSocketApp(
                    url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                # ping_interval keeps the connection alive; reconnect=0 means
                # we manage reconnect ourselves so backoff is respected.
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
                backoff = 1  # clean disconnect → reset backoff
            except Exception as e:
                self._connected = False
                logger.warning("BinanceWS error: %s — reconnecting in %ds", e, backoff)
            if self._running:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _on_open(self, ws):
        self._connected = True
        logger.info("BinanceWS connected")

    def _on_error(self, ws, error):
        self._connected = False
        if "451" in str(error):
            self._geo_blocked = True
            logger.warning("BinanceWS geo-blocked (451) — switching to stream.binance.us")
        else:
            logger.warning("BinanceWS disconnected: %s", error)

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        if self._running:
            logger.debug("BinanceWS closed (code=%s) — will reconnect", close_status_code)

    def _on_message(self, ws, message):
        """Parse combined-stream miniTicker payload and update price cache."""
        try:
            outer = json.loads(message)
            data  = outer.get("data", outer)
            if data.get("e") != "24hrMiniTicker":
                return

            raw_sym = data.get("s", "")
            close   = float(data.get("c", 0))
            vol     = float(data.get("q", 0))
            o       = float(data.get("o", 0))

            for short, binance in self._SYMBOLS.items():
                if binance.upper() == raw_sym.upper():
                    pct_change = ((close - o) / o * 100) if o > 0 else 0.0
                    with self._lock:
                        self._prices[short]    = close
                        self._volumes[short]   = vol
                        self._changes[short]   = round(pct_change, 3)
                        self._last_tick[short] = time.time()
                    break
        except Exception as e:
            logger.debug("WS message parse error: %s", e)


# ─── Global WebSocket instance (started lazily) ───────────────────────────────
_ws_feed: Optional[BinanceWebSocketFeed] = None
_ws_lock  = threading.Lock()

def get_ws_feed() -> BinanceWebSocketFeed:
    """Return the global WebSocket feed, starting it if not yet running."""
    global _ws_feed
    with _ws_lock:
        if _ws_feed is None:
            _ws_feed = BinanceWebSocketFeed()
            _ws_feed.start()
            time.sleep(0.5)   # Brief settle time
    return _ws_feed

def get_realtime_price(symbol: str) -> Optional[float]:
    """
    Get real-time price from Binance WebSocket cache (<50ms latency).
    Falls back to REST if WebSocket not fresh.
    """
    feed = get_ws_feed()
    if feed.is_fresh(symbol, max_age_sec=5.0):
        price = feed.get_price(symbol)
        if price and price > 0:
            return price
    # Fallback to REST
    return get_coin_price(f"{symbol.lower()}")


# ─── Binance REST — Funding Rates & Order Book (free, public endpoints) ───────

def get_funding_rates() -> dict[str, dict]:
    """
    Fetch current and predicted funding rates for all USDT-M perpetual contracts.
    Returns: { "BTCUSDT": {"rate": 0.0001, "next_funding_time": ...}, ... }
    Free endpoint — no API key required.
    """
    key = "funding_rates"
    cached = _cached(key, ttl=60)
    if cached is not None:
        return cached

    data = _get("https://fapi.binance.com/fapi/v1/premiumIndex")
    if not data:
        return _store(key, {})

    result = {}
    for item in data:
        sym  = item.get("symbol", "")
        rate = float(item.get("lastFundingRate", 0))
        nft  = item.get("nextFundingTime", 0)
        if sym.endswith("USDT") and rate != 0:
            result[sym] = {
                "rate": rate,
                "rate_pct": round(rate * 100, 6),
                "rate_daily_pct": round(rate * 3 * 100, 4),  # 3 funding periods/day
                "next_funding_time": nft,
                "annualized_pct": round(rate * 3 * 365 * 100, 1),
            }

    return _store(key, result)

def get_order_book_depth(symbol: str, limit: int = 20) -> dict:
    """
    Fetch L2 order book for a symbol from Binance.
    Returns bids/asks lists and spread metrics.
    Free public endpoint.
    """
    key = f"orderbook_{symbol}_{limit}"
    cached = _cached(key, ttl=5)   # Very short cache — order book changes fast
    if cached is not None:
        return cached

    data = _get(
        "https://api.binance.com/api/v3/depth",
        params={"symbol": symbol, "limit": limit},
        timeout=3,
    )
    if not data:
        return _store(key, {})

    bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in data.get("asks", [])]

    if not bids or not asks:
        return _store(key, {})

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    spread   = best_ask - best_bid
    spread_pct = spread / best_bid * 100

    total_bid_vol = sum(q for _, q in bids)
    total_ask_vol = sum(q for _, q in asks)
    imbalance = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)

    result = {
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": round(spread, 6),
        "spread_pct": round(spread_pct, 4),
        "bid_volume": round(total_bid_vol, 4),
        "ask_volume": round(total_ask_vol, 4),
        "imbalance": round(imbalance, 4),   # +1 = all bids, -1 = all asks
    }
    return _store(key, result)

def get_open_interest(symbol: str) -> dict:
    """
    Fetch open interest from Binance Futures (free public endpoint).
    High OI + rising price = strong trend. High OI + falling price = potential squeeze.
    """
    key = f"oi_{symbol}"
    cached = _cached(key, ttl=30)
    if cached is not None:
        return cached

    data = _get(
        "https://fapi.binance.com/fapi/v1/openInterest",
        params={"symbol": symbol},
        timeout=5,
    )
    if not data:
        return _store(key, {})

    result = {
        "symbol": data.get("symbol"),
        "open_interest": float(data.get("openInterest", 0)),
        "time": data.get("time"),
    }
    return _store(key, result)

# ─── CoinGecko ────────────────────────────────────────────────────────────────

def get_top_coins(n: int = config.CRYPTO_TOP_N) -> list[dict]:
    """Fetch top N coins by market cap from CoinGecko."""
    key = f"top_coins_{n}"
    cached = _cached(key, ttl=300)
    if cached is not None:
        return cached

    data = _get(
        f"{config.COINGECKO_BASE}/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": min(n, 250),
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "1h,24h,7d",
        },
    )
    result = data if data else []
    return _store(key, result)


def get_coin_ohlcv(coin_id: str, days: int = 30, interval: str = "hourly") -> pd.DataFrame:
    """
    Fetch OHLCV data for a coin from CoinGecko.
    Returns DataFrame with columns: timestamp, open, high, low, close, volume.
    """
    key = f"ohlcv_{coin_id}_{days}_{interval}"
    cached = _cached(key, ttl=120)
    if cached is not None:
        return cached

    data = _get(
        f"{config.COINGECKO_BASE}/coins/{coin_id}/ohlc",
        params={"vs_currency": "usd", "days": days},
    )
    if not data:
        return _store(key, pd.DataFrame())

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    # CoinGecko OHLC doesn't include volume — fetch it separately
    # NOTE: `interval` param requires a paid CoinGecko plan; omit for free tier.
    # Auto-granularity: 1-2 days → 5min, 3-89 days → hourly, 90+ → daily
    vol_data = _get(
        f"{config.COINGECKO_BASE}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": days},
    )
    if vol_data and "total_volumes" in vol_data:
        vdf = pd.DataFrame(vol_data["total_volumes"], columns=["timestamp", "volume"])
        vdf["timestamp"] = pd.to_datetime(vdf["timestamp"], unit="ms", utc=True)
        # Resample volumes to match OHLC frequency
        vdf = vdf.set_index("timestamp").resample("1h").last().reset_index()
        df = pd.merge_asof(df.sort_values("timestamp"), vdf.sort_values("timestamp"),
                           on="timestamp", direction="nearest")
    else:
        df["volume"] = 0.0

    return _store(key, df)


def get_coin_price(coin_id: str) -> Optional[float]:
    """Get current USD price of a coin."""
    key = f"price_{coin_id}"
    cached = _cached(key, ttl=30)
    if cached is not None:
        return cached

    data = _get(
        f"{config.COINGECKO_BASE}/simple/price",
        params={"ids": coin_id, "vs_currencies": "usd", "include_24hr_vol": True},
    )
    if data and coin_id in data:
        price = data[coin_id].get("usd")
        return _store(key, price)
    return None


# ─── CryptoCompare (Hourly OHLCV — More reliable for analysis) ────────────────

def get_crypto_ohlcv_cc(symbol: str, limit: int = 100, currency: str = "USD") -> pd.DataFrame:
    """
    Fetch hourly OHLCV data from CryptoCompare.
    `symbol` should be e.g. 'BTC', 'ETH'.
    """
    key = f"cc_ohlcv_{symbol}_{limit}"
    cached = _cached(key, ttl=120)
    if cached is not None:
        return cached

    data = _get(
        f"{config.CRYPTOCOMPARE_BASE}/v2/histohour",
        params={"fsym": symbol, "tsym": currency, "limit": limit},
    )
    if not data or data.get("Response") != "Success":
        logger.debug("CC API error for %s: %s", symbol, data.get("Message", "unknown") if data else "no data")
        return _store(key, pd.DataFrame())

    rows = data.get("Data", {}).get("Data", [])
    if not rows:
        return _store(key, pd.DataFrame())

    try:
        df = pd.DataFrame(rows)
        if "time" not in df.columns or "close" not in df.columns:
            return _store(key, pd.DataFrame())
        df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
        # volumefrom is the base currency volume; volumeto is quote (USD)
        vol_col = "volumefrom" if "volumefrom" in df.columns else "volumeto" if "volumeto" in df.columns else None
        if vol_col:
            df = df.rename(columns={vol_col: "volume"})
        else:
            df["volume"] = 0.0
        needed = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[needed]
        df = df[df["close"] > 0].sort_values("timestamp").reset_index(drop=True)
    except Exception as e:
        logger.debug("CC OHLCV parse error for %s: %s", symbol, e)
        return _store(key, pd.DataFrame())
    return _store(key, df)


# ─── CoinCap (Real-time prices) ───────────────────────────────────────────────

def get_coincap_assets(limit: int = 50) -> list[dict]:
    """Fetch top assets from CoinCap with real-time prices."""
    key = f"coincap_assets_{limit}"
    cached = _cached(key, ttl=60)
    if cached is not None:
        return cached

    data = _get(f"{config.COINCAP_BASE}/assets", params={"limit": limit})
    result = data.get("data", []) if data else []
    return _store(key, result)


def get_coincap_history(asset_id: str, interval: str = "h1", limit: int = 100) -> pd.DataFrame:
    """Fetch price history from CoinCap."""
    key = f"coincap_hist_{asset_id}_{interval}"
    cached = _cached(key, ttl=120)
    if cached is not None:
        return cached

    data = _get(
        f"{config.COINCAP_BASE}/assets/{asset_id}/history",
        params={"interval": interval},
    )
    if not data or not data.get("data"):
        return _store(key, pd.DataFrame())

    df = pd.DataFrame(data["data"])
    df["close"] = df["priceUsd"].astype(float)
    df["timestamp"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df[["timestamp", "close"]].sort_values("timestamp").tail(limit).reset_index(drop=True)
    return _store(key, df)


# ─── Forex ────────────────────────────────────────────────────────────────────

# Stooq pair codes (lowercase, no slash)
_STOOQ_CODES = {
    "EUR/USD": "eurusd", "GBP/USD": "gbpusd", "USD/JPY": "usdjpy",
    "AUD/USD": "audusd", "USD/CAD": "usdcad", "NZD/USD": "nzdusd",
    "EUR/GBP": "eurgbp", "EUR/JPY": "eurjpy", "GBP/JPY": "gbpjpy",
}


def get_forex_rates(base: str = "USD") -> dict:
    """Fetch current forex rates via ExchangeRate-API (free, no key)."""
    key = f"forex_{base}"
    cached = _cached(key, ttl=300)
    if cached is not None:
        return cached

    data = _get(f"{config.EXCHANGERATE_BASE}/latest/{base}")
    if data and data.get("result") == "success":
        rates = data.get("rates", {})
        return _store(key, rates)
    return {}


def get_forex_ohlcv_stooq(pair: str) -> pd.DataFrame:
    """
    Fetch daily forex OHLCV from Stooq (free, no API key, reliable data).
    Used for 4h-equivalent trend analysis (daily bars for direction; Stooq's
    hourly endpoint is intermittently unavailable on their servers).
    Returns DataFrame with timestamp, open, high, low, close columns.
    Cache: 4 hours (daily bars don't change intra-day).
    """
    code = _STOOQ_CODES.get(pair.upper())
    if not code:
        return pd.DataFrame()

    key = f"stooq_forex_{code}_d"
    cached = _cached(key, ttl=14400)  # 4 hours
    if cached is not None:
        return cached

    try:
        url = f"https://stooq.com/q/d/l/?s={code}&i=d"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "ai-trader/3.0"})
        if not resp.ok or "No data" in resp.text:
            return _store(key, pd.DataFrame())

        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        if df.empty or "Date" not in df.columns:
            return _store(key, pd.DataFrame())

        # Stooq returns: Date, Time, Open, High, Low, Close, Volume
        if "Time" in df.columns:
            df["timestamp"] = pd.to_datetime(
                df["Date"].astype(str) + " " + df["Time"].astype(str), utc=True,
                errors="coerce")
        else:
            df["timestamp"] = pd.to_datetime(df["Date"], utc=True, errors="coerce")

        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume"
        })
        cols = [c for c in ["timestamp", "open", "high", "low", "close"] if c in df.columns]
        df = df[cols].dropna().sort_values("timestamp").reset_index(drop=True)
        if "volume" not in df.columns:
            df["volume"] = 0.0
        return _store(key, df)
    except Exception as e:
        logger.debug("Stooq forex fetch failed for %s: %s", pair, e)
        return _store(key, pd.DataFrame())


def get_forex_ohlcv_av(pair: str, interval: str = "60min") -> pd.DataFrame:
    """
    Fetch forex OHLCV from Alpha Vantage FX_INTRADAY (requires ALPHAVANTAGE_KEY).
    Returns DataFrame with timestamp, open, high, low, close columns.
    Cache: 55 minutes.
    """
    if not config.ALPHAVANTAGE_KEY:
        return pd.DataFrame()

    parts = pair.replace("-", "/").split("/")
    if len(parts) != 2:
        return pd.DataFrame()
    base_sym, quote_sym = parts[0].strip(), parts[1].strip()

    key = f"av_forex_{base_sym}{quote_sym}_{interval}"
    cached = _cached(key, ttl=3300)
    if cached is not None:
        return cached

    data = _get(
        "https://www.alphavantage.co/query",
        params={
            "function": "FX_INTRADAY",
            "from_symbol": base_sym,
            "to_symbol": quote_sym,
            "interval": interval,
            "outputsize": "compact",
            "apikey": config.ALPHAVANTAGE_KEY,
        },
        timeout=15,
    )
    if not data:
        return _store(key, pd.DataFrame())

    ts_key = f"Time Series FX ({interval})"
    series = data.get(ts_key, {})
    if not series:
        logger.debug("Alpha Vantage forex: no data for %s (%s)", pair,
                     data.get("Note", data.get("Information", "unknown error")))
        return _store(key, pd.DataFrame())

    rows = []
    for ts_str, vals in series.items():
        rows.append({
            "timestamp": pd.to_datetime(ts_str, utc=True),
            "open":  float(vals.get("1. open",  0)),
            "high":  float(vals.get("2. high",  0)),
            "low":   float(vals.get("3. low",   0)),
            "close": float(vals.get("4. close", 0)),
            "volume": 0.0,
        })
    if not rows:
        return _store(key, pd.DataFrame())

    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return _store(key, df)


def get_forex_ohlcv_4h(pair: str) -> pd.DataFrame:
    """
    Fetch 4h-equivalent forex data for multi-timeframe trend confirmation.
    Uses Alpha Vantage 240min if key available, otherwise Stooq daily bars
    (daily is an adequate trend proxy when intraday 4h isn't available).
    """
    # Try Alpha Vantage 240min first
    if config.ALPHAVANTAGE_KEY:
        df = get_forex_ohlcv_av(pair, interval="240min")
        if not df.empty:
            return df

    # Stooq daily bars — sufficient for trend direction (EMA/ADX)
    return get_forex_ohlcv_stooq(pair)


def get_forex_ohlcv(pair: str, limit: int = 200) -> pd.DataFrame:
    """
    Fetch forex OHLCV via a multi-source fallback chain:
      1. Alpha Vantage FX_INTRADAY (best quality, requires API key)
      2. Stooq (free, reliable, no key)
      3. CryptoCompare (last resort — unreliable for forex)
    Returns up to `limit` hourly candles.
    """
    # 1. Alpha Vantage
    if config.ALPHAVANTAGE_KEY:
        df = get_forex_ohlcv_av(pair)
        if not df.empty:
            return df.tail(limit)

    # 2. Stooq
    df = get_forex_ohlcv_stooq(pair)
    if not df.empty:
        return df.tail(limit)

    # 3. CryptoCompare fallback (unreliable but better than nothing)
    parts = pair.replace("-", "/").split("/")
    if len(parts) != 2:
        return pd.DataFrame()
    fsym, tsym = parts[0].strip(), parts[1].strip()
    return get_crypto_ohlcv_cc(fsym, limit=limit, currency=tsym)


# ─── Stocks via yfinance ──────────────────────────────────────────────────────

def get_stock_ohlcv(symbol: str, period: str = "60d", interval: str = "1h") -> pd.DataFrame:
    """
    Fetch stock/ETF OHLCV using yfinance (unofficial Yahoo Finance).
    Falls back gracefully if yfinance is not installed.
    Returns empty DataFrame on weekends / outside market hours so signals
    don't trade on stale Friday-close data.
    """
    # US equity markets are closed on weekends — return nothing to prevent
    # the bot from acting on 2-day-old OHLCV as if it were live data.
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()   # 0=Mon … 6=Sun
    if weekday >= 5:              # Saturday or Sunday
        logger.debug("Stock market closed (weekend), skipping %s", symbol)
        return pd.DataFrame()

    key = f"stock_{symbol}_{period}_{interval}"
    cached = _cached(key, ttl=300)
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return _store(key, pd.DataFrame())
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df.index.name = "timestamp"
        df = df[["open", "high", "low", "close", "volume"]].reset_index()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Staleness guard: if the most recent candle is > 2 days old, discard
        if not df.empty:
            last_ts = df["timestamp"].iloc[-1]
            last_dt = last_ts.to_pydatetime()
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_hours = (now_utc - last_dt).total_seconds() / 3600
            if age_hours > 48:
                logger.debug("Stock data for %s is %.0fh old — discarding", symbol, age_hours)
                return _store(key, pd.DataFrame())

        return _store(key, df)
    except ImportError:
        logger.info("yfinance not installed, skipping stock data for %s", symbol)
        return _store(key, pd.DataFrame())
    except Exception as e:
        logger.warning("yfinance error for %s: %s", symbol, e)
        return _store(key, pd.DataFrame())


def get_stock_price(symbol: str) -> Optional[float]:
    """Get latest price for a stock/ETF."""
    df = get_stock_ohlcv(symbol, period="5d", interval="1h")
    if not df.empty:
        return float(df["close"].iloc[-1])
    return None


# ─── Messari (Crypto Fundamentals) ───────────────────────────────────────────

def get_messari_metrics(asset: str) -> dict:
    """Fetch fundamental metrics for a crypto asset from Messari."""
    key = f"messari_{asset}"
    cached = _cached(key, ttl=600)
    if cached is not None:
        return cached

    data = _get(f"{config.MESSARI_BASE}/assets/{asset}/metrics")
    if data and "data" in data:
        return _store(key, data["data"])
    return {}


# ─── Market Overview ──────────────────────────────────────────────────────────

def get_market_snapshot() -> dict:
    """
    Returns a snapshot of current market conditions:
    - Top crypto movers
    - BTC dominance
    - Market sentiment proxy
    """
    key = "market_snapshot"
    cached = _cached(key, ttl=120)
    if cached is not None:
        return cached

    coins = get_top_coins(100)
    if not coins:
        return {}

    gainers = sorted(coins, key=lambda c: c.get("price_change_percentage_24h") or 0, reverse=True)[:10]
    losers  = sorted(coins, key=lambda c: c.get("price_change_percentage_24h") or 0)[:10]

    total_mcap = sum(c.get("market_cap") or 0 for c in coins)
    btc_mcap   = next((c.get("market_cap") or 0 for c in coins if c["id"] == "bitcoin"), 0)
    btc_dom    = (btc_mcap / total_mcap * 100) if total_mcap > 0 else 0

    avg_change = sum(c.get("price_change_percentage_24h") or 0 for c in coins) / len(coins)

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "btc_dominance": round(btc_dom, 2),
        "avg_24h_change": round(avg_change, 2),
        "top_gainers": [{"id": c["id"], "symbol": c["symbol"], "change_24h": c.get("price_change_percentage_24h")} for c in gainers],
        "top_losers":  [{"id": c["id"], "symbol": c["symbol"], "change_24h": c.get("price_change_percentage_24h")} for c in losers],
        "market_sentiment": "bullish" if avg_change > 1 else "bearish" if avg_change < -1 else "neutral",
    }
    return _store(key, snapshot)
