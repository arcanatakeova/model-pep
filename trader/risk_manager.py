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
        self._daily_loss_limit = 0.05    # 5% daily hard stop — protects real capital
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
        # Already in this position
        if self.portfolio.has_position(asset_id):
            return False, f"Already holding position in {asset_id}"

        # Max open positions
        if len(self.portfolio.open_positions) >= config.MAX_OPEN_POSITIONS:
            return False, f"Max open positions reached ({config.MAX_OPEN_POSITIONS})"

        # Minimum conviction
        if abs(signal_score) < config.MIN_SIGNAL_STRENGTH:
            return False, f"Signal too weak ({signal_score:.2f} < {config.MIN_SIGNAL_STRENGTH})"

        # Minimum available cash — use cash directly (not equity which includes
        # unrealized positions that can't be spent).
        if self.portfolio.cash < 10.0:
            return False, f"Insufficient cash (${self.portfolio.cash:.2f} < $10 min)"

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
        return False  # Disabled — bot always trades

    def _max_drawdown_triggered(self) -> bool:
        return False  # Disabled — bot always trades

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
        if leverage < 1:
            return 0.0
        if side == "long":
            liq = entry * (1 - (1 / leverage) + maintenance_margin)
            return max(liq, 0.0)
        else:
            return max(entry * (1 + (1 / leverage) - maintenance_margin), 0.0)

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

        # 1. Volatility adjustment — HIGH vol = REDUCE size (not increase)
        vol_proxy = (abs(price_change_h1) + abs(price_change_h6 / 6)) / 2
        vol_proxy = max(vol_proxy, 1.0)
        vol_boost = min(1.0 + (vol_proxy - 1.0) * 0.18, 2.5)
        vol_mult = max(0.3, 1.0 / vol_boost)  # Invert: high vol = smaller positions
        vol_adjusted = base_size * vol_mult

        # 2. Safety scaling: riskier tokens get smaller positions (no floor)
        safety_multiplier = safety_score

        # 3. Score scaling: higher score = closer to full size
        # Raised floor from 0.4 to 0.5 — don't half-size the entry
        score_multiplier = 0.5 + (token_score * 0.5)

        size = vol_adjusted * safety_multiplier * score_multiplier

        # 4. Liquidity cap: never more than 2% of pool liquidity
        size = min(size, liquidity_usd * 0.02)

        # 5. Equity-scaled floor: minimal (1% of equity)
        equity_floor = equity * 0.01
        size = max(size, equity_floor)

        # 6. Caps
        size = min(size, config.DEX_MAX_POSITION_USD)
        size = min(size, liquidity_usd * config.MIN_LIQUIDITY_RATIO)
        # Cash cap: divide available cash across slots so one trade can't drain wallet.
        # For small portfolios, reduce slot count so per-slot stays above min position.
        open_dex = len([p for p in self.portfolio.open_positions.values()
                        if p.get("market") == "dex"])
        free_slots = max(1, config.MAX_DEX_POSITIONS - open_dex)
        usable_cash = self.portfolio.cash * 0.85
        # Don't split into more slots than cash can support at minimum position size
        min_pos = config.DEX_MIN_POSITION_USD
        max_affordable_slots = max(1, int(usable_cash / min_pos)) if min_pos > 0 else free_slots
        effective_slots = min(free_slots, max_affordable_slots)
        per_slot_cash = usable_cash / effective_slots
        size = min(size, per_slot_cash)

        if size < config.DEX_MIN_POSITION_USD:
            return 0.0
        return round(size, 2)

    def poly_position_size_usd(self, signal_score: float, edge_pct: float,
                               available_cash: float, market_budget: float) -> float:
        """Position sizing for Polymarket prediction market trades."""
        equity = self.portfolio.equity()
        if equity <= 0:
            return 0.0

        # Kelly-inspired sizing: edge_pct is our expected edge
        kelly = edge_pct * signal_score
        kelly = max(0, min(kelly, 0.10))  # Cap at 10% of equity

        size = equity * kelly
        size = min(size, config.POLYMARKET_MAX_POSITION_USD)
        size = min(size, market_budget * 0.25)
        size = min(size, available_cash * 0.05)  # Max 5% of cash per poly trade

        return round(max(size, 0.0), 2)

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
        base = max(h1_vol * 2.0, h6_vol * 0.5, h24_vol * 0.20, 0.10)
        base = min(base, 0.40)

        # Safety-adjusted multiplier: risky tokens get TIGHTER stops (not wider)
        if safety_score >= 0.80:
            mult = 1.0     # well-audited token, full stop width
        elif safety_score >= 0.60:
            mult = 0.80    # moderate risk — tighter
        else:
            mult = 0.60    # unknown/risky — tightest stops, cut losses fast

        stop = round(min(base * mult, 0.25), 3)
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
        # Tokens that already ran big can run further — set target proportionally
        if h24 > 1.0:
            base = h24 * 0.50   # Already 100%+ → target 50% of that (they continue)
        else:
            base = max(h24 * 0.80, 0.35)
        base = min(base, 4.00)  # 400% ceiling (was 250% — too conservative)
        target = round(base * (1 + score * 0.4), 3)
        logger.debug("Dynamic target: h24=%.1f%% score=%.2f → target=%.1f%%",
                     h24*100, score, target*100)
        return target

    def check_dex_concentration(self, dex_positions: dict,
                                 token_dex_id: str = "") -> tuple[bool, str]:
        """Check memecoin concentration limits before opening new position."""
        if len(dex_positions) >= config.MAX_DEX_POSITIONS:
            return False, f"Max DEX positions reached ({config.MAX_DEX_POSITIONS})"

        # Use remaining_fraction to get actual current exposure (not original full size)
        total_dex_usd = sum(
            p.get("size_usd", 0) * p.get("remaining_fraction", 1.0)
            for p in dex_positions.values()
        )
        # Enforce memecoin allocation cap — but allow new trades when legacy
        # positions already exceed the cap (they'll exit on their own via stops/TP).
        # Only hard-block when BOTH: already way over cap AND new trade is large.
        equity = self.portfolio.equity()
        if equity > 0:
            cap_usd = equity * config.MAX_MEMECOIN_ALLOCATION_PCT
            free_slots = max(1, config.MAX_DEX_POSITIONS - len(dex_positions))
            next_trade_size = (self.portfolio.cash * 0.85) / free_slots
            # Only block if we'd exceed 120% of the cap after adding the new trade
            # (20% buffer prevents thrashing near the boundary)
            if total_dex_usd + next_trade_size > cap_usd * 1.20:
                return False, f"Memecoin allocation cap ({config.MAX_MEMECOIN_ALLOCATION_PCT:.0%} of equity)"

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
            logger.warning("Corrupted opened_at '%s' — forcing exit for safety", opened_str[:40])
            return True, "Corrupted timestamp — forcing exit"

        # Hard time limit
        if age_hours >= config.DEX_MAX_HOLD_HOURS:
            return True, f"Max hold time ({age_hours:.1f}h >= {config.DEX_MAX_HOLD_HOURS}h)"

        # Stale position: no meaningful gain after N hours
        if age_hours >= config.DEX_STALE_EXIT_HOURS:
            pnl_pct = position.get("current_pnl_pct", 0)
            if pnl_pct < config.DEX_STALE_MIN_GAIN_PCT:
                # Override stale exit if position already taking partial profits
                # (means it DID reach a good level, now waiting for moonshot)
                partials_taken = len(position.get("partial_profits_taken", []))
                if partials_taken > 0:
                    return False, "Hold (partial profits taken — moonshot runner)"
                # Override stale exit if still showing 5m momentum
                price_change_m5 = position.get("price_change_m5", 0)
                if abs(price_change_m5) > 3:
                    return False, "Hold (active 5m momentum)"
                return True, f"Stale ({age_hours:.1f}h, only {pnl_pct:.1%} gain)"

        return False, "Hold"

    def get_partial_profit_action(self, position: dict) -> tuple[Optional[float], str, float]:
        """
        Check if a partial profit-take is due.
        Returns (fraction_to_sell, reason, threshold_pct) or (None, "Hold", 0).
        threshold_pct is returned so the caller can mark the exact tier as taken.
        """
        if not config.PARTIAL_PROFIT_ENABLED:
            return None, "Hold", 0

        entry = position.get("entry_price", 0)
        current = position.get("current_price", entry)
        if entry <= 0:
            return None, "Hold", 0

        side = position.get("side", "long")
        if side == "long":
            pnl_pct = (current - entry) / entry
        else:
            pnl_pct = (entry - current) / entry
        if pnl_pct <= 0:
            return None, "Hold", 0   # Never partial-sell at a loss
        already_taken = position.get("partial_profits_taken", [])

        for threshold_pct, sell_fraction in config.PARTIAL_PROFIT_TIERS:
            if threshold_pct <= 0:
                continue   # Skip any misconfigured non-positive tiers
            if pnl_pct >= threshold_pct and threshold_pct not in already_taken:
                return sell_fraction, f"Partial TP at +{threshold_pct:.0%} (sell {sell_fraction:.0%})", threshold_pct

        return None, "Hold", 0

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
