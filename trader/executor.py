"""
Trade Executor — Bridges signals to actual (paper or live) trade execution.

Paper mode: simulates fills at market price with configurable slippage.
Live mode: routes to exchange APIs (Binance, Coinbase Pro) via ccxt.
"""
import logging
from typing import Optional
from datetime import datetime, timezone

import config
from portfolio import Portfolio
from risk_manager import RiskManager
from strategies.ensemble import TradeSignal
import data_fetcher as df_mod

logger = logging.getLogger(__name__)

SLIPPAGE = 0.001    # 0.1% simulated slippage for paper trading
COMMISSION = 0.001  # 0.1% commission per trade (maker fee)

# ccxt symbol mapping: our asset IDs → exchange trading pairs
_SYMBOL_MAP = {
    "bitcoin": "BTC/USD", "ethereum": "ETH/USD", "solana": "SOL/USD",
    "cardano": "ADA/USD", "polkadot": "DOT/USD", "chainlink": "LINK/USD",
    "avalanche-2": "AVAX/USD", "uniswap": "UNI/USD", "aave": "AAVE/USD",
    "bnb": "BNB/USD", "binancecoin": "BNB/USD", "polygon": "MATIC/USD",
    "matic-network": "MATIC/USD", "arbitrum": "ARB/USD", "optimism": "OP/USD",
    "dogecoin": "DOGE/USD", "ripple": "XRP/USD", "litecoin": "LTC/USD",
    "stellar": "XLM/USD", "cosmos": "ATOM/USD", "near-protocol": "NEAR/USD",
}


class TradeExecutor:
    """
    Executes trade signals:
    - Paper trading (default, safe simulation)
    - Live trading via ccxt (requires API keys + explicit enable)
    """

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager):
        self.portfolio = portfolio
        self.risk = risk_manager
        self._exchange = None   # ccxt exchange instance (live only)

        if not config.PAPER_TRADING:
            self._init_live_exchange()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def process_signal(self, signal: TradeSignal) -> Optional[dict]:
        """
        Process a trade signal end-to-end:
        1. Validate through risk manager
        2. Size position
        3. Execute (paper or live)
        4. Record in portfolio

        Returns the filled trade dict or None if skipped.
        """
        if signal.signal == "HOLD":
            return None

        asset_id = signal.asset_id
        price    = signal.current_price

        # ── Manage existing position ──────────────────────────────────────
        if self.portfolio.has_position(asset_id):
            return self._maybe_close(asset_id, price, signal)

        # ── Open new position ─────────────────────────────────────────────
        if signal.signal == "BUY":
            return self._open_long(signal)
        elif signal.signal == "SELL":
            # Only short in crypto and forex (not stocks in paper mode)
            if signal.market in ("crypto", "forex"):
                return self._open_short(signal)
            else:
                logger.debug("Short selling skipped for %s (stocks)", asset_id)
                return None

        return None

    def update_all_positions(self):
        """
        Refresh prices for all open positions and trigger stop/TP checks.
        Call this periodically (every scan cycle).
        """
        for asset_id in list(self.portfolio.open_positions.keys()):
            pos = self.portfolio.open_positions.get(asset_id)
            if not pos:
                continue

            current_price = self._get_current_price(asset_id, pos)
            if current_price is None:
                continue

            # Update trailing stop
            new_trail = self.risk.update_trailing_stop(pos, current_price)
            self.portfolio.open_positions[asset_id]["trailing_stop"] = new_trail

            # Update mark-to-market
            self.portfolio.update_position_price(asset_id, current_price)

            # Check exit conditions
            should_close, reason = self.risk.should_close_position(pos, current_price)
            if should_close:
                self._execute_close(asset_id, current_price, reason)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Execution
    # ─────────────────────────────────────────────────────────────────────────

    def _open_long(self, signal: TradeSignal) -> Optional[dict]:
        """Open a long position."""
        allowed, reason = self.risk.can_open_position(
            signal.asset_id, signal.score, signal.market)
        if not allowed:
            logger.debug("SKIP BUY %s: %s", signal.asset_id, reason)
            return None

        price   = signal.current_price
        stop_pct = abs(price - signal.stop_loss) / price if price > 0 else config.STOP_LOSS_PCT
        size_usd = self.risk.position_size_usd(signal.score, signal.conviction, stop_pct, price)

        if size_usd < 1.0:
            logger.debug("Position size too small ($%.2f) for %s", size_usd, signal.asset_id)
            return None

        fill_price = self._fill_price(price, "buy")
        qty        = self.risk.qty_from_usd(size_usd, fill_price)
        commission  = size_usd * COMMISSION
        actual_cost = size_usd + commission

        if actual_cost > self.portfolio.cash:
            logger.debug("Not enough cash: need $%.2f, have $%.2f", actual_cost, self.portfolio.cash)
            return None

        # Execute live order if exchange is connected
        if self._exchange and not config.PAPER_TRADING:
            order = self.execute_live_buy(signal.asset_id, qty, fill_price)
            if not order:
                logger.warning("Live buy failed for %s — skipping", signal.asset_id)
                return None
            # Use actual fill price from exchange if available
            if order.get("average"):
                fill_price = float(order["average"])

        self.portfolio.cash -= commission   # Deduct commission before position open
        pos = self.portfolio.open_position(
            signal.asset_id, "long", qty, fill_price,
            signal.stop_loss, signal.take_profit, signal.to_dict()
        )
        if pos:
            mode = "LIVE" if self._exchange else "PAPER"
            logger.info("[%s] BUY  %-12s $%.4f × %.6f = $%.2f | Score: %.2f | Conv: %.2f",
                        mode, signal.symbol, fill_price, qty, size_usd, signal.score, signal.conviction)
        return pos

    def _open_short(self, signal: TradeSignal) -> Optional[dict]:
        """Open a short position (paper only)."""
        allowed, reason = self.risk.can_open_position(
            signal.asset_id, signal.score, signal.market)
        if not allowed:
            logger.debug("SKIP SELL %s: %s", signal.asset_id, reason)
            return None

        price    = signal.current_price
        stop_pct = abs(signal.stop_loss - price) / price if price > 0 else config.STOP_LOSS_PCT
        size_usd = self.risk.position_size_usd(abs(signal.score), signal.conviction, stop_pct, price)

        if size_usd < 1.0:
            return None

        fill_price = self._fill_price(price, "sell")
        qty        = self.risk.qty_from_usd(size_usd, fill_price)

        pos = self.portfolio.open_position(
            signal.asset_id, "short", qty, fill_price,
            signal.stop_loss, signal.take_profit, signal.to_dict()
        )
        if pos:
            logger.info("EXECUTED SELL %-12s $%.4f × %.6f = $%.2f | Score: %.2f | Conv: %.2f",
                        signal.symbol, fill_price, qty, size_usd, signal.score, signal.conviction)
        return pos

    def _maybe_close(self, asset_id: str, current_price: float, signal: TradeSignal) -> Optional[dict]:
        """
        If we're already in a position and a new signal arrives,
        consider closing if the signal has flipped direction.
        """
        pos = self.portfolio.open_positions.get(asset_id)
        if not pos:
            return None

        side = pos["side"]
        # Flip: we're long but get a strong sell signal → close
        if side == "long" and signal.signal == "SELL" and signal.conviction > 0.65:
            return self._execute_close(asset_id, current_price, "Signal flip to SELL")
        # Flip: we're short but get a strong buy signal → close
        if side == "short" and signal.signal == "BUY" and signal.conviction > 0.65:
            return self._execute_close(asset_id, current_price, "Signal flip to BUY")

        return None

    def _execute_close(self, asset_id: str, price: float, reason: str) -> dict:
        """Execute a position close."""
        pos = self.portfolio.open_positions.get(asset_id, {})
        side = pos.get("side", "long")
        qty = pos.get("qty", 0)
        fill_price = self._fill_price(price, "sell" if side == "long" else "buy")

        # Execute live order if exchange is connected
        if self._exchange and not config.PAPER_TRADING and qty > 0:
            if side == "long":
                order = self.execute_live_sell(asset_id, qty, fill_price)
            else:
                order = self.execute_live_buy(asset_id, qty, fill_price)
            if order and order.get("average"):
                fill_price = float(order["average"])

        trade = self.portfolio.close_position(asset_id, fill_price, reason)
        if trade:
            pnl_str = f"+${trade['pnl_usd']:.2f}" if trade['pnl_usd'] >= 0 else f"-${abs(trade['pnl_usd']):.2f}"
            mode = "LIVE" if self._exchange else "PAPER"
            logger.info("[%s] CLOSED %-12s @ $%.4f | PnL: %s (%.2f%%) | %s",
                        mode, trade.get("symbol", asset_id), fill_price,
                        pnl_str, trade['pnl_pct'], reason)
        return trade

    # ─────────────────────────────────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────────────────────────────────

    def _fill_price(self, market_price: float, side: str) -> float:
        """Simulate slippage for paper trading, or return market price for live."""
        if config.PAPER_TRADING or self._exchange is None:
            if side == "buy":
                return market_price * (1 + SLIPPAGE)
            else:
                return market_price * (1 - SLIPPAGE)
        return market_price

    def _get_current_price(self, asset_id: str, pos: dict) -> Optional[float]:
        """Fetch current price for an open position."""
        market = pos.get("market", "crypto")
        try:
            # Try live exchange ticker first (most accurate)
            if self._exchange and market == "crypto":
                price = self._exchange_price(asset_id)
                if price:
                    return price

            if market == "crypto":
                symbol = pos.get("symbol", asset_id.upper().split("-")[0])
                df = df_mod.get_crypto_ohlcv_cc(symbol, limit=2)
                if not df.empty:
                    return float(df["close"].iloc[-1])
                return df_mod.get_coin_price(asset_id)
            elif market == "stocks":
                return df_mod.get_stock_price(asset_id)
            elif market == "forex":
                pair = asset_id
                parts = pair.replace("-", "/").split("/")
                if len(parts) == 2:
                    rates = df_mod.get_forex_rates(parts[1])
                    return rates.get(parts[0])
        except Exception as e:
            logger.debug("Price fetch error for %s: %s", asset_id, e)
        return None

    def _exchange_price(self, asset_id: str) -> Optional[float]:
        """Get price from connected exchange."""
        if not self._exchange:
            return None
        pair = _SYMBOL_MAP.get(asset_id.lower())
        if not pair:
            # Try constructing: SYMBOL/USD
            sym = asset_id.upper().split("-")[0]
            pair = f"{sym}/USD"
        try:
            ticker = self._exchange.fetch_ticker(pair)
            return float(ticker["last"]) if ticker and ticker.get("last") else None
        except Exception:
            # Try USDT pair as fallback
            try:
                pair_usdt = pair.replace("/USD", "/USDT")
                ticker = self._exchange.fetch_ticker(pair_usdt)
                return float(ticker["last"]) if ticker and ticker.get("last") else None
            except Exception:
                return None

    def execute_live_buy(self, asset_id: str, qty: float, price: float) -> Optional[dict]:
        """Place a live market buy order on the connected exchange."""
        if not self._exchange:
            return None
        pair = _SYMBOL_MAP.get(asset_id.lower(), f"{asset_id.upper().split('-')[0]}/USD")
        try:
            order = self._exchange.create_market_buy_order(pair, qty)
            logger.info("LIVE BUY  %s qty=%.6f | order=%s", pair, qty, order.get("id", "?"))
            return order
        except Exception as e:
            logger.error("Live buy failed %s: %s", pair, e)
            return None

    def execute_live_sell(self, asset_id: str, qty: float, price: float) -> Optional[dict]:
        """Place a live market sell order on the connected exchange."""
        if not self._exchange:
            return None
        pair = _SYMBOL_MAP.get(asset_id.lower(), f"{asset_id.upper().split('-')[0]}/USD")
        try:
            order = self._exchange.create_market_sell_order(pair, qty)
            logger.info("LIVE SELL %s qty=%.6f | order=%s", pair, qty, order.get("id", "?"))
            return order
        except Exception as e:
            logger.error("Live sell failed %s: %s", pair, e)
            return None

    def _init_live_exchange(self):
        """Initialize ccxt exchange — Coinbase primary, Binance fallback."""
        # ── Primary: Coinbase Advanced Trade ─────────────────────────────────
        if config.COINBASE_API_KEY and config.COINBASE_SECRET:
            try:
                import ccxt
                self._exchange = ccxt.coinbase({
                    "apiKey": config.COINBASE_API_KEY,
                    "secret": config.COINBASE_SECRET,
                    "enableRateLimit": True,
                })
                # Verify connection
                self._exchange.load_markets()
                logger.info("Live trading initialized: Coinbase Advanced Trade")
                return
            except ImportError:
                logger.error("ccxt not installed. Run: pip3 install ccxt")
                return
            except Exception as e:
                logger.warning("Coinbase init failed: %s — trying Binance", e)

        # ── Fallback: Binance ────────────────────────────────────────────────
        if config.BINANCE_API_KEY and config.BINANCE_SECRET:
            try:
                import ccxt
                self._exchange = ccxt.binance({
                    "apiKey": config.BINANCE_API_KEY,
                    "secret": config.BINANCE_SECRET,
                    "enableRateLimit": True,
                })
                self._exchange.load_markets()
                logger.info("Live trading initialized: Binance")
                return
            except ImportError:
                logger.error("ccxt not installed. Run: pip3 install ccxt")
            except Exception as e:
                logger.error("Binance init failed: %s", e)
            return

        logger.warning("Live trading enabled but no exchange API keys set — staying in paper mode")
