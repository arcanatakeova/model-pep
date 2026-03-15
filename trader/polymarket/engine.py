"""
Polymarket Engine
=================
Top-level orchestrator for the Polymarket trading system.
Replaces PolymarketTrader as the single entry point.
"""
from __future__ import annotations
import logging
from typing import Optional

from .api_client import PolymarketAPIClient
from .websocket_feed import PolymarketWebSocket
from .news_sentiment import NewsSentimentAnalyzer
from .cross_platform import CrossPlatformAggregator
from .probability_engine import ProbabilityEngine
from .smart_money import SmartMoneyTracker
from .market_maker import PolymarketMarketMaker
from .position_manager import PolyPositionManager
from .strategies import PolymarketStrategies

logger = logging.getLogger(__name__)


class PolymarketEngine:
    """
    Orchestrator for the Polymarket prediction market trading system.
    Called by main.py._run_polymarket_scan().
    """

    def __init__(self, private_key: str = "", chain_id: int = 137):
        import config

        # Build the component graph
        self.api_client = PolymarketAPIClient(private_key, chain_id)
        self.ws_feed = PolymarketWebSocket()

        # Intelligence modules
        self.news = NewsSentimentAnalyzer() if getattr(config, "POLYMARKET_NEWS_ENABLED", True) else None
        self.cross_platform = (
            CrossPlatformAggregator()
            if getattr(config, "POLYMARKET_CROSS_PLATFORM_ENABLED", True)
            else None
        )
        self.probability = ProbabilityEngine(self.news, self.cross_platform)
        self.smart_money = (
            SmartMoneyTracker(self.api_client)
            if getattr(config, "POLYMARKET_SMART_MONEY_ENABLED", True)
            else None
        )

        # Position management
        self.positions = PolyPositionManager(self.api_client, self.ws_feed)

        # Strategies
        self.strategies = PolymarketStrategies(
            probability_engine=self.probability,
            news_analyzer=self.news,
            cross_platform=self.cross_platform,
            smart_money=self.smart_money,
            api_client=self.api_client,
        )

        # Market making (off by default)
        self.market_maker = PolymarketMarketMaker(self.api_client, self.ws_feed)

    def start(self):
        """Start real-time components (WebSocket, position loading)."""
        import config
        if getattr(config, "POLYMARKET_WS_ENABLED", True):
            self.ws_feed.connect()

        # Subscribe to price feeds for existing positions
        token_ids = [p.token_id for p in self.positions._positions.values()]
        if token_ids:
            self.ws_feed.subscribe_price(token_ids)

        logger.info("PolymarketEngine started")

    def stop(self):
        """Gracefully shut down all components."""
        self.ws_feed.disconnect()
        self.positions.save()

        # Cancel any active market maker orders
        import config
        if getattr(config, "POLYMARKET_MM_ENABLED", False):
            self.market_maker.cancel_stale_quotes()

        logger.info("PolymarketEngine stopped")

    def scan_and_trade(self, portfolio, risk_mgr, compounder) -> list[dict]:
        """
        Main entry point called by main.py._run_polymarket_scan().
        Returns list of trade results.
        """
        import config
        results = []

        # 1. Update existing position prices
        self.positions.update_prices()

        # 2. Check exits on existing positions
        for pos, reason in self.positions.check_exits():
            result = self.positions.close_position(pos.condition_id, reason)
            if result:
                results.append(result)

        # 3. Fetch active markets
        scan_limit = getattr(config, "POLYMARKET_SCAN_LIMIT", 100)
        min_volume = getattr(config, "POLYMARKET_MIN_VOLUME", 5000)
        markets = self.api_client.get_active_markets(limit=scan_limit, min_volume=min_volume)

        # 4. Run all strategies
        min_edge = getattr(config, "POLYMARKET_MIN_EDGE", 0.04)
        signals = self.strategies.scan_all(markets, min_edge=min_edge)

        # 5. Size and execute top signals
        budget = compounder.max_position_for_market("polymarket")
        exposure = self.positions.get_total_exposure()
        max_total = getattr(config, "POLYMARKET_MAX_TOTAL_EXPOSURE", 1000)
        max_trades = getattr(config, "POLYMARKET_MAX_TRADES_PER_CYCLE", 5)
        min_order = getattr(config, "POLYMARKET_MIN_ORDER_SIZE", 5.0)

        for sig in signals[:max_trades]:
            if exposure >= max_total:
                break

            size = risk_mgr.poly_position_size_usd(
                sig.score, sig.edge_pct, portfolio.cash, budget)
            size = min(size, max_total - exposure)
            size = min(size, getattr(config, "POLYMARKET_MAX_POSITION_USD", 200))

            if size >= min_order:
                token_id = (sig.market.yes_token_id
                            if sig.side == "YES"
                            else sig.market.no_token_id)
                order_result = self.api_client.place_limit_order(
                    token_id=token_id,
                    side="BUY",
                    price=sig.target_price,
                    size=round(size / sig.target_price, 2) if sig.target_price > 0 else 0,
                )
                if order_result:
                    self.positions.open_position(sig, size, order_result)
                    # Subscribe to price feed for new position
                    self.ws_feed.subscribe_price([token_id])
                    exposure += size
                    results.append({
                        "action": "open",
                        "side": sig.side,
                        "market": sig.market.question[:60],
                        "size_usdc": round(size, 2),
                        "edge_pct": round(sig.edge_pct * 100, 2),
                        "strategy": sig.strategy,
                        "order": order_result,
                    })

        # 6. Update market making quotes (if enabled)
        if getattr(config, "POLYMARKET_MM_ENABLED", False):
            try:
                self.market_maker.update_quotes(markets)
            except Exception as e:
                logger.debug("Market maker error: %s", e)

        return results

    def update_positions(self):
        """Price refresh + exit checks (can be called between scan cycles)."""
        self.positions.update_prices()
        exits = self.positions.check_exits()
        for pos, reason in exits:
            self.positions.close_position(pos.condition_id, reason)

    def get_status(self) -> dict:
        """Current engine status for monitoring."""
        summary = self.positions.get_portfolio_summary()
        return {
            "polymarket": summary,
            "ws_connected": self.ws_feed._running,
        }
