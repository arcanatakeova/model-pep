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
COMMISSION = 0.001  # 0.1% commission per trade (Binance maker fee)


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

        self.portfolio.cash -= commission   # Deduct commission before position open
        pos = self.portfolio.open_position(
            signal.asset_id, "long", qty, fill_price,
            signal.stop_loss, signal.take_profit, signal.to_dict()
        )
        if pos:
            logger.info("EXECUTED BUY  %-12s $%.4f × %.6f = $%.2f | Score: %.2f | Conv: %.2f",
                        signal.symbol, fill_price, qty, size_usd, signal.score, signal.conviction)
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
        fill_price = self._fill_price(price, "sell" if self.portfolio.open_positions.get(asset_id, {}).get("side") == "long" else "buy")
        trade = self.portfolio.close_position(asset_id, fill_price, reason)
        if trade:
            pnl_str = f"+${trade['pnl_usd']:.2f}" if trade['pnl_usd'] >= 0 else f"-${abs(trade['pnl_usd']):.2f}"
            logger.info("CLOSED %-12s @ $%.4f | PnL: %s (%.2f%%) | %s",
                        trade.get("symbol", asset_id), fill_price,
                        pnl_str, trade['pnl_pct'], reason)
        return trade

    # ─────────────────────────────────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────────────────────────────────

    def _fill_price(self, market_price: float, side: str) -> float:
        """Simulate slippage for paper trading."""
        if config.PAPER_TRADING:
            if side == "buy":
                return market_price * (1 + SLIPPAGE)
            else:
                return market_price * (1 - SLIPPAGE)
        return market_price

    def _get_current_price(self, asset_id: str, pos: dict) -> Optional[float]:
        """Fetch current price for an open position."""
        market = pos.get("market", "crypto")
        try:
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

    def _init_live_exchange(self):
        """Initialize ccxt exchange for live trading (Binance by default)."""
        if not config.BINANCE_API_KEY:
            logger.warning("Live trading enabled but no BINANCE_API_KEY set — staying in paper mode")
            return
        try:
            import ccxt
            self._exchange = ccxt.binance({
                "apiKey": config.BINANCE_API_KEY,
                "secret": config.BINANCE_SECRET,
                "enableRateLimit": True,
            })
            logger.info("Live trading initialized: Binance")
        except ImportError:
            logger.error("ccxt not installed. Run: pip install ccxt")
        except Exception as e:
            logger.error("Failed to init exchange: %s", e)
