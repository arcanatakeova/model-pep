"""
Polymarket Position Manager
============================
Full lifecycle position tracking with trailing stops, partial exits,
daily P&L circuit breakers, correlation checks, and persistence.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import PolyMarket, PolySignal, PolyPosition

logger = logging.getLogger(__name__)


# ─── Trailing Stop ────────────────────────────────────────────────────────────

class TrailingStop:
    """Trailing stop that adjusts upward with favorable price movement."""

    def __init__(self, entry_price: float, initial_stop_pct: float = 0.15,
                 trail_pct: float = 0.10, activation_pct: float = 0.05):
        self.entry_price = entry_price
        self.initial_stop_pct = initial_stop_pct
        self.trail_pct = trail_pct
        self.activation_pct = activation_pct
        self.initial_stop = max(0.01, entry_price * (1 - initial_stop_pct))
        self.highest_price = entry_price
        self.current_stop = self.initial_stop
        self.activated = False

    def update(self, current_price: float) -> float:
        """Update trailing stop and return current stop level."""
        if current_price > self.highest_price:
            self.highest_price = current_price

        # Activate trailing once position is up enough from entry
        if not self.activated:
            if self.entry_price > 0:
                gain_pct = (current_price - self.entry_price) / self.entry_price
                if gain_pct >= self.activation_pct:
                    self.activated = True
                    logger.debug("Trailing stop activated at %.4f (gain %.1f%%)",
                                 current_price, gain_pct * 100)

        if self.activated:
            trail_stop = self.highest_price * (1 - self.trail_pct)
            self.current_stop = max(self.current_stop, trail_stop)

        return self.current_stop

    def is_triggered(self, current_price: float) -> bool:
        """Return True if current price has fallen below the trailing stop."""
        self.update(current_price)
        return current_price <= self.current_stop

    def to_dict(self) -> dict:
        return {
            "entry_price": self.entry_price,
            "initial_stop_pct": self.initial_stop_pct,
            "trail_pct": self.trail_pct,
            "activation_pct": self.activation_pct,
            "initial_stop": self.initial_stop,
            "highest_price": self.highest_price,
            "current_stop": self.current_stop,
            "activated": self.activated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrailingStop:
        ts = cls(
            entry_price=d.get("entry_price", 0.5),
            initial_stop_pct=d.get("initial_stop_pct", 0.15),
            trail_pct=d.get("trail_pct", 0.10),
            activation_pct=d.get("activation_pct", 0.05),
        )
        ts.initial_stop = d.get("initial_stop", ts.initial_stop)
        ts.highest_price = d.get("highest_price", ts.highest_price)
        ts.current_stop = d.get("current_stop", ts.current_stop)
        ts.activated = d.get("activated", False)
        return ts


# ─── Exit Schedule (Partial Profit Taking) ────────────────────────────────────

class ExitSchedule:
    """Defines partial exit targets for a position based on edge size."""

    def __init__(self, entry_price: float, edge_pct: float):
        self.entry_price = entry_price
        self.edge_pct = edge_pct
        self.targets: list[dict] = [
            {
                "pct_to_sell": 0.33,
                "target_price": min(0.99, entry_price + edge_pct * 1.5),
                "filled": False,
                "label": "T1 (1.5x edge)",
            },
            {
                "pct_to_sell": 0.33,
                "target_price": min(0.99, entry_price + edge_pct * 2.5),
                "filled": False,
                "label": "T2 (2.5x edge)",
            },
            # Final ~34% rides to resolution or trailing stop
        ]

    def check_targets(self, current_price: float) -> Optional[dict]:
        """Return the next unfilled target that has been hit, or None."""
        for target in self.targets:
            if not target["filled"] and current_price >= target["target_price"]:
                return target
        return None

    def mark_filled(self, target: dict) -> None:
        """Mark a target as filled."""
        target["filled"] = True

    @property
    def all_filled(self) -> bool:
        return all(t["filled"] for t in self.targets)

    def to_dict(self) -> dict:
        return {
            "entry_price": self.entry_price,
            "edge_pct": self.edge_pct,
            "targets": copy.deepcopy(self.targets),
        }

    @classmethod
    def from_dict(cls, d: dict) -> ExitSchedule:
        es = cls(
            entry_price=d.get("entry_price", 0.5),
            edge_pct=d.get("edge_pct", 0.05),
        )
        saved_targets = d.get("targets", [])
        if saved_targets:
            es.targets = copy.deepcopy(saved_targets)
        return es


# ─── Daily P&L Tracker / Circuit Breaker ──────────────────────────────────────

class DailyPnLTracker:
    """Track daily P&L for circuit breaker logic."""

    HALT_DURATION_SECONDS = 6 * 3600  # 6 hours

    def __init__(self):
        self._daily_pnl: dict[str, float] = {}        # date_str -> realized P&L
        self._daily_start_equity: dict[str, float] = {}
        self._halted_until: float = 0.0

    def _today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def record_trade_pnl(self, pnl: float) -> None:
        """Record realized P&L from a closed trade."""
        key = self._today_key()
        self._daily_pnl[key] = self._daily_pnl.get(key, 0.0) + pnl

    def set_start_equity(self, equity: float) -> None:
        """Record equity at start of day for loss-% computation."""
        key = self._today_key()
        if key not in self._daily_start_equity:
            self._daily_start_equity[key] = equity

    def get_today_pnl(self) -> float:
        return self._daily_pnl.get(self._today_key(), 0.0)

    def is_halted(self) -> bool:
        return time.time() < self._halted_until

    def check_circuit_breaker(self, total_exposure: float) -> bool:
        """
        Returns True if trading should be halted.
        Triggers if daily loss > 5% of total polymarket exposure.
        Halts new entries for 6 hours.
        """
        if self.is_halted():
            return True

        today_pnl = self.get_today_pnl()
        if total_exposure <= 0:
            return False

        loss_pct = abs(today_pnl) / total_exposure if today_pnl < 0 else 0.0
        if loss_pct > 0.05:
            self._halted_until = time.time() + self.HALT_DURATION_SECONDS
            logger.warning(
                "CIRCUIT BREAKER: daily poly P&L $%.2f (%.1f%% of $%.0f exposure). "
                "Halting new entries for 6 hours.",
                today_pnl, loss_pct * 100, total_exposure,
            )
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "daily_pnl": dict(self._daily_pnl),
            "daily_start_equity": dict(self._daily_start_equity),
            "halted_until": self._halted_until,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DailyPnLTracker:
        tracker = cls()
        tracker._daily_pnl = d.get("daily_pnl", {})
        tracker._daily_start_equity = d.get("daily_start_equity", {})
        tracker._halted_until = d.get("halted_until", 0.0)
        return tracker


# ─── Position Manager ─────────────────────────────────────────────────────────

class PolyPositionManager:
    """Manages Polymarket positions with trailing stops, partial exits, and P&L tracking."""

    POSITIONS_FILE = "poly_positions.json"
    TRADE_HISTORY_FILE = "poly_trade_history.json"
    MAX_BACKUP_COUNT = 3

    def __init__(self, api_client, ws_feed=None):
        self._api = api_client
        self._ws = ws_feed
        self._positions: dict[str, PolyPosition] = {}  # condition_id -> PolyPosition
        self._trailing_stops: dict[str, TrailingStop] = {}  # condition_id -> TrailingStop
        self._exit_schedules: dict[str, ExitSchedule] = {}  # condition_id -> ExitSchedule
        self._entry_contexts: dict[str, dict] = {}  # condition_id -> signal/market snapshot
        self._pnl_tracker = DailyPnLTracker()
        self._lock = threading.Lock()
        self.load()

    # ─── Position Opening ─────────────────────────────────────────────────

    def open_position(self, signal: PolySignal, size_usdc: float,
                      order_result: dict) -> Optional[PolyPosition]:
        """
        Open a new position with dynamic stop loss, trailing stop, and
        partial exit schedule.

        Validates:
        - No duplicate position in same market
        - Total exposure does not exceed limit
        - Circuit breaker not active
        """
        import config

        mkt = signal.market
        condition_id = mkt.condition_id

        # --- Validation: duplicate position ---
        with self._lock:
            if condition_id in self._positions:
                logger.warning(
                    "POLY POS skip: already have position in %s ('%s')",
                    condition_id[:8], mkt.question[:40],
                )
                return None

        # --- Validation: total exposure limit ---
        max_total = getattr(config, "POLYMARKET_MAX_TOTAL_EXPOSURE", 1000)
        current_exposure = self.get_total_exposure()
        if current_exposure + size_usdc > max_total:
            allowed = max_total - current_exposure
            if allowed < getattr(config, "POLYMARKET_MIN_ORDER_SIZE", 5.0):
                logger.warning(
                    "POLY POS skip: exposure $%.0f + $%.0f > limit $%.0f",
                    current_exposure, size_usdc, max_total,
                )
                return None
            size_usdc = allowed
            logger.info("POLY POS capped size to $%.2f (exposure limit)", size_usdc)

        # --- Validation: circuit breaker ---
        if self._pnl_tracker.check_circuit_breaker(current_exposure):
            logger.warning("POLY POS skip: circuit breaker active")
            return None

        # --- Build position ---
        token_id = mkt.yes_token_id if signal.side == "YES" else mkt.no_token_id
        shares = round(size_usdc / signal.target_price, 2) if signal.target_price > 0 else 0.0

        # Dynamic stop loss: tighter for high-edge signals, wider for low-edge
        base_stop_pct = getattr(config, "POLYMARKET_STOP_LOSS_PCT", 0.15)
        edge_abs = abs(signal.edge_pct)
        if edge_abs >= 0.10:
            stop_loss_pct = base_stop_pct * 0.8  # Tighter stop for large edge
        elif edge_abs <= 0.04:
            stop_loss_pct = base_stop_pct * 1.2  # Wider stop for thin edge
        else:
            stop_loss_pct = base_stop_pct

        # Strategy-specific adjustments
        if signal.strategy in ("smart_money_follow", "whale_follow"):
            stop_loss_pct *= 0.85  # Tighter: smart money signals are more reliable
        elif signal.strategy in ("cross_platform_arb", "consensus_divergence"):
            stop_loss_pct *= 1.1   # Wider: arb can take time to converge

        take_profit_mult = getattr(config, "POLYMARKET_TAKE_PROFIT_MULT", 2.5)

        pos = PolyPosition(
            condition_id=condition_id,
            token_id=token_id,
            side=signal.side,
            entry_price=signal.target_price,
            current_price=signal.target_price,
            size_usdc=size_usdc,
            shares=shares,
            opened_at=datetime.now(timezone.utc).isoformat(),
            market_question=mkt.question,
            end_date=mkt.end_date,
            strategy=signal.strategy,
            stop_loss_price=max(0.01, signal.target_price * (1 - stop_loss_pct)),
            take_profit_price=min(0.99, signal.target_price + signal.edge_pct * take_profit_mult),
        )

        # Trailing stop
        trailing = TrailingStop(
            entry_price=signal.target_price,
            initial_stop_pct=stop_loss_pct,
            trail_pct=stop_loss_pct * 0.7,  # Trail at 70% of stop width
            activation_pct=edge_abs * 0.5,  # Activate after capturing half the edge
        )

        # Partial exit schedule
        exit_sched = ExitSchedule(
            entry_price=signal.target_price,
            edge_pct=signal.edge_pct,
        )

        # Entry context for post-trade analysis
        entry_ctx = {
            "signal": signal.to_dict(),
            "market_snapshot": {
                "yes_price": mkt.yes_price,
                "no_price": mkt.no_price,
                "volume_24h": mkt.volume_24h,
                "spread": mkt.spread,
                "open_interest": mkt.open_interest,
            },
            "order_result": order_result,
            "stop_loss_pct_used": stop_loss_pct,
            "opened_at_ts": time.time(),
        }

        with self._lock:
            self._positions[condition_id] = pos
            self._trailing_stops[condition_id] = trailing
            self._exit_schedules[condition_id] = exit_sched
            self._entry_contexts[condition_id] = entry_ctx

        # Update daily equity baseline
        self._pnl_tracker.set_start_equity(current_exposure + size_usdc)

        self.save()
        logger.info(
            "POLY POS opened: %s %s @ %.4f $%.0f stop=%.4f trail_act=%.1f%% '%s'",
            signal.side, condition_id[:8], signal.target_price, size_usdc,
            pos.stop_loss_price, trailing.activation_pct * 100, mkt.question[:40],
        )
        return pos

    # ─── Price Updates ────────────────────────────────────────────────────

    def update_prices(self) -> None:
        """Update current prices for all positions and advance trailing stops."""
        with self._lock:
            positions = list(self._positions.values())

        for pos in positions:
            price = None

            # Try WebSocket first
            if self._ws:
                try:
                    price = self._ws.get_price(pos.token_id)
                except Exception:
                    pass

            # Fallback to REST
            if price is None:
                try:
                    mid = self._api.get_midpoint(pos.token_id)
                    if mid is not None:
                        price = mid
                except Exception:
                    pass

            if price is not None and price > 0:
                with self._lock:
                    if pos.condition_id in self._positions:
                        p = self._positions[pos.condition_id]
                        p.current_price = price
                        if p.entry_price > 0:
                            p.unrealized_pnl = (price - p.entry_price) * p.shares
                            p.unrealized_pnl_pct = (price - p.entry_price) / p.entry_price

                    # Advance trailing stop
                    ts = self._trailing_stops.get(pos.condition_id)
                    if ts:
                        ts.update(price)

    # ─── Exit Checks ─────────────────────────────────────────────────────

    def check_exits(self) -> list[tuple[PolyPosition, str, float]]:
        """
        Check all positions for exit conditions.
        Returns (position, reason, exit_fraction) tuples.

        Exit rules (checked in priority order):
        1. HARD STOP: Price below stop_loss_price -> full exit
        2. TRAILING STOP: Price below trailing stop -> full exit
        3. PARTIAL TAKE PROFIT: Hit a partial exit target -> sell that fraction
        4. RESOLUTION PROXIMITY: <24h to end_date AND profitable -> full exit
        5. TIME DECAY: Held >48h with <2% movement -> full exit (stale)
        6. MAX HOLD TIME: Held > POLYMARKET_MAX_HOLD_HOURS -> full exit
        7. CROSS-PLATFORM SHIFT: Consensus shifted against us by >10% -> full exit
        8. DAILY LOSS LIMIT: Portfolio poly P&L < -5% today -> close all
        """
        exits: list[tuple[PolyPosition, str, float]] = []

        with self._lock:
            positions = list(self._positions.values())

        # Check daily circuit breaker first — if triggered, close everything
        total_exposure = self.get_total_exposure()
        if self._pnl_tracker.get_today_pnl() < 0:
            if self._pnl_tracker.check_circuit_breaker(total_exposure):
                for pos in positions:
                    exits.append((pos, "Daily loss circuit breaker", 1.0))
                return exits

        for pos in positions:
            cid = pos.condition_id

            # 1. Hard stop loss
            reason = self._check_stop_loss(pos)
            if reason:
                exits.append((pos, reason, 1.0))
                continue

            # 2. Trailing stop
            reason = self._check_trailing_stop(pos)
            if reason:
                exits.append((pos, reason, 1.0))
                continue

            # 3. Partial take profit
            partial = self._check_partial_exit(pos)
            if partial:
                reason_text, fraction = partial
                exits.append((pos, reason_text, fraction))
                # Don't continue — allow other checks on remaining position
                continue

            # 4. Resolution proximity
            reason = self._check_resolution_exit(pos)
            if reason:
                exits.append((pos, reason, 1.0))
                continue

            # 5-6. Time decay / max hold
            reason = self._check_time_decay_exit(pos)
            if reason:
                exits.append((pos, reason, 1.0))
                continue

            # 7. Cross-platform shift
            reason = self._check_cross_platform_shift(pos)
            if reason:
                exits.append((pos, reason, 1.0))
                continue

        return exits

    # ─── Individual Exit Rules ────────────────────────────────────────────

    def _check_stop_loss(self, pos: PolyPosition) -> Optional[str]:
        """Hard stop loss: exit if price drops below threshold."""
        if pos.stop_loss_price > 0 and pos.current_price <= pos.stop_loss_price:
            return f"Stop loss hit ({pos.current_price:.4f} <= {pos.stop_loss_price:.4f})"
        return None

    def _check_trailing_stop(self, pos: PolyPosition) -> Optional[str]:
        """Trailing stop: exit if price drops below trailing stop level."""
        ts = self._trailing_stops.get(pos.condition_id)
        if ts and ts.is_triggered(pos.current_price):
            return (
                f"Trailing stop hit ({pos.current_price:.4f} <= {ts.current_stop:.4f}, "
                f"high={ts.highest_price:.4f})"
            )
        return None

    def _check_partial_exit(self, pos: PolyPosition) -> Optional[tuple[str, float]]:
        """Partial take profit: sell a fraction if a target price is hit."""
        sched = self._exit_schedules.get(pos.condition_id)
        if not sched:
            return None

        target = sched.check_targets(pos.current_price)
        if target:
            label = target.get("label", "partial target")
            fraction = target["pct_to_sell"]
            return (
                f"Partial exit {label} ({pos.current_price:.4f} >= {target['target_price']:.4f})",
                fraction,
            )
        return None

    def _check_take_profit(self, pos: PolyPosition) -> Optional[str]:
        """Full take profit: exit if price reaches the hard take-profit level."""
        if pos.take_profit_price > 0 and pos.current_price >= pos.take_profit_price:
            return f"Take profit hit ({pos.current_price:.4f} >= {pos.take_profit_price:.4f})"
        return None

    def _check_resolution_exit(self, pos: PolyPosition) -> Optional[str]:
        """Exit if market is within 24h of resolution and position is profitable."""
        if not pos.end_date:
            return None
        try:
            end = datetime.fromisoformat(pos.end_date.replace("Z", "+00:00"))
            hours_left = (end - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_left < 24 and pos.unrealized_pnl > 0:
                return f"Resolution proximity ({hours_left:.0f}h left, profitable)"
        except (ValueError, TypeError):
            pass
        return None

    def _check_time_decay_exit(self, pos: PolyPosition) -> Optional[str]:
        """Exit if position is stale or has exceeded max hold time."""
        import config
        max_hours = getattr(config, "POLYMARKET_MAX_HOLD_HOURS", 168)

        try:
            opened = datetime.fromisoformat(pos.opened_at.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
        except (ValueError, TypeError):
            return None

        if age_hours >= max_hours:
            return f"Max hold time ({age_hours:.0f}h >= {max_hours}h)"

        # Stale: held > 48h with minimal movement
        if age_hours >= 48 and abs(pos.unrealized_pnl_pct) < 0.02:
            return f"Stale position ({age_hours:.0f}h, {pos.unrealized_pnl_pct:.1%} move)"

        return None

    def _check_cross_platform_shift(self, pos: PolyPosition) -> Optional[str]:
        """
        Exit if cross-platform consensus has shifted >10% against our position.
        Uses entry context to compare original consensus vs current price.
        """
        ctx = self._entry_contexts.get(pos.condition_id)
        if not ctx:
            return None

        signal_data = ctx.get("signal", {})
        entry_consensus = signal_data.get("cross_platform_consensus", 0.0)
        if entry_consensus <= 0:
            return None

        # Compute implied shift: if we bought YES at 0.40 and consensus was 0.55
        # but now price is 0.35 (market moved against us), check if consensus
        # also shifted. We approximate by checking price drift from entry.
        price_shift = pos.current_price - pos.entry_price
        if pos.side == "YES" and price_shift < -0.10:
            return (
                f"Adverse price shift ({price_shift:+.2f} from entry, side={pos.side})"
            )
        elif pos.side == "NO" and price_shift > 0.10:
            # For NO positions, price going UP is bad (market thinks YES more likely)
            return (
                f"Adverse price shift ({price_shift:+.2f} from entry, side={pos.side})"
            )

        return None

    # ─── Position Closing ─────────────────────────────────────────────────

    def close_position(self, condition_id: str, reason: str,
                       fraction: float = 1.0) -> Optional[dict]:
        """
        Close a position (fully or partially).

        fraction < 1.0: reduce position size, keep remainder open.
        fraction = 1.0: full close, remove from tracking.
        Records realized P&L and updates trade history.
        """
        fraction = max(0.0, min(1.0, fraction))

        with self._lock:
            pos = self._positions.get(condition_id)
            if not pos:
                return None
            # Snapshot values under lock
            exit_price = pos.current_price
            entry_price = pos.entry_price
            total_shares = pos.shares
            total_size = pos.size_usdc
            side = pos.side
            token_id = pos.token_id

        sell_shares = round(total_shares * fraction, 2)
        sell_size = round(total_size * fraction, 2)

        if sell_shares <= 0:
            return None

        # Place sell order
        result = self._api.place_limit_order(
            token_id=token_id,
            side="SELL",
            price=exit_price,
            size=sell_shares,
        )

        pnl = (exit_price - entry_price) * sell_shares

        if fraction >= 1.0:
            # Full close
            with self._lock:
                self._positions.pop(condition_id, None)
                self._trailing_stops.pop(condition_id, None)
                self._exit_schedules.pop(condition_id, None)
                ctx = self._entry_contexts.pop(condition_id, None)

            self._record_closed_trade(pos, exit_price, reason, pnl, fraction, ctx)

            logger.info(
                "POLY POS closed: %s %s | entry=%.4f exit=%.4f | P&L=$%.2f | %s",
                side, condition_id[:8], entry_price, exit_price, pnl, reason,
            )
        else:
            # Partial close — reduce position size
            with self._lock:
                if condition_id in self._positions:
                    p = self._positions[condition_id]
                    p.shares = round(p.shares - sell_shares, 2)
                    p.size_usdc = round(p.size_usdc - sell_size, 2)
                    p.partial_exits.append({
                        "fraction": fraction,
                        "price": exit_price,
                        "pnl": round(pnl, 2),
                        "reason": reason,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                    # Mark the exit schedule target as filled
                    sched = self._exit_schedules.get(condition_id)
                    if sched:
                        target = sched.check_targets(exit_price)
                        if target:
                            sched.mark_filled(target)

            self._record_closed_trade(
                pos, exit_price, reason, pnl, fraction,
                self._entry_contexts.get(condition_id),
            )

            logger.info(
                "POLY POS partial close (%.0f%%): %s %s | P&L=$%.2f | %s | "
                "remaining: %.2f shares, $%.2f",
                fraction * 100, side, condition_id[:8], pnl, reason,
                total_shares - sell_shares, total_size - sell_size,
            )

        # Record realized P&L for circuit breaker
        self._pnl_tracker.record_trade_pnl(pnl)

        self.save()

        return {
            "action": "close" if fraction >= 1.0 else "partial_close",
            "condition_id": condition_id,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "fraction": fraction,
            "shares_sold": sell_shares,
            "pnl_usd": round(pnl, 2),
            "reason": reason,
            "order_result": result,
        }

    # ─── Trade History ────────────────────────────────────────────────────

    def _record_closed_trade(self, pos: PolyPosition, exit_price: float,
                             reason: str, pnl: float, fraction: float,
                             entry_ctx: Optional[dict] = None) -> None:
        """Append completed trade to trade_history.json for analysis."""
        record = {
            "condition_id": pos.condition_id,
            "market_question": pos.market_question,
            "side": pos.side,
            "strategy": pos.strategy,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "shares_sold": round(pos.shares * fraction, 2),
            "size_usdc": round(pos.size_usdc * fraction, 2),
            "pnl_usd": round(pnl, 2),
            "pnl_pct": round(
                (exit_price - pos.entry_price) / pos.entry_price * 100, 2
            ) if pos.entry_price > 0 else 0.0,
            "fraction": fraction,
            "reason": reason,
            "opened_at": pos.opened_at,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "partial_exits": pos.partial_exits,
        }
        if entry_ctx:
            record["entry_context"] = {
                "signal_score": entry_ctx.get("signal", {}).get("score", 0),
                "signal_edge": entry_ctx.get("signal", {}).get("edge_pct", 0),
                "signal_strategy": entry_ctx.get("signal", {}).get("strategy", ""),
                "market_volume_24h": entry_ctx.get("market_snapshot", {}).get("volume_24h", 0),
                "market_spread": entry_ctx.get("market_snapshot", {}).get("spread", 0),
            }

        # Compute hold time
        try:
            opened = datetime.fromisoformat(pos.opened_at.replace("Z", "+00:00"))
            hold_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
            record["hold_hours"] = round(hold_hours, 2)
        except (ValueError, TypeError):
            record["hold_hours"] = 0.0

        # Append to history file
        try:
            history = []
            if os.path.exists(self.TRADE_HISTORY_FILE):
                with open(self.TRADE_HISTORY_FILE) as f:
                    history = json.load(f)
                if not isinstance(history, list):
                    history = []
            history.append(record)

            tmp = self.TRADE_HISTORY_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(history, f, indent=2)
            os.replace(tmp, self.TRADE_HISTORY_FILE)
        except Exception as e:
            logger.warning("Failed to write trade history: %s", e)

    # ─── Portfolio Summary / Analytics ────────────────────────────────────

    def get_portfolio_summary(self) -> dict:
        """
        Comprehensive portfolio analysis including strategy breakdown
        and risk metrics.
        """
        with self._lock:
            positions = list(self._positions.values())
            trailing_stops = dict(self._trailing_stops)

        if not positions:
            return {
                "open_positions": 0,
                "total_value_usdc": 0.0,
                "total_unrealized_pnl": 0.0,
                "total_unrealized_pnl_pct": 0.0,
                "best_position": None,
                "worst_position": None,
                "avg_hold_time_hours": 0.0,
                "strategy_breakdown": {},
                "risk_metrics": {},
                "daily_pnl": self._pnl_tracker.get_today_pnl(),
                "circuit_breaker_active": self._pnl_tracker.is_halted(),
                "positions": [],
            }

        total_value = sum(p.size_usdc for p in positions)
        total_pnl = sum(p.unrealized_pnl for p in positions)

        # Weighted average P&L %
        if total_value > 0:
            total_pnl_pct = sum(
                p.unrealized_pnl_pct * (p.size_usdc / total_value)
                for p in positions
            )
        else:
            total_pnl_pct = 0.0

        # Best / worst
        best = max(positions, key=lambda p: p.unrealized_pnl)
        worst = min(positions, key=lambda p: p.unrealized_pnl)

        # Hold times
        now = datetime.now(timezone.utc)
        hold_times = []
        for p in positions:
            try:
                opened = datetime.fromisoformat(p.opened_at.replace("Z", "+00:00"))
                hold_times.append((now - opened).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass
        avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0

        # Strategy breakdown
        strat_map: dict[str, dict] = {}
        for p in positions:
            s = p.strategy or "unknown"
            if s not in strat_map:
                strat_map[s] = {"count": 0, "pnl": 0.0, "total_hold_hours": 0.0}
            strat_map[s]["count"] += 1
            strat_map[s]["pnl"] += p.unrealized_pnl
        for p in positions:
            s = p.strategy or "unknown"
            try:
                opened = datetime.fromisoformat(p.opened_at.replace("Z", "+00:00"))
                strat_map[s]["total_hold_hours"] += (now - opened).total_seconds() / 3600
            except (ValueError, TypeError):
                pass
        for s, data in strat_map.items():
            data["avg_hold_hours"] = round(
                data["total_hold_hours"] / data["count"], 1
            ) if data["count"] else 0.0
            data["pnl"] = round(data["pnl"], 2)
            del data["total_hold_hours"]

        # Risk metrics
        max_single_pct = max(
            (p.size_usdc / total_value for p in positions), default=0.0
        ) if total_value > 0 else 0.0

        correlated = self._check_correlation_risk()

        nearest_resolution_hours = float("inf")
        for p in positions:
            if p.end_date:
                try:
                    end = datetime.fromisoformat(p.end_date.replace("Z", "+00:00"))
                    hrs = (end - now).total_seconds() / 3600
                    if hrs > 0:
                        nearest_resolution_hours = min(nearest_resolution_hours, hrs)
                except (ValueError, TypeError):
                    pass
        if nearest_resolution_hours == float("inf"):
            nearest_resolution_hours = None

        risk_metrics = {
            "max_single_position_pct": round(max_single_pct, 3),
            "correlated_pairs": len(correlated),
            "correlated_positions": [
                {"id1": c[0][:8], "id2": c[1][:8], "reason": c[2]}
                for c in correlated
            ],
            "time_to_nearest_resolution_hours": (
                round(nearest_resolution_hours, 1)
                if nearest_resolution_hours is not None
                else None
            ),
        }

        # Position details
        pos_details = []
        for p in positions:
            ts = trailing_stops.get(p.condition_id)
            detail = {
                "condition_id": p.condition_id[:12],
                "question": p.market_question[:60],
                "side": p.side,
                "entry": p.entry_price,
                "current": p.current_price,
                "shares": p.shares,
                "size_usdc": round(p.size_usdc, 2),
                "pnl": round(p.unrealized_pnl, 2),
                "pnl_pct": f"{p.unrealized_pnl_pct:.1%}",
                "strategy": p.strategy,
                "stop_loss": p.stop_loss_price,
            }
            if ts:
                detail["trailing_stop"] = round(ts.current_stop, 4)
                detail["trailing_activated"] = ts.activated
                detail["highest_price"] = round(ts.highest_price, 4)
            pos_details.append(detail)

        return {
            "open_positions": len(positions),
            "total_value_usdc": round(total_value, 2),
            "total_unrealized_pnl": round(total_pnl, 2),
            "total_unrealized_pnl_pct": round(total_pnl_pct, 4),
            "best_position": {
                "question": best.market_question[:40],
                "pnl": round(best.unrealized_pnl, 2),
            },
            "worst_position": {
                "question": worst.market_question[:40],
                "pnl": round(worst.unrealized_pnl, 2),
            },
            "avg_hold_time_hours": round(avg_hold, 1),
            "strategy_breakdown": strat_map,
            "risk_metrics": risk_metrics,
            "daily_pnl": round(self._pnl_tracker.get_today_pnl(), 2),
            "circuit_breaker_active": self._pnl_tracker.is_halted(),
            "positions": pos_details,
        }

    def get_total_exposure(self) -> float:
        """Total USD exposure across all open positions."""
        with self._lock:
            return sum(p.size_usdc for p in self._positions.values())

    # ─── Correlation Risk ─────────────────────────────────────────────────

    def _check_correlation_risk(self) -> list[tuple[str, str, str]]:
        """
        Check for correlated positions that amplify risk:
        - Multiple positions in same event (same event_id via entry context)
        - Total exposure to single event > 40% of portfolio
        Returns: list of (condition_id_1, condition_id_2, reason) tuples.
        """
        correlated: list[tuple[str, str, str]] = []

        with self._lock:
            positions = list(self._positions.values())
            contexts = dict(self._entry_contexts)

        # Group by event_id
        event_groups: dict[str, list[PolyPosition]] = {}
        for p in positions:
            ctx = contexts.get(p.condition_id, {})
            signal = ctx.get("signal", {})
            # Event ID might be embedded in the signal's market data
            event_id = signal.get("event_id", "")
            if not event_id:
                # Try to extract from condition_id prefix (common pattern)
                continue
            if event_id not in event_groups:
                event_groups[event_id] = []
            event_groups[event_id].append(p)

        for event_id, group in event_groups.items():
            if len(group) > 1:
                # Flag all pairs within the same event
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        correlated.append((
                            group[i].condition_id,
                            group[j].condition_id,
                            f"Same event ({event_id[:8]})",
                        ))

        # Check for same-side concentration
        yes_positions = [p for p in positions if p.side == "YES"]
        no_positions = [p for p in positions if p.side == "NO"]
        total_exposure = sum(p.size_usdc for p in positions)

        if total_exposure > 0:
            yes_exposure = sum(p.size_usdc for p in yes_positions)
            if yes_exposure / total_exposure > 0.80 and len(yes_positions) > 2:
                # Heavy YES bias
                correlated.append((
                    yes_positions[0].condition_id,
                    yes_positions[-1].condition_id,
                    f"Heavy YES bias ({yes_exposure / total_exposure:.0%})",
                ))
            no_exposure = sum(p.size_usdc for p in no_positions)
            if no_exposure / total_exposure > 0.80 and len(no_positions) > 2:
                correlated.append((
                    no_positions[0].condition_id,
                    no_positions[-1].condition_id,
                    f"Heavy NO bias ({no_exposure / total_exposure:.0%})",
                ))

        return correlated

    # ─── Persistence ──────────────────────────────────────────────────────

    def save(self) -> None:
        """
        Atomic save with backup rotation.
        Saves positions, trailing stops, exit schedules, entry contexts,
        and daily P&L tracker.
        """
        with self._lock:
            data = {
                "_version": 2,
                "positions": {},
                "trailing_stops": {},
                "exit_schedules": {},
                "entry_contexts": {},
                "pnl_tracker": self._pnl_tracker.to_dict(),
            }
            for cid, pos in self._positions.items():
                data["positions"][cid] = {
                    "condition_id": pos.condition_id,
                    "token_id": pos.token_id,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "current_price": pos.current_price,
                    "size_usdc": pos.size_usdc,
                    "shares": pos.shares,
                    "opened_at": pos.opened_at,
                    "market_question": pos.market_question,
                    "end_date": pos.end_date,
                    "strategy": pos.strategy,
                    "stop_loss_price": pos.stop_loss_price,
                    "take_profit_price": pos.take_profit_price,
                    "partial_exits": pos.partial_exits,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                }
            for cid, ts in self._trailing_stops.items():
                data["trailing_stops"][cid] = ts.to_dict()
            for cid, es in self._exit_schedules.items():
                data["exit_schedules"][cid] = es.to_dict()
            for cid, ctx in self._entry_contexts.items():
                data["entry_contexts"][cid] = ctx

        try:
            # Rotate backups before writing
            self._rotate_backups()

            tmp = self.POSITIONS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.POSITIONS_FILE)
        except Exception as e:
            logger.warning("Failed to save poly positions: %s", e)

    def _rotate_backups(self) -> None:
        """Keep last N saves as .bak.1, .bak.2, etc."""
        if not os.path.exists(self.POSITIONS_FILE):
            return
        try:
            # Shift existing backups
            for i in range(self.MAX_BACKUP_COUNT, 1, -1):
                older = f"{self.POSITIONS_FILE}.bak.{i - 1}"
                newer = f"{self.POSITIONS_FILE}.bak.{i}"
                if os.path.exists(older):
                    shutil.copy2(older, newer)

            # Current file becomes .bak.1
            shutil.copy2(self.POSITIONS_FILE, f"{self.POSITIONS_FILE}.bak.1")
        except Exception as e:
            logger.debug("Backup rotation error: %s", e)

    def load(self) -> None:
        """
        Load positions with validation, schema migration, and reconstruction
        of TrailingStop / ExitSchedule objects.
        """
        raw_data = self._load_raw()
        if raw_data is None:
            return

        version = raw_data.get("_version", 1)

        if version >= 2:
            self._load_v2(raw_data)
        else:
            self._load_v1(raw_data)

    def _load_raw(self) -> Optional[dict]:
        """Load raw JSON from file, falling back to backups if needed."""
        for path in self._file_candidates():
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if path != self.POSITIONS_FILE:
                        logger.warning("Loaded poly positions from backup: %s", path)
                    return data
            except FileNotFoundError:
                continue
            except json.JSONDecodeError as e:
                logger.warning("Corrupt JSON in %s: %s", path, e)
                continue
            except Exception as e:
                logger.warning("Could not read %s: %s", path, e)
                continue
        return None

    def _file_candidates(self) -> list[str]:
        """Primary file + backup candidates in priority order."""
        candidates = [self.POSITIONS_FILE]
        for i in range(1, self.MAX_BACKUP_COUNT + 1):
            candidates.append(f"{self.POSITIONS_FILE}.bak.{i}")
        return candidates

    def _load_v2(self, data: dict) -> None:
        """Load version 2 format (full state)."""
        positions_data = data.get("positions", {})
        trailing_data = data.get("trailing_stops", {})
        schedule_data = data.get("exit_schedules", {})
        context_data = data.get("entry_contexts", {})
        pnl_data = data.get("pnl_tracker", {})

        with self._lock:
            self._positions.clear()
            self._trailing_stops.clear()
            self._exit_schedules.clear()
            self._entry_contexts.clear()

            for cid, d in positions_data.items():
                try:
                    self._positions[cid] = self._deserialize_position(d)
                except Exception as e:
                    logger.warning("Skip corrupt position %s: %s", cid[:8], e)

            for cid, d in trailing_data.items():
                if cid in self._positions:
                    try:
                        self._trailing_stops[cid] = TrailingStop.from_dict(d)
                    except Exception as e:
                        logger.warning("Skip corrupt trailing stop %s: %s", cid[:8], e)

            for cid, d in schedule_data.items():
                if cid in self._positions:
                    try:
                        self._exit_schedules[cid] = ExitSchedule.from_dict(d)
                    except Exception as e:
                        logger.warning("Skip corrupt exit schedule %s: %s", cid[:8], e)

            for cid, d in context_data.items():
                if cid in self._positions and isinstance(d, dict):
                    self._entry_contexts[cid] = d

            if pnl_data:
                self._pnl_tracker = DailyPnLTracker.from_dict(pnl_data)

        logger.info(
            "Loaded %d poly positions (v2, %d trailing stops, %d exit schedules)",
            len(self._positions), len(self._trailing_stops), len(self._exit_schedules),
        )

    def _load_v1(self, data: dict) -> None:
        """Load version 1 format (flat dict of positions) with migration."""
        with self._lock:
            self._positions.clear()
            self._trailing_stops.clear()
            self._exit_schedules.clear()
            self._entry_contexts.clear()

            for cid, d in data.items():
                if cid.startswith("_"):
                    continue
                try:
                    pos = self._deserialize_position(d)
                    self._positions[cid] = pos

                    # Reconstruct trailing stop from position data
                    stop_pct = 0.15
                    if pos.entry_price > 0 and pos.stop_loss_price > 0:
                        stop_pct = max(0.01, (pos.entry_price - pos.stop_loss_price) / pos.entry_price)
                    ts = TrailingStop(
                        entry_price=pos.entry_price,
                        initial_stop_pct=stop_pct,
                    )
                    # Advance trailing stop to current price if position is winning
                    if pos.current_price > pos.entry_price:
                        ts.update(pos.current_price)
                    self._trailing_stops[cid] = ts

                except Exception as e:
                    logger.warning("Skip corrupt v1 position %s: %s", cid[:8] if len(cid) >= 8 else cid, e)

        logger.info("Loaded %d poly positions (migrated from v1)", len(self._positions))
        # Re-save in v2 format
        if self._positions:
            self.save()

    def _deserialize_position(self, d: dict) -> PolyPosition:
        """Deserialize a position dict into a PolyPosition, with defaults."""
        return PolyPosition(
            condition_id=d["condition_id"],
            token_id=d["token_id"],
            side=d["side"],
            entry_price=d["entry_price"],
            current_price=d.get("current_price", d["entry_price"]),
            size_usdc=d["size_usdc"],
            shares=d["shares"],
            opened_at=d["opened_at"],
            market_question=d.get("market_question", ""),
            end_date=d.get("end_date", ""),
            strategy=d.get("strategy", ""),
            stop_loss_price=d.get("stop_loss_price", 0),
            take_profit_price=d.get("take_profit_price", 0),
            partial_exits=d.get("partial_exits", []),
            unrealized_pnl=d.get("unrealized_pnl", 0.0),
            unrealized_pnl_pct=d.get("unrealized_pnl_pct", 0.0),
        )
