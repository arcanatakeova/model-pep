"""
Polymarket Position Manager
============================
Full lifecycle position tracking with P&L, exits, and persistence.
"""
from __future__ import annotations
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .models import PolyMarket, PolySignal, PolyPosition

logger = logging.getLogger(__name__)


class PolyPositionManager:
    """Manages Polymarket positions with automated exit logic."""

    POSITIONS_FILE = "poly_positions.json"

    def __init__(self, api_client, ws_feed=None):
        self._api = api_client
        self._ws = ws_feed
        self._positions: dict[str, PolyPosition] = {}  # condition_id -> PolyPosition
        self._lock = threading.Lock()
        self.load()

    def open_position(self, signal: PolySignal, size_usdc: float,
                      order_result: dict) -> Optional[PolyPosition]:
        """Record a new position from an executed order."""
        import config

        mkt = signal.market
        token_id = mkt.yes_token_id if signal.side == "YES" else mkt.no_token_id
        shares = round(size_usdc / signal.target_price, 2) if signal.target_price > 0 else 0

        stop_loss_pct = getattr(config, "POLYMARKET_STOP_LOSS_PCT", 0.15)
        take_profit_mult = getattr(config, "POLYMARKET_TAKE_PROFIT_MULT", 2.5)

        pos = PolyPosition(
            condition_id=mkt.condition_id,
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

        with self._lock:
            self._positions[mkt.condition_id] = pos

        self.save()
        logger.info("POLY POS opened: %s %s @ %.4f $%.0f '%s'",
                     signal.side, mkt.condition_id[:8],
                     signal.target_price, size_usdc, mkt.question[:40])
        return pos

    def update_prices(self):
        """Update current prices for all positions."""
        with self._lock:
            positions = list(self._positions.values())

        for pos in positions:
            price = None

            # Try WebSocket first
            if self._ws:
                price = self._ws.get_price(pos.token_id)

            # Fallback to REST
            if price is None:
                try:
                    mid = self._api.get_midpoint(pos.token_id)
                    if mid is not None:
                        price = mid
                except Exception:
                    pass

            if price is not None:
                with self._lock:
                    if pos.condition_id in self._positions:
                        p = self._positions[pos.condition_id]
                        p.current_price = price
                        if p.entry_price > 0:
                            p.unrealized_pnl = (price - p.entry_price) * p.shares
                            p.unrealized_pnl_pct = (price - p.entry_price) / p.entry_price

    def check_exits(self) -> list[tuple[PolyPosition, str]]:
        """Check all positions for exit conditions. Returns (position, reason) pairs."""
        exits: list[tuple[PolyPosition, str]] = []

        with self._lock:
            positions = list(self._positions.values())

        for pos in positions:
            reason = self._check_stop_loss(pos)
            if not reason:
                reason = self._check_take_profit(pos)
            if not reason:
                reason = self._check_resolution_exit(pos)
            if not reason:
                reason = self._check_time_decay_exit(pos)
            if reason:
                exits.append((pos, reason))

        return exits

    def close_position(self, condition_id: str, reason: str) -> Optional[dict]:
        """Close a position and return trade result."""
        with self._lock:
            pos = self._positions.pop(condition_id, None)

        if not pos:
            return None

        # Place sell order
        result = self._api.place_limit_order(
            token_id=pos.token_id,
            side="SELL",
            price=pos.current_price,
            size=pos.shares,
        )

        pnl = (pos.current_price - pos.entry_price) * pos.shares

        logger.info("POLY POS closed: %s %s | entry=%.4f exit=%.4f | P&L=$%.2f | %s",
                     pos.side, condition_id[:8],
                     pos.entry_price, pos.current_price, pnl, reason)

        self.save()
        return {
            "action": "close",
            "condition_id": condition_id,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": pos.current_price,
            "pnl_usd": round(pnl, 2),
            "reason": reason,
            "order_result": result,
        }

    def get_portfolio_summary(self) -> dict:
        """Get summary of all open positions."""
        with self._lock:
            positions = list(self._positions.values())

        total_value = sum(p.size_usdc for p in positions)
        total_pnl = sum(p.unrealized_pnl for p in positions)

        return {
            "open_positions": len(positions),
            "total_value_usdc": round(total_value, 2),
            "total_unrealized_pnl": round(total_pnl, 2),
            "positions": [
                {
                    "question": p.market_question[:60],
                    "side": p.side,
                    "entry": p.entry_price,
                    "current": p.current_price,
                    "pnl": round(p.unrealized_pnl, 2),
                    "pnl_pct": f"{p.unrealized_pnl_pct:.1%}",
                    "strategy": p.strategy,
                }
                for p in positions
            ],
        }

    def get_total_exposure(self) -> float:
        """Total USD exposure across all positions."""
        with self._lock:
            return sum(p.size_usdc for p in self._positions.values())

    # ─── Exit Rules ────────────────────────────────────────────────────────

    def _check_stop_loss(self, pos: PolyPosition) -> Optional[str]:
        """Stop loss: exit if price drops below threshold."""
        if pos.stop_loss_price > 0 and pos.current_price <= pos.stop_loss_price:
            return f"Stop loss hit ({pos.current_price:.4f} <= {pos.stop_loss_price:.4f})"
        return None

    def _check_take_profit(self, pos: PolyPosition) -> Optional[str]:
        """Take profit: exit if price reaches target."""
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
        """Exit if position has been held too long with no movement."""
        import config
        max_hours = getattr(config, "POLYMARKET_MAX_HOLD_HOURS", 168)

        try:
            opened = datetime.fromisoformat(pos.opened_at.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
        except (ValueError, TypeError):
            return None

        if age_hours >= max_hours:
            return f"Max hold time ({age_hours:.0f}h >= {max_hours}h)"

        # Stale: held > 48h with no movement toward thesis
        if age_hours >= 48 and abs(pos.unrealized_pnl_pct) < 0.02:
            return f"Stale position ({age_hours:.0f}h, {pos.unrealized_pnl_pct:.1%} move)"

        return None

    # ─── Persistence ───────────────────────────────────────────────────────

    def save(self):
        """Save positions to disk (atomic write)."""
        with self._lock:
            data = {}
            for cid, pos in self._positions.items():
                data[cid] = {
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
                }
        try:
            tmp = self.POSITIONS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.POSITIONS_FILE)
        except Exception as e:
            logger.warning("Failed to save poly positions: %s", e)

    def load(self):
        """Load positions from disk."""
        try:
            with open(self.POSITIONS_FILE) as f:
                data = json.load(f)
            with self._lock:
                for cid, d in data.items():
                    self._positions[cid] = PolyPosition(
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
                    )
            logger.info("Loaded %d poly positions from disk", len(self._positions))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Could not load poly positions: %s", e)
