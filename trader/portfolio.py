"""
Portfolio — Tracks cash, open positions, trade history, and P&L.
Thread-safe for concurrent market scanning.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)


class Portfolio:
    """
    Manages the paper (or live) trading portfolio state.
    All monetary values in USD.
    """

    def __init__(self, initial_capital: float = config.INITIAL_CAPITAL):
        self.cash = initial_capital
        self.initial_capital = initial_capital
        self.open_positions: dict[str, dict] = {}   # asset_id → position dict
        self.closed_trades: list[dict] = []
        self.peak_equity = initial_capital
        self._lock = threading.Lock()

    # ─────────────────────────────────────────────────────────────────────────
    # Core State
    # ─────────────────────────────────────────────────────────────────────────

    def equity(self) -> float:
        """Total equity = cash + value of open positions."""
        with self._lock:
            pos_value = sum(
                p["qty"] * p["current_price"]
                for p in self.open_positions.values()
                if p["side"] == "long"
            )
            return self.cash + pos_value

    def has_position(self, asset_id: str) -> bool:
        return asset_id in self.open_positions

    def open_position(self, asset_id: str, side: str, qty: float, price: float,
                      stop_loss: float, take_profit: float, signal: dict) -> dict:
        """Open a new position. Returns the position dict."""
        with self._lock:
            cost = qty * price
            if side == "long" and cost > self.cash:
                logger.warning("Insufficient cash: need $%.2f, have $%.2f", cost, self.cash)
                return {}
            if side == "long":
                self.cash -= cost

            position = {
                "asset_id": asset_id,
                "market": signal.get("market", "unknown"),
                "symbol": signal.get("symbol", asset_id),
                "side": side,
                "qty": qty,
                "entry_price": price,
                "current_price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "trailing_stop": stop_loss,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "signal_score": signal.get("score", 0),
                "conviction": signal.get("conviction", 0),
                "reasons": signal.get("reasons", []),
                "unrealized_pnl": 0.0,
                "unrealized_pnl_pct": 0.0,
            }
            self.open_positions[asset_id] = position
            logger.info("OPEN %s %s: %.6f @ $%.4f (SL: $%.4f, TP: $%.4f)",
                        side.upper(), asset_id, qty, price, stop_loss, take_profit)
            return position

    def update_position_price(self, asset_id: str, current_price: float):
        """Update mark-to-market price and unrealized P&L."""
        with self._lock:
            if asset_id not in self.open_positions:
                return
            pos = self.open_positions[asset_id]
            pos["current_price"] = current_price
            entry = pos["entry_price"]
            qty   = pos["qty"]
            if pos["side"] == "long":
                pos["unrealized_pnl"] = (current_price - entry) * qty
                pos["unrealized_pnl_pct"] = (current_price / entry - 1) * 100
            else:
                pos["unrealized_pnl"] = (entry - current_price) * qty
                pos["unrealized_pnl_pct"] = (entry / current_price - 1) * 100

    def close_position(self, asset_id: str, price: float, reason: str = "") -> dict:
        """Close an open position and record the trade."""
        with self._lock:
            if asset_id not in self.open_positions:
                return {}
            pos = self.open_positions.pop(asset_id)
            qty  = pos["qty"]
            side = pos["side"]
            entry = pos["entry_price"]

            if side == "long":
                proceeds = qty * price
                self.cash += proceeds
                pnl = (price - entry) * qty
            else:
                pnl = (entry - price) * qty
                self.cash += pnl  # Credit/debit for short

            pnl_pct = pnl / (entry * qty) * 100 if entry * qty > 0 else 0

            trade = {
                **pos,
                "exit_price": price,
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "pnl_usd": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2),
                "close_reason": reason,
                "duration_bars": None,  # Could calculate from timestamps
            }
            self.closed_trades.append(trade)

            tag = "WIN" if pnl >= 0 else "LOSS"
            logger.info("[%s] CLOSE %s %s @ $%.4f | PnL: $%.2f (%.2f%%) | %s",
                        tag, side.upper(), asset_id, price,
                        pnl, pnl_pct, reason)

            # Update peak equity
            eq = self.cash
            if eq > self.peak_equity:
                self.peak_equity = eq
            return trade

    # ─────────────────────────────────────────────────────────────────────────
    # Analytics
    # ─────────────────────────────────────────────────────────────────────────

    def performance_summary(self) -> dict:
        """Compute portfolio performance statistics."""
        closed = self.closed_trades
        eq     = self.equity()

        total_return = (eq - self.initial_capital) / self.initial_capital * 100

        if not closed:
            return {
                "equity": round(eq, 2),
                "cash": round(self.cash, 2),
                "total_return_pct": round(total_return, 2),
                "total_trades": 0,
                "open_positions": len(self.open_positions),
            }

        winning = [t for t in closed if t["pnl_usd"] > 0]
        losing  = [t for t in closed if t["pnl_usd"] <= 0]
        win_rate = len(winning) / len(closed) * 100 if closed else 0
        avg_win  = sum(t["pnl_pct"] for t in winning) / len(winning) if winning else 0
        avg_loss = sum(t["pnl_pct"] for t in losing) / len(losing) if losing else 0
        profit_factor = (
            abs(sum(t["pnl_usd"] for t in winning)) /
            abs(sum(t["pnl_usd"] for t in losing))
            if losing and sum(t["pnl_usd"] for t in losing) != 0 else float("inf")
        )
        total_pnl = sum(t["pnl_usd"] for t in closed)
        peak = self.peak_equity
        max_dd = (peak - eq) / peak * 100 if peak > 0 else 0

        return {
            "equity": round(eq, 2),
            "cash": round(self.cash, 2),
            "initial_capital": self.initial_capital,
            "total_return_pct": round(total_return, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "total_trades": len(closed),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate_pct": round(win_rate, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "open_positions": len(self.open_positions),
            "markets": self._breakdown_by_market(),
        }

    def _breakdown_by_market(self) -> dict:
        result = {}
        for t in self.closed_trades:
            m = t.get("market", "unknown")
            if m not in result:
                result[m] = {"trades": 0, "pnl_usd": 0.0, "wins": 0}
            result[m]["trades"] += 1
            result[m]["pnl_usd"] += t["pnl_usd"]
            if t["pnl_usd"] > 0:
                result[m]["wins"] += 1
        for m in result:
            n = result[m]["trades"]
            result[m]["win_rate_pct"] = round(result[m]["wins"] / n * 100, 1) if n > 0 else 0
            result[m]["pnl_usd"] = round(result[m]["pnl_usd"], 2)
        return result

    def open_positions_summary(self) -> list[dict]:
        """Return a clean summary of all open positions."""
        with self._lock:
            return [
                {
                    "asset_id": pos["asset_id"],
                    "symbol": pos["symbol"],
                    "market": pos["market"],
                    "side": pos["side"],
                    "qty": round(pos["qty"], 8),
                    "entry_price": pos["entry_price"],
                    "current_price": pos["current_price"],
                    "unrealized_pnl": round(pos.get("unrealized_pnl", 0), 2),
                    "unrealized_pnl_pct": round(pos.get("unrealized_pnl_pct", 0), 2),
                    "stop_loss": pos["stop_loss"],
                    "take_profit": pos["take_profit"],
                    "opened_at": pos["opened_at"],
                }
                for pos in self.open_positions.values()
            ]

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────────────────────

    def save(self, filepath: str = config.TRADE_LOG_FILE):
        """Persist portfolio state to JSON."""
        state = {
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "peak_equity": self.peak_equity,
            "open_positions": self.open_positions,
            "closed_trades": self.closed_trades,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(filepath, "w") as f:
            json.dump(state, f, indent=2)
        logger.debug("Portfolio saved to %s", filepath)

    def load(self, filepath: str = config.TRADE_LOG_FILE):
        """Load portfolio state from JSON."""
        try:
            with open(filepath) as f:
                state = json.load(f)
            self.cash = state["cash"]
            self.initial_capital = state.get("initial_capital", config.INITIAL_CAPITAL)
            self.peak_equity = state.get("peak_equity", self.cash)
            self.open_positions = state.get("open_positions", {})
            self.closed_trades = state.get("closed_trades", [])
            logger.info("Portfolio loaded: equity=$%.2f, %d open, %d closed trades",
                        self.equity(), len(self.open_positions), len(self.closed_trades))
        except FileNotFoundError:
            logger.info("No saved portfolio found, starting fresh")
        except Exception as e:
            logger.error("Failed to load portfolio: %s", e)
