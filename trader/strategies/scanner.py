"""
Market Scanner — Scans all watched markets and produces ranked trade signals.
Covers: Crypto, Forex, Stocks/ETFs.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import data_fetcher as df_mod
import config
from .ensemble import EnsembleSignal, TradeSignal

logger = logging.getLogger(__name__)


class MarketScanner:
    """
    Scans all configured markets concurrently and returns
    a sorted list of TradeSignal objects.
    """

    def __init__(self):
        self.engine = EnsembleSignal()
        self._symbol_map = {}   # coin_id → symbol (BTC, ETH, ...)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def scan_all(self, max_workers: int = 8) -> list[TradeSignal]:
        """
        Run a full market scan across crypto, forex, and stocks.
        Returns signals sorted by absolute score (highest conviction first).
        """
        tasks = []

        # Build task list
        tasks += [("crypto", cid) for cid in self._get_crypto_ids()]
        tasks += [("forex",  pair) for pair in config.FOREX_PAIRS]
        tasks += [("stocks", sym)  for sym in config.STOCK_WATCHLIST]

        signals = []
        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            futures = {exe.submit(self._analyze_one, market, asset): (market, asset)
                       for market, asset in tasks}
            for fut in as_completed(futures):
                market, asset = futures[fut]
                try:
                    sig = fut.result(timeout=20)
                    if sig is not None:
                        signals.append(sig)
                except Exception as e:
                    logger.debug("Scanner error for %s %s: %s", market, asset, e)

        # Sort: actionable signals first, then by conviction
        signals.sort(key=lambda s: (s.signal == "HOLD", -abs(s.score), -s.conviction))
        logger.info("Scan complete: %d signals (%d BUY, %d SELL, %d HOLD)",
                    len(signals),
                    sum(1 for s in signals if s.signal == "BUY"),
                    sum(1 for s in signals if s.signal == "SELL"),
                    sum(1 for s in signals if s.signal == "HOLD"))
        return signals

    def scan_crypto(self) -> list[TradeSignal]:
        return [s for s in self.scan_all() if s.market == "crypto"]

    def scan_forex(self) -> list[TradeSignal]:
        return [s for s in self.scan_all() if s.market == "forex"]

    def scan_stocks(self) -> list[TradeSignal]:
        return [s for s in self.scan_all() if s.market == "stocks"]

    def get_best_signals(self, n: int = 5, signal_type: str = "BUY") -> list[TradeSignal]:
        """Return the top N signals of a given type from the latest scan."""
        all_signals = self.scan_all()
        filtered = [s for s in all_signals if s.signal == signal_type]
        return filtered[:n]

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_crypto_ids(self) -> list[str]:
        """Get crypto IDs to scan: watchlist + top N by market cap."""
        watchlist = list(config.CRYPTO_WATCHLIST)
        try:
            coins = df_mod.get_top_coins(config.CRYPTO_TOP_N)
            # Filter by minimum volume
            top_ids = [
                c["id"] for c in coins
                if (c.get("total_volume") or 0) >= config.CRYPTO_MIN_VOLUME_USD
            ]
            # Build symbol map
            for c in coins:
                self._symbol_map[c["id"]] = c.get("symbol", c["id"]).upper()
        except Exception as e:
            logger.warning("Failed to fetch top coins: %s", e)
            top_ids = []

        # Merge watchlist and top-N (deduplicated, watchlist first)
        seen = set()
        result = []
        for cid in watchlist + top_ids:
            if cid not in seen:
                seen.add(cid)
                result.append(cid)
        return result

    def _analyze_one(self, market: str, asset: str) -> Optional[TradeSignal]:
        """Fetch data and run ensemble analysis for a single asset."""
        try:
            if market == "crypto":
                return self._analyze_crypto(asset)
            elif market == "forex":
                return self._analyze_forex(asset)
            elif market == "stocks":
                return self._analyze_stock(asset)
        except Exception as e:
            logger.debug("Analysis error [%s %s]: %s", market, asset, e)
        return None

    def _analyze_crypto(self, coin_id: str) -> Optional[TradeSignal]:
        # Use CryptoCompare exclusively for OHLCV — fast, no rate limits
        symbol = self._symbol_map.get(coin_id, coin_id.upper().split("-")[0])
        # Handle multi-word CoinGecko IDs → CC symbol (e.g. "avalanche-2" → "AVAX")
        CC_OVERRIDES = {
            "avalanche-2": "AVAX", "matic-network": "MATIC", "binancecoin": "BNB",
            "shiba-inu": "SHIB", "wrapped-bitcoin": "WBTC", "staked-ether": "STETH",
        }
        symbol = CC_OVERRIDES.get(coin_id, symbol)
        df = df_mod.get_crypto_ohlcv_cc(symbol, limit=config.OHLCV_CANDLES)
        if df.empty or len(df) < 30:
            return None

        return self.engine.analyze(df, asset_id=coin_id, market="crypto", symbol=symbol)

    def _analyze_forex(self, pair: str) -> Optional[TradeSignal]:
        df = df_mod.get_forex_ohlcv(pair, limit=config.OHLCV_CANDLES)
        if df.empty or len(df) < 30:
            return None
        symbol = pair.replace("/", "")
        return self.engine.analyze(df, asset_id=pair, market="forex", symbol=symbol)

    def _analyze_stock(self, symbol: str) -> Optional[TradeSignal]:
        df = df_mod.get_stock_ohlcv(symbol, period="60d", interval="1h")
        if df.empty or len(df) < 30:
            return None
        return self.engine.analyze(df, asset_id=symbol, market="stocks", symbol=symbol)
