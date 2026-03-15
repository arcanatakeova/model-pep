"""
Trade Executor — Bridges signals to actual (paper or live) trade execution.

Paper mode: simulates fills at market price with configurable slippage.
Live mode: routes to exchange APIs (Binance spot + Binance USDT-M Futures) via ccxt.

Futures trading uses isolated margin mode. Each futures position tracks:
  - leverage used
  - estimated liquidation price
  - margin posted (collateral)
"""
import logging
import threading
import time
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
FUTURES_COMMISSION = 0.0004  # 0.04% Binance futures taker fee

# Cooldown between reopening the same pair after a close
_FOREX_COOLDOWN_SEC  = 900   # 15 min — prevents whipsawing into the same trade
_CRYPTO_COOLDOWN_SEC = 120   # 2 min for crypto


class PositionMonitor:
    """
    Dedicated monitoring thread for a single open position.
    Polls price at market-appropriate frequency and triggers
    stop-loss / take-profit / trailing-stop at the exact right moment,
    independent of the main scan cycle timer.
    """
    _INTERVALS = {"forex": 3.0, "crypto": 5.0, "stocks": 30.0}

    def __init__(self, asset_id: str, executor: "TradeExecutor"):
        self.asset_id = asset_id
        self._ex      = executor
        self._thread  = threading.Thread(
            target=self._run, name=f"Mon-{asset_id[:12]}", daemon=True)
        self._thread.start()

    def _run(self):
        port     = self._ex.portfolio
        pos_init = port.open_positions.get(self.asset_id, {})
        market   = pos_init.get("market", "crypto")
        interval = self._INTERVALS.get(market, 5.0)
        logger.debug("PositionMonitor started for %s (%.0fs interval)", self.asset_id, interval)

        while self.asset_id in port.open_positions:
            try:
                pos = port.open_positions.get(self.asset_id)
                if not pos:
                    break

                price = self._ex._get_current_price(self.asset_id, pos)
                if price is None or price <= 0:
                    time.sleep(interval)
                    continue

                # Trailing stop update
                new_trail = self._ex.risk.update_trailing_stop(pos, price)
                if self.asset_id in port.open_positions:
                    port.open_positions[self.asset_id]["trailing_stop"] = new_trail
                    port.update_position_price(self.asset_id, price)

                # Exit check — use _close_lock to prevent double-close
                should_close, reason = self._ex.risk.should_close_position(pos, price)
                if should_close:
                    with self._ex._close_lock:
                        if self.asset_id in port.open_positions:
                            self._ex._execute_close(self.asset_id, price, reason)
                    break

            except Exception as e:
                logger.debug("PositionMonitor[%s] error: %s", self.asset_id, e)

            time.sleep(interval)


class TradeExecutor:
    """
    Executes trade signals:
    - Paper trading (default, safe simulation)
    - Live trading via ccxt (requires API keys + explicit enable)
    """

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager):
        self.portfolio = portfolio
        self.risk = risk_manager
        self._exchange = None          # Binance spot (live only)
        self._futures_exchange = None  # Binance USDT-M futures (live only)
        self._tls = threading.local()  # Thread-local storage for risk override
        self._close_lock = threading.Lock()              # Prevent double-close race conditions
        self._pair_cooldowns: dict[str, float] = {}      # asset_id → last close timestamp

        if not config.PAPER_TRADING:
            self._init_live_exchange()
            if config.FUTURES_ENABLED:
                self._init_futures_exchange()

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

        # ── Cooldown check — prevent re-entering same pair immediately ────
        cooldown = _FOREX_COOLDOWN_SEC if signal.market == "forex" else _CRYPTO_COOLDOWN_SEC
        last_close = self._pair_cooldowns.get(asset_id, 0)
        elapsed = time.time() - last_close
        if elapsed < cooldown:
            logger.debug("COOLDOWN %s — %ds remaining", asset_id, int(cooldown - elapsed))
            return None

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
        Mark-to-market all positions and run liquidation guard for futures.
        Stop/TP/trailing exits are handled by per-position PositionMonitor threads.
        """
        for asset_id in list(self.portfolio.open_positions.keys()):
            pos = self.portfolio.open_positions.get(asset_id)
            if not pos:
                continue

            current_price = self._get_current_price(asset_id, pos)
            if current_price is None:
                continue

            # Futures: liquidation guard is time-critical — keep in main loop
            if pos.get("is_futures"):
                self._check_liquidation_guard(asset_id, pos, current_price)
                if asset_id not in self.portfolio.open_positions:
                    continue

            # Mark-to-market (PositionMonitor threads handle stop/TP)
            self.portfolio.update_position_price(asset_id, current_price)

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

        # Thread-local risk override — safe for concurrent signal processing
        risk_override = getattr(self._tls, "risk_override", None)
        size_usd = self.risk.position_size_usd(
            signal.score, signal.conviction, stop_pct, price,
            risk_pct_override=risk_override,
        )

        if size_usd < 1.0:
            logger.debug("Position size too small ($%.2f) for %s", size_usd, signal.asset_id)
            return None

        # In live mode without a connected exchange, skip CEX trades — no fake positions
        if not config.PAPER_TRADING and self._exchange is None:
            return None

        fill_price = self._fill_price(price, "buy", signal.market)
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
        if not pos:
            self.portfolio.cash += commission  # Restore commission if open failed
            return None
        PositionMonitor(signal.asset_id, self)
        logger.info("EXECUTED BUY  %-12s $%.4f × %.6f = $%.2f | Score: %.2f | Conv: %.2f",
                    signal.symbol, fill_price, qty, size_usd, signal.score, signal.conviction)
        return pos

    def _open_short(self, signal: TradeSignal) -> Optional[dict]:
        """Open a short position (paper only)."""
        # In live mode without a connected exchange, skip — no fake CEX positions
        if not config.PAPER_TRADING and self._exchange is None:
            return None

        allowed, reason = self.risk.can_open_position(
            signal.asset_id, signal.score, signal.market)
        if not allowed:
            logger.debug("SKIP SELL %s: %s", signal.asset_id, reason)
            return None

        price    = signal.current_price
        stop_pct = abs(signal.stop_loss - price) / price if price > 0 else config.STOP_LOSS_PCT

        # Thread-local risk override — consistent with _open_long
        risk_override = getattr(self._tls, "risk_override", None)
        size_usd = self.risk.position_size_usd(
            abs(signal.score), signal.conviction, stop_pct, price,
            risk_pct_override=risk_override,
        )

        if size_usd < 1.0:
            return None

        fill_price = self._fill_price(price, "sell", signal.market)
        qty        = self.risk.qty_from_usd(size_usd, fill_price)
        commission = size_usd * COMMISSION

        # Pre-flight cash check: need at least commission
        if commission > self.portfolio.cash:
            logger.debug("Insufficient cash for short commission: need $%.4f, have $%.2f",
                         commission, self.portfolio.cash)
            return None

        self.portfolio.cash -= commission   # Deduct commission for short entry
        pos = self.portfolio.open_position(
            signal.asset_id, "short", qty, fill_price,
            signal.stop_loss, signal.take_profit, signal.to_dict()
        )
        if not pos:
            self.portfolio.cash += commission  # Restore commission if open failed
            return None
        PositionMonitor(signal.asset_id, self)
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
        pos_side = self.portfolio.open_positions.get(asset_id, {}).get("side", "long")
        market   = self.portfolio.open_positions.get(asset_id, {}).get("market", "crypto")
        fill_price = self._fill_price(price, "sell" if pos_side == "long" else "buy", market)
        trade = self.portfolio.close_position(asset_id, fill_price, reason)
        if trade:
            pnl_str = f"+${trade['pnl_usd']:.2f}" if trade['pnl_usd'] >= 0 else f"-${abs(trade['pnl_usd']):.2f}"
            logger.info("CLOSED %-12s @ $%.4f | PnL: %s (%.2f%%) | %s",
                        trade.get("symbol", asset_id), fill_price,
                        pnl_str, trade['pnl_pct'], reason)
            self._pair_cooldowns[asset_id] = time.time()  # Start cooldown after close
        return trade

    # ─────────────────────────────────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────────────────────────────────

    def _fill_price(self, market_price: float, side: str,
                    market: str = "crypto") -> float:
        """
        Simulate realistic fill price for paper trading.
        Forex uses a tight spread model (~0.5 pip on majors) rather than
        the 0.1% crypto slippage — otherwise stops are triggered incorrectly
        and position sizing is off by an order of magnitude in pips.
        """
        if market_price <= 0:
            return 0.0
        if config.PAPER_TRADING:
            # Forex: use typical pip spread (0.005% ≈ 0.5 pip on EUR/USD at 1.10)
            slip = 0.00005 if market == "forex" else SLIPPAGE
            if side == "buy":
                return market_price * (1 + slip)
            else:
                return market_price * (1 - slip)
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
                pair  = asset_id
                parts = pair.replace("-", "/").split("/")
                if len(parts) == 2:
                    base, quote = parts[0].strip(), parts[1].strip()
                    # get_forex_rates(base) returns {quote: price_per_base}
                    # e.g. get_forex_rates("USD")["JPY"] = 148  (JPY per USD) ✓
                    #      get_forex_rates("AUD")["USD"] = 0.63 (USD per AUD) ✓
                    rates = df_mod.get_forex_rates(base)
                    val   = rates.get(quote)
                    return float(val) if val else None
        except Exception as e:
            logger.debug("Price fetch error for %s: %s", asset_id, e)
        return None

    def _init_live_exchange(self):
        """Initialize ccxt exchange for live spot trading (Binance or Coinbase)."""
        try:
            import ccxt
            if config.BINANCE_API_KEY:
                self._exchange = ccxt.binance({
                    "apiKey": config.BINANCE_API_KEY,
                    "secret": config.BINANCE_SECRET,
                    "enableRateLimit": True,
                })
                logger.info("Live spot trading initialized: Binance")
            elif config.COINBASE_KEY_NAME or config.COINBASE_API_KEY:
                # Coinbase Advanced Trade (CDP): key name + EC private key (PEM)
                cb_key    = (config.COINBASE_KEY_NAME or config.COINBASE_API_KEY).strip()
                cb_secret = config.COINBASE_SECRET.strip()
                if not cb_key or not cb_secret:
                    logger.error("Coinbase key/secret incomplete — check Vault. "
                                 "key=%d chars, secret=%d chars", len(cb_key), len(cb_secret))
                else:
                    self._exchange = ccxt.coinbase({
                        "apiKey": cb_key,
                        "secret": cb_secret,
                        "enableRateLimit": True,
                    })
                    logger.info("Live spot trading initialized: Coinbase Advanced Trade")
            else:
                logger.debug("No exchange API keys set — CEX in paper mode")
        except ImportError:
            logger.error("ccxt not installed. Run: pip install ccxt")
        except Exception as e:
            logger.error("Failed to init spot exchange: %s", e)

    def _init_futures_exchange(self):
        """Initialize Binance USDT-M Futures via ccxt for leveraged perpetuals."""
        if not config.BINANCE_API_KEY:
            logger.debug("Futures enabled but no BINANCE_API_KEY — futures in paper mode")
            return
        try:
            import ccxt
            self._futures_exchange = ccxt.binanceusdm({
                "apiKey": config.BINANCE_API_KEY,
                "secret": config.BINANCE_SECRET,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            })
            logger.info("Futures trading initialized: Binance USDT-M Perpetuals")
        except ImportError:
            logger.error("ccxt not installed. Run: pip install ccxt")
        except Exception as e:
            logger.error("Failed to init futures exchange: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # Futures / Leveraged Execution
    # ─────────────────────────────────────────────────────────────────────────

    def open_futures_position(self, signal) -> Optional[dict]:
        """
        Open a leveraged futures position (long or short) on Binance USDT-M.

        Signal can be a TradeSignal or ScalpSignal (duck-typed: needs .signal,
        .score, .conviction, .current_price, .stop_loss, .take_profit, .symbol,
        .asset_id, .market).

        Returns the position dict or None if skipped.
        """
        if not config.FUTURES_ENABLED:
            return None
        if signal.signal == "HOLD":
            return None

        # BUG FIX: Hard gate — minimum conviction required for any leverage
        if signal.conviction < getattr(config, "MIN_FUTURES_CONVICTION", 0.40):
            logger.debug("SKIP FUTURES %s: conviction %.2f below min %.2f",
                         signal.symbol, signal.conviction,
                         getattr(config, "MIN_FUTURES_CONVICTION", 0.40))
            return None

        side = "long" if signal.signal == "BUY" else "short"
        price = signal.current_price
        if price <= 0:
            return None

        # Use a namespaced asset_id so futures don't collide with spot positions
        fut_asset_id = f"fut_{signal.asset_id}"

        # Check if we're already in this futures position
        if self.portfolio.has_position(fut_asset_id):
            return None

        allowed, reason = self.risk.can_open_position(
            fut_asset_id, signal.score, signal.market)
        if not allowed:
            logger.debug("SKIP FUTURES %s: %s", signal.symbol, reason)
            return None

        leverage = self.risk.leverage_for_signal(signal.score, signal.conviction)
        stop_pct  = abs(price - signal.stop_loss) / price if price > 0 else config.STOP_LOSS_PCT

        margin_usd = self.risk.leveraged_position_size_usd(
            signal.score, signal.conviction, stop_pct, price, leverage)
        if margin_usd < 1.0:
            logger.debug("Futures margin too small ($%.2f) for %s", margin_usd, signal.symbol)
            return None

        notional_usd = margin_usd * leverage
        fill_price   = self._fill_price(price, "buy" if side == "long" else "sell")
        qty          = self.risk.qty_from_usd(notional_usd, fill_price)
        commission   = notional_usd * FUTURES_COMMISSION
        liq_price    = self.risk.liquidation_price(fill_price, side, leverage)

        if margin_usd + commission > self.portfolio.cash:
            logger.debug("Insufficient cash for futures margin: need $%.2f, have $%.2f",
                         margin_usd + commission, self.portfolio.cash)
            return None

        # Execute live or paper
        if self._futures_exchange:
            try:
                self._futures_exchange.set_leverage(leverage, signal.symbol,
                                                     params={"marginMode": "isolated"})
                order_side = "buy" if side == "long" else "sell"
                self._futures_exchange.create_market_order(
                    signal.symbol, order_side, qty,
                    params={"reduceOnly": False},
                )
            except Exception as e:
                logger.error("Futures order failed for %s: %s", signal.symbol, e)
                return None

        # In live mode without a connected futures exchange, skip — never create fake
        # leveraged positions that would corrupt cash accounting with real SOL in play.
        if not config.PAPER_TRADING and self._futures_exchange is None:
            return None

        # Deduct only margin + commission (NOT the full notional).
        # portfolio.open_position() deducts qty*price (notional) for longs — we temporarily
        # pre-fund that amount so its internal check passes, then the net deduction is correct.
        self.portfolio.cash -= (margin_usd + commission)
        if side == "long":
            self.portfolio.cash += notional_usd  # temp pre-fund for open_position's check

        # Record as a portfolio position with futures metadata
        sig_dict = signal.to_dict() if hasattr(signal, "to_dict") else {}
        pos = self.portfolio.open_position(
            fut_asset_id, side, qty, fill_price,
            signal.stop_loss, signal.take_profit, sig_dict,
        )
        if not pos:
            # open_position failed — restore cash
            self.portfolio.cash += (margin_usd + commission)
            if side == "long":
                self.portfolio.cash -= notional_usd
            return None
        pos["is_futures"] = True
        pos["leverage"]   = leverage
        pos["liq_price"]  = round(liq_price, 6)
        pos["margin_usd"] = round(margin_usd, 2)
        pos["notional_usd"] = round(notional_usd, 2)
        pos["symbol"]     = signal.symbol
        pos["market"]     = signal.market
        pos["timeframe"]  = getattr(signal, "timeframe", "1h")

        logger.info(
            "FUTURES %-5s %-14s x%d @ $%-12.4f | "
            "margin=$%-8.2f notional=$%-8.2f | liq=$%.4f | %s",
            side.upper(), signal.symbol, leverage, fill_price,
            margin_usd, notional_usd, liq_price,
            getattr(signal, "reasons", [""])[0] if getattr(signal, "reasons", []) else "",
        )
        return pos

    def _check_liquidation_guard(self, asset_id: str, pos: dict, current_price: float):
        """
        Emit a warning and force-close a futures position if it's within
        15% of its estimated liquidation price (emergency protection).
        """
        liq = pos.get("liq_price", 0)
        if liq <= 0:
            return
        side = pos.get("side", "long")
        if side == "long":
            distance_pct = (current_price - liq) / current_price if current_price > 0 else 1.0
        else:
            distance_pct = (liq - current_price) / liq if liq > 0 else 1.0

        if distance_pct < 0.15:
            logger.warning(
                "⚠️  LIQUIDATION GUARD: %s within %.1f%% of liq ($%.4f) — closing now",
                asset_id, distance_pct * 100, liq)
            self._execute_close(asset_id, current_price, "Liquidation guard — emergency close")
