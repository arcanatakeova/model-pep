"""
Data Fetcher — Retrieves market data from free public APIs.

Sources:
  - CoinGecko  : crypto OHLCV, market cap, volume (free, no key)
  - CoinCap    : real-time crypto prices (free, no key)
  - CryptoCompare: crypto historical OHLCV (free, no key)
  - ExchangeRate API: forex rates (free, no key)
  - Yahoo Finance (yfinance): stocks & ETFs (unofficial, no key)
"""
from __future__ import annotations

import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

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
                wait = 2 ** attempt * 5
                logger.debug("Rate limited by %s, waiting %ds", url, wait)
                time.sleep(wait)
                continue
            if resp.status_code in (401, 403):
                logger.debug("Auth required for %s (free tier limit) — skipping", url)
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.debug("Request failed (%s): %s", url, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
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


# CoinGecko ID → CryptoCompare ticker symbol
_CG_TO_CC: dict = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
    "binancecoin": "BNB", "bnb": "BNB", "avalanche-2": "AVAX",
    "chainlink": "LINK", "polkadot": "DOT", "cardano": "ADA",
    "polygon": "MATIC", "matic-network": "MATIC", "arbitrum": "ARB",
    "optimism": "OP", "uniswap": "UNI", "aave": "AAVE",
    "maker": "MKR", "compound-governance-token": "COMP",
    "cosmos": "ATOM", "near-protocol": "NEAR", "fantom": "FTM",
    "algorand": "ALGO", "hedera-hashgraph": "HBAR", "dogecoin": "DOGE",
    "ripple": "XRP", "litecoin": "LTC", "stellar": "XLM",
    "tron": "TRX", "ethereum-classic": "ETC", "bitcoin-cash": "BCH",
    "internet-computer": "ICP", "filecoin": "FIL", "aptos": "APT",
    "sui": "SUI", "injective-protocol": "INJ", "render-token": "RNDR",
    "the-graph": "GRT", "decentraland": "MANA", "sandbox": "SAND",
    "axie-infinity": "AXS", "mantle": "MNT", "rain": "RAIN",
    "whitebit": "WBT", "ethena-usde": "USDE", "usds": "USDS",
    "usd-coin": "USDC", "tether": "USDT", "figure-heloc": "HELOC",
    "crypto-com-chain": "CRO",
}


def _cc_symbol(coin_id: str) -> str:
    """Map a CoinGecko coin ID to its CryptoCompare ticker symbol."""
    return _CG_TO_CC.get(coin_id.lower(), coin_id.upper().split("-")[0])


def get_coin_ohlcv(coin_id: str, days: int = 30, interval: str = "hourly") -> pd.DataFrame:
    """
    Fetch OHLCV data for a coin.
    Uses CryptoCompare as primary source (free, reliable, no key needed).
    Falls back to CoinGecko /ohlc if CryptoCompare returns nothing.
    """
    key = f"ohlcv_{coin_id}_{days}_{interval}"
    cached = _cached(key, ttl=120)
    if cached is not None:
        return cached

    # ── Primary: CryptoCompare (full OHLCV, no API key required) ─────────────
    cc_sym = _cc_symbol(coin_id)
    df = get_crypto_ohlcv_cc(cc_sym, limit=min(days * 24, 100))
    if not df.empty:
        return _store(key, df)

    # ── Fallback: CoinGecko /ohlc (no volume, no market_chart) ───────────────
    data = _get(
        f"{config.COINGECKO_BASE}/coins/{coin_id}/ohlc",
        params={"vs_currency": "usd", "days": days},
    )
    if not data:
        return _store(key, pd.DataFrame())

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["volume"] = 0.0   # /ohlc has no volume; market_chart requires paid key

    return _store(key, df)


def get_coin_price(coin_id: str) -> Optional[float]:
    """Get current USD price of a coin. Tries CoinCap first, CoinGecko fallback."""
    key = f"price_{coin_id}"
    cached = _cached(key, ttl=30)
    if cached is not None:
        return cached

    # Primary: CoinCap (free, no key, reliable)
    coincap_id = coin_id.lower().replace("_", "-")
    cap_data = _get(f"{config.COINCAP_BASE}/assets/{coincap_id}")
    if cap_data and "data" in cap_data:
        try:
            price = float(cap_data["data"]["priceUsd"])
            if price > 0:
                return _store(key, price)
        except (KeyError, ValueError, TypeError):
            pass

    # Fallback: CoinGecko
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
