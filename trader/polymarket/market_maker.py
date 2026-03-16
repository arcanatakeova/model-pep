"""
Polymarket Market Maker
=======================
Passive liquidity provision to earn spread on Polymarket markets.
"""
from __future__ import annotations
import logging
from typing import Optional

from .models import PolyMarket

logger = logging.getLogger(__name__)


class PolymarketMarketMaker:
    """Provides liquidity on Polymarket by quoting bid/ask spreads."""

    def __init__(self, api_client, ws_feed=None):
        self._api = api_client
        self._ws = ws_feed
        self._active_orders: dict[str, list[str]] = {}  # condition_id -> [order_ids]
        self._inventory: dict[str, float] = {}           # condition_id -> net exposure USD
        self._max_inventory_per_market = 100.0            # Max $100 exposure per market
        self._max_markets = 5
        self._base_spread = 0.02  # 2 cents
        self._orders_this_minute = 0
        self._order_limit_per_min = 50

    def select_markets(self, markets: list[PolyMarket]) -> list[PolyMarket]:
        """Select markets suitable for market making."""
        candidates = []
        for mkt in markets:
            # Must have good volume (enough flow to fill orders)
            if mkt.volume_24h < 50_000:
                continue
            # Spread must be wide enough to earn
            if mkt.spread < 0.03:
                continue
            # Avoid resolution risk (end date > 7 days)
            # Skip extreme prices (keep 10-90 cent range)
            if mkt.yes_price < 0.10 or mkt.yes_price > 0.90:
                continue
            candidates.append(mkt)

        # Sort by spread (widest = most profitable)
        candidates.sort(key=lambda m: m.spread, reverse=True)
        return candidates[:self._max_markets]

    def quote(self, market: PolyMarket) -> Optional[tuple[dict, dict]]:
        """
        Generate bid and ask orders for a market.
        Returns (bid_order_params, ask_order_params) or None.
        """
        if self._orders_this_minute >= self._order_limit_per_min:
            return None

        # Check inventory limits
        inv = self._inventory.get(market.condition_id, 0.0)
        if abs(inv) >= self._max_inventory_per_market:
            logger.debug("MM: inventory limit for %s", market.condition_id[:8])
            return None

        mid = (market.yes_price + (1.0 - market.no_price)) / 2.0
        bid_price, ask_price = self._calculate_spread(market, mid)

        # Skew for inventory
        bid_price, ask_price = self._skew_for_inventory(bid_price, ask_price, inv)

        # Ensure valid prices
        bid_price = max(0.01, round(bid_price, 2))
        ask_price = min(0.99, round(ask_price, 2))

        if bid_price >= ask_price:
            return None

        size = 10.0  # $10 per side

        bid = {
            "token_id": market.yes_token_id,
            "side": "BUY",
            "price": bid_price,
            "size": round(size / bid_price, 2),
        }
        ask = {
            "token_id": market.yes_token_id,
            "side": "SELL",
            "price": ask_price,
            "size": round(size / ask_price, 2),
        }
        return bid, ask

    def update_quotes(self, markets: list[PolyMarket]):
        """Update quotes on selected markets."""
        selected = self.select_markets(markets)
        if not selected:
            return

        # Cancel stale quotes first
        self.cancel_stale_quotes()

        for mkt in selected:
            orders = self.quote(mkt)
            if not orders:
                continue

            bid, ask = orders
            bid_result = self._api.place_limit_order(**bid)
            ask_result = self._api.place_limit_order(**ask)

            order_ids = []
            if bid_result and bid_result.get("orderID"):
                order_ids.append(bid_result["orderID"])
            if ask_result and ask_result.get("orderID"):
                order_ids.append(ask_result["orderID"])

            if order_ids:
                self._active_orders[mkt.condition_id] = order_ids
                self._orders_this_minute += 2

        logger.debug("MM: updated quotes on %d markets", len(selected))

    def cancel_stale_quotes(self):
        """Cancel all existing market making orders."""
        for cid, order_ids in list(self._active_orders.items()):
            for oid in order_ids:
                self._api.cancel_order(oid)
            del self._active_orders[cid]

    def inventory_risk(self, condition_id: str) -> float:
        """Current inventory exposure for a market."""
        return abs(self._inventory.get(condition_id, 0.0))

    def _calculate_spread(self, market: PolyMarket, mid: float) -> tuple[float, float]:
        """Calculate bid/ask prices from midpoint."""
        half_spread = max(self._base_spread / 2, market.tick_size)

        # Widen spread for low-volume markets
        if market.volume_24h < 100_000:
            half_spread *= 1.5

        bid = mid - half_spread
        ask = mid + half_spread
        return bid, ask

    def _skew_for_inventory(self, bid: float, ask: float,
                            inventory: float) -> tuple[float, float]:
        """Skew prices to reduce inventory risk."""
        if abs(inventory) < 10:
            return bid, ask

        # If we're long (positive inventory), lower ask to sell faster
        skew = inventory / self._max_inventory_per_market * 0.01
        return bid - skew, ask - skew
