"""
Portfolio — Tracks cash, open positions, trade history, and P&L.
Thread-safe for concurrent market scanning.
"""
from __future__ import annotations
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Maximum closed trades kept in memory (older ones archived to trades_archive.jsonl)
_MAX_CLOSED_TRADES_MEMORY = 2_000


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
        self._archive_file = "trades_archive.jsonl"

    # ─────────────────────────────────────────────────────────────────────────
    # Core State
    # ─────────────────────────────────────────────────────────────────────────

    def equity(self) -> float:
        """Total equity = cash + long position values + short unrealized PnL."""
        with self._lock:
            pos_value = 0.0
            for p in self.open_positions.values():
                if p["side"] == "long":
                    pos_value += p["qty"] * p.get("current_price", p["entry_price"])
                else:
                    # Futures short: margin was deducted from cash as collateral.
                    # Add it back here so equity = cash + margin + PnL (correct).
                    # Non-futures shorts don't deduct cash, so margin_usd=0 is safe.
                    pos_value += p.get("margin_usd", 0.0) + p.get("unrealized_pnl", 0.0)
            eq = self.cash + pos_value
            if eq > self.peak_equity:
                self.peak_equity = eq
            return eq

    def has_position(self, asset_id: str) -> bool:
        return asset_id in self.open_positions

    def open_position(self, asset_id: str, side: str, qty: float, price: float,
                      stop_loss: float, take_profit: float, signal: dict) -> dict:
        """Open a new position. Returns the position dict or {} on failure."""
        with self._lock:
            if qty <= 0 or price <= 0:
                logger.warning("Invalid position params: qty=%.8f price=%.4f", qty, price)
                return {}
            cost = qty * price
            if side == "long" and cost > self.cash + 1e-6:
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
        if current_price <= 0:
            logger.debug("Skipping price update for %s: invalid price %.8f", asset_id, current_price)
            return
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
                pos["unrealized_pnl_pct"] = (entry / current_price - 1) * 100 if current_price > 0 else 0

    def close_position(self, asset_id: str, price: float, reason: str = "") -> dict:
        """Close an open position and record the trade."""
        if price <= 0:
            logger.warning("close_position: invalid price %.4f for %s", price, asset_id)
            return {}
        with self._lock:
            if asset_id not in self.open_positions:
                return {}
            pos = self.open_positions.pop(asset_id)
            qty   = pos["qty"]
            side  = pos["side"]
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
            }
            self.closed_trades.append(trade)

            # Update peak equity while holding the lock (cash already updated)
            eq = self.cash + sum(
                p["qty"] * p.get("current_price", p["entry_price"]) if p["side"] == "long"
                else p.get("unrealized_pnl", 0.0)
                for p in self.open_positions.values()
            )
            if eq > self.peak_equity:
                self.peak_equity = eq

            emoji = "✓" if pnl >= 0 else "✗"
            logger.info("%s CLOSE %s %s @ $%.4f | PnL: $%.2f (%.2f%%) | %s",
                        emoji, side.upper(), asset_id, price,
                        pnl, pnl_pct, reason)

            # Archive + prune if too many closed trades in memory
            if len(self.closed_trades) > _MAX_CLOSED_TRADES_MEMORY:
                self._archive_old_trades()

            return trade

    def _archive_old_trades(self):
        """Move the oldest half of closed_trades to the JSONL archive file."""
        cutoff = _MAX_CLOSED_TRADES_MEMORY // 2
        to_archive = self.closed_trades[:cutoff]
        self.closed_trades = self.closed_trades[cutoff:]
        try:
            with open(self._archive_file, "a") as f:
                for trade in to_archive:
                    f.write(json.dumps(trade) + "\n")
            logger.info("Archived %d old trades to %s", len(to_archive), self._archive_file)
        except Exception as e:
            logger.warning("Failed to archive trades: %s", e)

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
        gross_profit = abs(sum(t["pnl_usd"] for t in winning))
        gross_loss   = abs(sum(t["pnl_usd"] for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 1e-8 else 9999.0
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
                    "current_price": pos.get("current_price", pos["entry_price"]),
                    "unrealized_pnl": round(pos.get("unrealized_pnl", 0), 2),
                    "unrealized_pnl_pct": round(pos.get("unrealized_pnl_pct", 0), 2),
                    "stop_loss": pos["stop_loss"],
                    "take_profit": pos["take_profit"],
                    "opened_at": pos["opened_at"],
                }
                for pos in self.open_positions.values()
            ]

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence (atomic write via temp file → rename)
    # ─────────────────────────────────────────────────────────────────────────

    def save(self, filepath: str = config.TRADE_LOG_FILE):
        """Persist portfolio state to JSON atomically (temp file + rename)."""
        state = {
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "peak_equity": self.peak_equity,
            "open_positions": self.open_positions,
            "closed_trades": self.closed_trades,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = filepath + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, filepath)   # Atomic on POSIX — no half-written file
            logger.debug("Portfolio saved to %s", filepath)
        except Exception as e:
            logger.error("Failed to save portfolio: %s", e)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def load(self, filepath: str = config.TRADE_LOG_FILE):
        """Load and validate portfolio state from JSON."""
        try:
            with open(filepath) as f:
                state = json.load(f)

            # Validate required keys
            if "cash" not in state:
                raise ValueError("Missing 'cash' in saved state")

            cash = float(state["cash"])
            if cash < 0:
                logger.error("Corrupted state: negative cash $%.2f — resetting to INITIAL_CAPITAL", cash)
                cash = config.INITIAL_CAPITAL
            self.cash = cash

            initial = float(state.get("initial_capital", config.INITIAL_CAPITAL))
            self.initial_capital = max(initial, 1.0)   # Must be positive

            peak = float(state.get("peak_equity", self.cash))
            self.peak_equity = max(peak, self.cash, 1.0)  # Never below current equity

            self.open_positions = state.get("open_positions", {})
            self.closed_trades  = state.get("closed_trades", [])

            # Validate open positions have required fields
            bad = [k for k, v in self.open_positions.items()
                   if not isinstance(v, dict) or "entry_price" not in v or "side" not in v
                   or float(v.get("entry_price", 0)) <= 0 or float(v.get("qty", 0)) <= 0]
            if bad:
                logger.warning("Removing %d malformed open positions: %s", len(bad), bad)
                for k in bad:
                    self.open_positions.pop(k, None)

            logger.info("Portfolio loaded: equity=$%.2f, %d open, %d closed trades",
                        self.equity(), len(self.open_positions), len(self.closed_trades))
        except FileNotFoundError:
            logger.info("No saved portfolio found, starting fresh")
        except Exception as e:
            logger.error("Failed to load portfolio: %s — starting fresh", e)
