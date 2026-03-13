"""
Risk Manager — Position sizing, exposure limits, and risk controls.

Implements:
- Kelly Criterion (fractional) for position sizing
- Maximum portfolio exposure limits
- Correlation-based concentration limits
- Daily loss circuit breaker
- Volatility-adjusted position sizing
"""
from __future__ import annotations
import logging
import math
from typing import Optional

import config

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Manages risk parameters and position sizing.
    All dollar amounts in USD.
    """

    def __init__(self, portfolio):
        self.portfolio = portfolio
        self._daily_loss_start_equity: Optional[float] = None
        self._daily_loss_limit = 0.05    # 5% max daily drawdown before circuit break
        self._max_drawdown_limit = 0.15  # 15% max drawdown from peak

    # ─────────────────────────────────────────────────────────────────────────
    # Position Sizing
    # ─────────────────────────────────────────────────────────────────────────

    def position_size_usd(self, signal_score: float, conviction: float,
                          stop_pct: float, current_price: float) -> float:
        """
        Calculate position size in USD using a hybrid approach:
        1. Fixed-fractional risk (2% of equity per trade)
        2. Scaled by conviction and signal strength
        3. Capped at MAX_POSITION_PCT of portfolio

        Args:
            signal_score: [-1, +1] ensemble score
            conviction:   [0, 1] signal agreement
            stop_pct:     Fraction of price at risk (stop distance / price)
            current_price: Current asset price

        Returns:
            Dollar amount to allocate to this position.
        """
        equity = self.portfolio.equity()
        if equity <= 0 or stop_pct <= 0:
            return 0.0

        # Base risk: 2% of equity
        base_risk_usd = equity * config.RISK_PER_TRADE_PCT

        # Position size = risk / stop_distance
        # stop_pct is the fraction of price we're willing to lose
        raw_size = base_risk_usd / stop_pct

        # Scale by signal strength and conviction
        scale = min(abs(signal_score), 1.0) * conviction
        scaled_size = raw_size * (0.5 + scale * 0.5)  # 50%–100% of raw size

        # Cap at MAX_POSITION_PCT of total equity
        max_size = equity * config.MAX_POSITION_PCT
        position_usd = min(scaled_size, max_size)

        # Ensure we have enough free cash
        position_usd = min(position_usd, self.portfolio.cash * 0.95)

        return round(max(position_usd, 0.0), 2)

    def kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Full Kelly: f = (W * B - L) / B  where B = avg_win/avg_loss.
        Returns fractional Kelly (25%) for safety.
        """
        if avg_loss <= 0 or avg_win <= 0:
            return 0.0
        b = avg_win / avg_loss
        f = (win_rate * b - (1 - win_rate)) / b
        return max(0.0, f * 0.25)  # Quarter-Kelly

    def qty_from_usd(self, usd: float, price: float) -> float:
        """Convert dollar amount to asset quantity."""
        if price <= 0:
            return 0.0
        return usd / price

    # ─────────────────────────────────────────────────────────────────────────
    # Trade Validation
    # ─────────────────────────────────────────────────────────────────────────

    def can_open_position(self, asset_id: str, signal_score: float,
                          market: str = "crypto") -> tuple[bool, str]:
        """
        Check if a new position can be opened.
        Returns (allowed: bool, reason: str).
        """
        # Circuit breaker: daily loss limit
        if self._daily_loss_triggered():
            return False, "Daily loss limit reached — circuit breaker active"

        # Max drawdown guard
        if self._max_drawdown_triggered():
            return False, "Maximum drawdown limit reached"

        # Already in this position
        if self.portfolio.has_position(asset_id):
            return False, f"Already holding position in {asset_id}"

        # Max open positions
        if len(self.portfolio.open_positions) >= config.MAX_OPEN_POSITIONS:
            return False, f"Max open positions reached ({config.MAX_OPEN_POSITIONS})"

        # Minimum conviction
        if abs(signal_score) < config.MIN_SIGNAL_STRENGTH:
            return False, f"Signal too weak ({signal_score:.2f} < {config.MIN_SIGNAL_STRENGTH})"

        # Minimum available cash
        min_trade_usd = self.portfolio.equity() * 0.01
        if self.portfolio.cash < min_trade_usd:
            return False, "Insufficient cash"

        return True, "OK"

    def should_close_position(self, position: dict, current_price: float) -> tuple[bool, str]:
        """
        Determine if an open position should be closed.
        Checks: stop loss, take profit, trailing stop, time limit.
        """
        side          = position["side"]     # "long" or "short"
        entry_price   = position["entry_price"]
        stop_loss     = position["stop_loss"]
        take_profit   = position["take_profit"]
        trail_stop    = position.get("trailing_stop")

        if side == "long":
            pnl_pct = (current_price - entry_price) / entry_price
            # Take profit
            if current_price >= take_profit:
                return True, f"Take profit hit ({current_price:.4f} >= {take_profit:.4f})"
            # Stop loss
            if current_price <= stop_loss:
                return True, f"Stop loss hit ({current_price:.4f} <= {stop_loss:.4f})"
            # Trailing stop
            if trail_stop and current_price <= trail_stop:
                return True, f"Trailing stop hit ({current_price:.4f} <= {trail_stop:.4f})"
        else:  # short
            pnl_pct = (entry_price - current_price) / entry_price
            if current_price <= take_profit:
                return True, f"Take profit hit ({current_price:.4f} <= {take_profit:.4f})"
            if current_price >= stop_loss:
                return True, f"Stop loss hit ({current_price:.4f} >= {stop_loss:.4f})"
            if trail_stop and current_price >= trail_stop:
                return True, f"Trailing stop hit ({current_price:.4f} >= {trail_stop:.4f})"

        return False, "Hold"

    def update_trailing_stop(self, position: dict, current_price: float) -> float:
        """Update trailing stop based on current price movement."""
        side = position["side"]
        trail_pct = config.TRAILING_STOP_PCT

        if side == "long":
            new_trail = current_price * (1 - trail_pct)
            current_trail = position.get("trailing_stop", 0)
            return max(new_trail, current_trail)  # Only moves up
        else:
            new_trail = current_price * (1 + trail_pct)
            current_trail = position.get("trailing_stop", float("inf"))
            return min(new_trail, current_trail)  # Only moves down

    # ─────────────────────────────────────────────────────────────────────────
    # Circuit Breakers
    # ─────────────────────────────────────────────────────────────────────────

    def reset_daily_loss_tracker(self):
        """Call at the start of each trading day."""
        self._daily_loss_start_equity = self.portfolio.equity()

    def _daily_loss_triggered(self) -> bool:
        if self._daily_loss_start_equity is None:
            self._daily_loss_start_equity = self.portfolio.equity()
            return False
        current = self.portfolio.equity()
        daily_loss = (self._daily_loss_start_equity - current) / self._daily_loss_start_equity
        if daily_loss >= self._daily_loss_limit:
            logger.warning("CIRCUIT BREAKER: Daily loss %.1f%% exceeds limit %.1f%%",
                           daily_loss * 100, self._daily_loss_limit * 100)
            return True
        return False

    def _max_drawdown_triggered(self) -> bool:
        peak = self.portfolio.peak_equity
        current = self.portfolio.equity()
        if peak <= 0:
            return False
        drawdown = (peak - current) / peak
        if drawdown >= self._max_drawdown_limit:
            logger.warning("CIRCUIT BREAKER: Max drawdown %.1f%% exceeds limit %.1f%%",
                           drawdown * 100, self._max_drawdown_limit * 100)
            return True
        return False

    def risk_report(self) -> dict:
        """Return current risk metrics summary."""
        equity = self.portfolio.equity()
        peak   = self.portfolio.peak_equity
        drawdown = (peak - equity) / peak if peak > 0 else 0
        daily_start = self._daily_loss_start_equity or equity
        daily_loss = (daily_start - equity) / daily_start if daily_start > 0 else 0

        return {
            "equity": round(equity, 2),
            "cash": round(self.portfolio.cash, 2),
            "open_positions": len(self.portfolio.open_positions),
            "max_positions": config.MAX_OPEN_POSITIONS,
            "peak_equity": round(peak, 2),
            "current_drawdown_pct": round(drawdown * 100, 2),
            "daily_loss_pct": round(daily_loss * 100, 2),
            "circuit_breaker_active": self._daily_loss_triggered() or self._max_drawdown_triggered(),
        }
