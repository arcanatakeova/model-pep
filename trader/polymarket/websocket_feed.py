"""
Polymarket WebSocket Feed
=========================
Real-time price streaming via Polymarket's WebSocket API.
"""
from __future__ import annotations
import json
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class PolymarketWebSocket:
    """Real-time price and orderbook streaming from Polymarket."""

    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws"

    def __init__(self):
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._subscriptions: dict[str, set] = {}   # channel -> set of asset_ids
        self._prices: dict[str, float] = {}         # token_id -> last price
        self._orderbooks: dict[str, dict] = {}      # token_id -> {bids, asks}
        self._callbacks: list[Callable] = []
        self._lock = threading.Lock()
        self._reconnect_delay = 1.0

    def connect(self):
        """Start WebSocket connection in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Polymarket WebSocket connecting...")

    def disconnect(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        logger.info("Polymarket WebSocket disconnected")

    def subscribe_price(self, token_ids: list[str]):
        """Subscribe to price updates for given token IDs."""
        with self._lock:
            channel = "price_change"
            if channel not in self._subscriptions:
                self._subscriptions[channel] = set()
            self._subscriptions[channel].update(token_ids)

        if self._ws:
            self._send_subscribe(channel, token_ids)

    def subscribe_book(self, token_ids: list[str]):
        """Subscribe to orderbook updates."""
        with self._lock:
            channel = "book"
            if channel not in self._subscriptions:
                self._subscriptions[channel] = set()
            self._subscriptions[channel].update(token_ids)

        if self._ws:
            self._send_subscribe(channel, token_ids)

    def get_price(self, token_id: str) -> Optional[float]:
        """Get the last known price for a token."""
        with self._lock:
            return self._prices.get(token_id)

    def get_book(self, token_id: str) -> Optional[dict]:
        """Get the last known orderbook for a token."""
        with self._lock:
            return self._orderbooks.get(token_id)

    def on_price_update(self, callback: Callable):
        """Register a callback for price updates: callback(token_id, price)."""
        self._callbacks.append(callback)

    # ─── Internal ──────────────────────────────────────────────────────────

    def _run_loop(self):
        """Connection loop with reconnection logic."""
        while self._running:
            try:
                self._connect_ws()
            except Exception as e:
                logger.warning("Polymarket WS error: %s", e)

            if not self._running:
                break

            delay = min(self._reconnect_delay, 60.0)
            logger.info("Polymarket WS reconnecting in %.0fs...", delay)
            time.sleep(delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)

    def _connect_ws(self):
        """Establish WebSocket connection."""
        try:
            import websocket
        except ImportError:
            logger.warning("websocket-client not installed -- WS feed disabled")
            self._running = False
            return

        self._ws = websocket.WebSocketApp(
            self.WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    def _on_open(self, ws):
        """Re-subscribe to all channels on connect."""
        logger.info("Polymarket WS connected")
        self._reconnect_delay = 1.0
        with self._lock:
            for channel, asset_ids in self._subscriptions.items():
                if asset_ids:
                    self._send_subscribe(channel, list(asset_ids))

    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return

        msg_type = data.get("type", "")

        if msg_type in ("price_change", "last_trade_price"):
            asset_id = data.get("asset_id", "")
            price = data.get("price")
            if asset_id and price is not None:
                price = float(price)
                with self._lock:
                    self._prices[asset_id] = price
                for cb in self._callbacks:
                    try:
                        cb(asset_id, price)
                    except Exception:
                        pass

        elif msg_type == "book":
            asset_id = data.get("asset_id", "")
            if asset_id:
                with self._lock:
                    self._orderbooks[asset_id] = {
                        "bids": data.get("bids", []),
                        "asks": data.get("asks", []),
                    }

    def _on_error(self, ws, error):
        logger.debug("Polymarket WS error: %s", error)

    def _on_close(self, ws, code, reason):
        logger.debug("Polymarket WS closed: code=%s reason=%s", code, reason)

    def _send_subscribe(self, channel: str, asset_ids: list[str]):
        """Send subscription message to WebSocket."""
        if not self._ws:
            return
        try:
            msg = json.dumps({
                "type": "subscribe",
                "channel": channel,
                "assets_ids": asset_ids,
            })
            self._ws.send(msg)
        except Exception as e:
            logger.debug("WS subscribe failed: %s", e)
