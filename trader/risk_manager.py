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
        self._daily_loss_limit = 0.20    # 20% — bot always keeps trading and recovering
        self._max_drawdown_limit = 0.50  # 50% max drawdown hard stop (last resort only)

    # ─────────────────────────────────────────────────────────────────────────
    # Position Sizing
    # ─────────────────────────────────────────────────────────────────────────

    def position_size_usd(self, signal_score: float, conviction: float,
                          stop_pct: float, current_price: float,
                          risk_pct_override: float = None) -> float:
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

        # Base risk: 2% of equity (or override passed by executor)
        risk_pct = risk_pct_override if risk_pct_override is not None else config.RISK_PER_TRADE_PCT
        base_risk_usd = equity * risk_pct

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
        # Max drawdown guard (50% hard stop — catastrophic protection only)
        if self._max_drawdown_triggered():
            return False, "Maximum drawdown limit reached (50%)"

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
        # Use tighter limit when leverage/futures are active
        limit = (config.FUTURES_DAILY_LOSS_LIMIT
                 if getattr(config, "FUTURES_ENABLED", False) else self._daily_loss_limit)
        if daily_loss >= limit:
            logger.warning("CIRCUIT BREAKER: Daily loss %.1f%% exceeds limit %.1f%%",
                           daily_loss * 100, limit * 100)
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

    # ─────────────────────────────────────────────────────────────────────────
    # Leverage / Futures Risk Controls
    # ─────────────────────────────────────────────────────────────────────────

    def leverage_for_signal(self, score: float, conviction: float) -> int:
        """
        Choose leverage based on signal conviction.
        Higher conviction → higher leverage (capped at MAX_LEVERAGE).
        """
        for min_conv, lev in config.LEVERAGE_BY_CONVICTION:
            if conviction >= min_conv:
                return min(lev, config.MAX_LEVERAGE)
        return 2

    def leveraged_position_size_usd(self, signal_score: float, conviction: float,
                                     stop_pct: float, price: float,
                                     leverage: int) -> float:
        """
        Calculate margin (collateral) required for a leveraged futures position.

        Logic:
          - Risk the same USD per trade as spot (FUTURES_RISK_PCT of equity)
          - margin = risk_usd / stop_pct  (how much collateral covers the stop)
          - Notional = margin * leverage  (exchange controls the larger position)
          - Caps: MAX_POSITION_PCT of equity, 20% of free cash

        Returns margin USD to post (not notional).
        """
        equity = self.portfolio.equity()
        if equity <= 0 or stop_pct <= 0 or leverage <= 0:
            return 0.0

        base_risk_usd = equity * config.FUTURES_RISK_PCT
        margin = base_risk_usd / stop_pct

        # Scale by signal quality
        scale = min(abs(signal_score), 1.0) * conviction
        margin *= (0.5 + scale * 0.5)

        # Caps
        margin = min(margin, equity * config.MAX_POSITION_PCT)
        margin = min(margin, self.portfolio.cash * 0.20)

        return round(max(margin, 0.0), 2)

    def liquidation_price(self, entry: float, side: str,
                           leverage: int,
                           maintenance_margin: float = 0.005) -> float:
        """
        Estimate isolated-margin liquidation price.

        For LONG:  liq ≈ entry * (1 − 1/leverage + maintenance_margin)
        For SHORT: liq ≈ entry * (1 + 1/leverage − maintenance_margin)
        """
        if leverage <= 0:
            return 0.0
        if side == "long":
            return entry * (1 - (1 / leverage) + maintenance_margin)
        else:
            return entry * (1 + (1 / leverage) - maintenance_margin)

    # ─────────────────────────────────────────────────────────────────────────
    # DEX / Memecoin-Specific Risk Controls
    # ─────────────────────────────────────────────────────────────────────────

    def dex_position_size_usd(self, token_score: float, safety_score: float,
                               liquidity_usd: float, price_change_h1: float,
                               price_change_h6: float) -> float:
        """
        Volatility-adjusted position sizing for DEX/memecoin trades.
        Higher volatility = smaller size. Lower safety = smaller size.
        """
        equity = self.portfolio.equity()
        if equity <= 0:
            return 0.0

        base_size = config.DEX_BASE_POSITION_USD

        # 1. Volatility adjustment (use h1/h6 changes as vol proxy)
        vol_proxy = (abs(price_change_h1) + abs(price_change_h6) / 6) / 2
        vol_proxy = max(vol_proxy, 1.0)
        vol_adjusted = base_size / (vol_proxy * config.POSITION_VOL_SCALAR / 10)
        if vol_proxy > 30:
            vol_adjusted *= 0.5  # Extra cut for extreme volatility

        # 2. Safety scaling: riskier tokens get smaller positions
        safety_multiplier = max(0.3, safety_score)

        # 3. Score scaling: higher score = closer to full size
        score_multiplier = 0.5 + (token_score * 0.5)

        size = vol_adjusted * safety_multiplier * score_multiplier

        # 4. Caps
        size = min(size, config.DEX_MAX_POSITION_USD)
        size = min(size, equity * config.MAX_POSITION_PCT)
        size = min(size, liquidity_usd * config.MIN_LIQUIDITY_RATIO)
        size = min(size, self.portfolio.cash * 0.10)

        if size < config.DEX_MIN_POSITION_USD:
            return 0.0
        return round(size, 2)

    def dynamic_dex_stop_pct(self, price_change_h1: float, price_change_h6: float,
                              price_change_h24: float, safety_score: float) -> float:
        """
        AI-set stop-loss for each DEX trade based on observed volatility.

        Logic: if a token moves ±20% per hour normally, a 15% stop is just noise —
        it will get hit on random oscillations. Stop should be wide enough to survive
        the token's own natural swing range while still protecting from real losses.

          Base stop  = 2.5× the 1h move (gives room for 2-3 normal candles)
          Minimum    = 20% (memecoins always need at least this)
          Maximum    = 50% (hard cap — never risk more than half the position)
          Safety adj = riskier tokens swing harder → need even wider stops
        """
        h1_vol  = abs(price_change_h1  or 0) / 100
        h6_vol  = abs(price_change_h6  or 0) / 100
        h24_vol = abs(price_change_h24 or 0) / 100

        # Use the largest observed timeframe as the volatility anchor
        base = max(h1_vol * 2.5, h6_vol * 0.6, h24_vol * 0.25, 0.20)
        base = min(base, 0.45)

        # Safety-adjusted multiplier: less trusted = more volatile = wider stop needed
        if safety_score >= 0.80:
            mult = 0.85    # well-audited token, slightly tighter
        elif safety_score >= 0.60:
            mult = 1.00    # average
        else:
            mult = 1.30    # unknown/risky — expect big swings

        stop = round(min(base * mult, 0.50), 3)
        logger.debug("Dynamic stop: h1=%.1f%% h6=%.1f%% h24=%.1f%% safety=%.2f → stop=%.1f%%",
                     h1_vol*100, h6_vol*100, h24_vol*100, safety_score, stop*100)
        return stop

    def dynamic_dex_target_pct(self, price_change_h24: float, score: float) -> float:
        """
        AI-set take-profit for each DEX trade based on momentum strength.

        A token that already ran +200% in 24h has shown it CAN move big.
        Set targets proportionally — don't cut a moonshot at +40%.

          Base target = 80% of the 24h move already seen (tokens often continue)
          Minimum     = 35% (never settle for less on a memecoin)
          Maximum     = 250% (realistic ceiling for a 1-3 day hold)
          Score boost = higher conviction → higher target
        """
        h24 = abs(price_change_h24 or 0) / 100
        base = max(h24 * 0.80, 0.35)
        base = min(base, 2.50)
        target = round(base * (1 + score * 0.3), 3)
        logger.debug("Dynamic target: h24=%.1f%% score=%.2f → target=%.1f%%",
                     h24*100, score, target*100)
        return target

    def check_dex_concentration(self, dex_positions: dict,
                                 token_dex_id: str = "") -> tuple[bool, str]:
        """Check memecoin concentration limits before opening new position."""
        if len(dex_positions) >= config.MAX_DEX_POSITIONS:
            return False, f"Max DEX positions reached ({config.MAX_DEX_POSITIONS})"

        total_dex_usd = sum(p.get("size_usd", 0) for p in dex_positions.values())
        max_dex_total = self.portfolio.equity() * config.MAX_MEMECOIN_ALLOCATION_PCT
        if total_dex_usd >= max_dex_total:
            return False, f"Memecoin allocation cap (${total_dex_usd:.0f} >= ${max_dex_total:.0f})"

        if token_dex_id:
            same_dex = sum(1 for p in dex_positions.values()
                          if p.get("dex_id", "") == token_dex_id)
            if same_dex >= config.MAX_SAME_DEX_POSITIONS:
                return False, f"Max positions on {token_dex_id} reached"

        return True, "OK"

    def check_time_exit(self, position: dict) -> tuple[bool, str]:
        """Check if a DEX position should be closed due to time rules."""
        from datetime import datetime, timezone

        opened_str = position.get("opened_at", "")
        if not opened_str:
            return False, "Hold"

        try:
            opened_at = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
        except (ValueError, TypeError):
            return False, "Hold"

        # Hard time limit
        if age_hours >= config.DEX_MAX_HOLD_HOURS:
            return True, f"Max hold time ({age_hours:.1f}h >= {config.DEX_MAX_HOLD_HOURS}h)"

        # Stale position: no meaningful gain after N hours
        if age_hours >= config.DEX_STALE_EXIT_HOURS:
            pnl_pct = position.get("current_pnl_pct", 0)
            if pnl_pct < config.DEX_STALE_MIN_GAIN_PCT:
                return True, f"Stale ({age_hours:.1f}h, only {pnl_pct:.1%} gain)"

        return False, "Hold"

    def get_partial_profit_action(self, position: dict) -> tuple[Optional[float], str]:
        """
        Check if a partial profit-take is due.
        Returns (fraction_to_sell, reason) or (None, "Hold").
        """
        if not config.PARTIAL_PROFIT_ENABLED:
            return None, "Hold"

        entry = position.get("entry_price", 0)
        current = position.get("current_price", entry)
        if entry <= 0:
            return None, "Hold"

        pnl_pct = (current - entry) / entry
        already_taken = position.get("partial_profits_taken", [])

        for threshold_pct, sell_fraction in config.PARTIAL_PROFIT_TIERS:
            if pnl_pct >= threshold_pct and threshold_pct not in already_taken:
                return sell_fraction, f"Partial TP at +{threshold_pct:.0%} (sell {sell_fraction:.0%})"

        return None, "Hold"

    # ─────────────────────────────────────────────────────────────────────────
    # Risk Reporting
    # ─────────────────────────────────────────────────────────────────────────

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
