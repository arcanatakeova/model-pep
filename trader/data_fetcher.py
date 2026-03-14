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
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import pandas as pd

import config

logger = logging.getLogger(__name__)

# ─── Simple in-memory cache ────────────────────────────────────────────────────
_cache: dict = {}

def _cached(key: str, ttl: int = config.DATA_CACHE_TTL):
    if key in _cache:
        value, ts = _cache[key]
        if time.time() - ts < ttl:
            return value
    return None

def _store(key: str, value):
    _cache[key] = (value, time.time())
    return value

def _get(url: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
    """HTTP GET with basic error handling and rate-limit backoff."""
    for attempt in range(3):
        try:
            headers = {"Accept": "application/json", "User-Agent": "ai-trader/2.0"}
            if config.COINGECKO_API_KEY and "coingecko" in url:
                headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = 2 + attempt * 2
                logger.debug("Rate limited by %s, waiting %ds", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.debug("Request failed (%s): %s", url, e)
            if attempt < 2:
                time.sleep(1)
    return None

# ─── Binance WebSocket Real-Time Price Feed ────────────────────────────────────

class BinanceWebSocketFeed:
    """
    Maintains a persistent WebSocket connection to Binance for real-time
    trade prices on BTC, ETH, and SOL. Updates an in-memory price cache
    with <50ms latency — orders of magnitude faster than REST polling.

    Usage:
        ws = BinanceWebSocketFeed()
        ws.start()
        price = ws.get_price("BTC")   # instant, no HTTP call
    """

    # Binance combined stream endpoint
    _WS_URL = "wss://stream.binance.com:9443/stream"
    _SYMBOLS = {
        "BTC": "btcusdt",
        "ETH": "ethusdt",
        "SOL": "solusdt",
        "BNB": "bnbusdt",
    }

    def __init__(self):
        self._prices:    dict[str, float] = {}   # "BTC" → price
        self._volumes:   dict[str, float] = {}   # "BTC" → 24h quote vol
        self._changes:   dict[str, float] = {}   # "BTC" → 24h % change
        self._last_tick: dict[str, float] = {}   # "BTC" → epoch of last tick
        self._lock       = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running    = False
        self._connected  = False

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

    def get_price(self, symbol: str) -> Optional[float]:
        """Return latest WebSocket price, or None if not yet received."""
        with self._lock:
            return self._prices.get(symbol.upper())

    def get_24h_change(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._changes.get(symbol.upper())

    def get_volume(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._volumes.get(symbol.upper())

    def is_fresh(self, symbol: str, max_age_sec: float = 10.0) -> bool:
        """Return True if the last tick for this symbol is within max_age_sec."""
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
        """Reconnecting WebSocket loop."""
        backoff = 1
        while self._running:
            try:
                self._connect_and_listen()
                backoff = 1   # Reset on clean disconnect
            except Exception as e:
                self._connected = False
                logger.warning("BinanceWS disconnected: %s — reconnecting in %ds", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _connect_and_listen(self):
        """Open WS, read messages until disconnected."""
        host = "stream.binance.com"
        path = f"/stream?streams={self._stream_names()}"
        ctx  = ssl.create_default_context()

        sock = socket.create_connection((host, 9443), timeout=10)
        sock = ctx.wrap_socket(sock, server_hostname=host)

        # Send HTTP upgrade request
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(handshake.encode())

        # Read HTTP response (skip headers)
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += sock.recv(4096)
        if b"101 Switching Protocols" not in resp:
            raise ConnectionError("WS handshake failed")

        self._connected = True
        logger.info("BinanceWS connected")

        sock.settimeout(30)   # 30s timeout per recv — server sends pings
        buf = b""

        while self._running:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                # Send a WS ping frame to keep alive
                sock.sendall(b"\x89\x00")
                continue
            if not chunk:
                raise ConnectionError("WS stream closed by server")

            buf += chunk
            # Parse all complete WebSocket frames in buffer
            while True:
                frame, buf = self._parse_frame(buf)
                if frame is None:
                    break
                self._handle_message(frame)

        sock.close()
        self._connected = False

    @staticmethod
    def _parse_frame(buf: bytes) -> tuple[Optional[str], bytes]:
        """
        Minimal WebSocket frame parser (text frames only, no masking).
        Returns (payload_str, remaining_buf) or (None, buf) if incomplete.
        """
        if len(buf) < 2:
            return None, buf

        opcode = buf[0] & 0x0F
        masked  = (buf[1] & 0x80) != 0
        length  = buf[1] & 0x7F

        offset = 2
        if length == 126:
            if len(buf) < 4:
                return None, buf
            length = int.from_bytes(buf[2:4], "big")
            offset = 4
        elif length == 127:
            if len(buf) < 10:
                return None, buf
            length = int.from_bytes(buf[2:10], "big")
            offset = 10

        mask_key = b""
        if masked:
            if len(buf) < offset + 4:
                return None, buf
            mask_key = buf[offset:offset + 4]
            offset += 4

        if len(buf) < offset + length:
            return None, buf   # Not enough data yet

        payload = bytearray(buf[offset:offset + length])
        if masked:
            for i in range(length):
                payload[i] ^= mask_key[i % 4]

        remaining = buf[offset + length:]

        if opcode == 0x8:   # Close
            raise ConnectionError("Server sent close frame")
        if opcode not in (0x1, 0x0):  # Not text or continuation
            return None, remaining

        try:
            return payload.decode("utf-8"), remaining
        except UnicodeDecodeError:
            return None, remaining

    def _handle_message(self, text: str):
        """Parse miniTicker message and update price cache."""
        try:
            outer = json.loads(text)
            data  = outer.get("data", outer)  # Combined stream wraps in {"data": {...}}
            if data.get("e") != "24hrMiniTicker":
                return

            raw_sym = data.get("s", "")  # e.g. "BTCUSDT"
            close   = float(data.get("c", 0))
            vol     = float(data.get("q", 0))  # Quote asset volume 24h
            o       = float(data.get("o", 0))  # Open 24h

            # Map back to our short symbol
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

# ─── Simple in-memory cache ────────────────────────────────────────────────────
_cache: dict = {}

def _cached(key: str, ttl: int = config.DATA_CACHE_TTL):
    """Return cached value if fresh, else None."""
    if key in _cache:
        value, ts = _cache[key]
        if time.time() - ts < ttl:
            return value
    return None

def _store(key: str, value):
    _cache[key] = (value, time.time())
    return value


def _get(url: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
    """HTTP GET with basic error handling and rate-limit backoff."""
    for attempt in range(3):
        try:
            headers = {"Accept": "application/json", "User-Agent": "ai-trader/1.0"}
            if config.COINGECKO_API_KEY and "coingecko" in url:
                headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = 2 + attempt * 2   # 2s, 4s, 6s — fast backoff for 60s cycles
                logger.debug("Rate limited by %s, waiting %ds", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.debug("Request failed (%s): %s", url, e)
            if attempt < 2:
                time.sleep(1)
    return None


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
        return _store(key, pd.DataFrame())

    rows = data["Data"]["Data"]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"volumefrom": "volume"})[
        ["timestamp", "open", "high", "low", "close", "volume"]
    ]
    df = df[df["close"] > 0].sort_values("timestamp").reset_index(drop=True)
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


def get_forex_ohlcv(pair: str, limit: int = 100) -> pd.DataFrame:
    """
    Build synthetic OHLCV for forex from CryptoCompare (supports forex pairs).
    pair: 'EUR/USD' → fsym='EUR', tsym='USD'
    """
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
    """
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
        return _store(key, df.sort_values("timestamp").reset_index(drop=True))
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
