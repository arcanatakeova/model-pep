"""
StateManager — Centralized state file I/O for the trading system.

Handles:
  - bot_state.json: dashboard reads this for real-time display
  - heartbeat.json: external watchdogs check this
  - Atomic writes (temp file + rename) to prevent corruption
  - Thread-safe merging of state from multiple traders
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class StateManager:
    """
    Thread-safe state aggregator for multiple trader subsystems.

    Each trader calls `update_trader_state(name, data)` with its own status.
    The orchestrator (or a background thread) calls `write_bot_state()`
    to merge everything into a single bot_state.json for the dashboard.
    """

    def __init__(self, state_dir: str = "."):
        self.state_dir = state_dir
        self._lock = threading.Lock()
        self._trader_states: dict[str, dict] = {}
        self._global_state: dict = {}
        self._equity_curve: list[dict] = []
        self._max_equity_points = 17_280  # 24h at 5s cadence

    # ── Per-trader state updates ────────────────────────────────────────────

    def update_trader_state(self, trader_name: str, state: dict):
        """Called by each trader after every cycle to report its status."""
        with self._lock:
            self._trader_states[trader_name] = {
                **state,
                "updated_at": time.time(),
            }

    def update_global_state(self, state: dict):
        """Update orchestrator-level state (equity, mode, etc.)."""
        with self._lock:
            self._global_state.update(state)

    # ── Aggregated state output ─────────────────────────────────────────────

    def write_bot_state(self, portfolio=None):
        """
        Merge all trader states + global state into bot_state.json.
        Called by the orchestrator every few seconds.
        """
        with self._lock:
            traders = dict(self._trader_states)
            global_state = dict(self._global_state)

        state = {
            "last_update": time.time(),
            "traders": traders,
            **global_state,
        }

        # Add portfolio summary if provided
        if portfolio:
            perf = portfolio.performance_summary()
            state.update({
                "equity": perf.get("equity", 0),
                "cash": perf.get("cash", 0),
                "initial_capital": perf.get("initial_capital", 0),
                "total_trades": perf.get("total_trades", 0),
                "win_rate_pct": perf.get("win_rate_pct", 0),
                "profit_factor": perf.get("profit_factor", 0),
                "max_drawdown_pct": perf.get("max_drawdown_pct", 0),
                "total_pnl_usd": perf.get("total_pnl_usd", 0),
                "recent_trades": list(portfolio.closed_trades)[-50:],
            })

        self._atomic_json(
            os.path.join(self.state_dir, "bot_state.json"), state)

    def write_heartbeat(self, extra: dict = None):
        """Write heartbeat file for external health monitors."""
        hb = {
            "ts": time.time(),
            "alive": True,
            "traders": {},
        }
        with self._lock:
            for name, state in self._trader_states.items():
                hb["traders"][name] = {
                    "running": state.get("running", False),
                    "cycle": state.get("cycle", 0),
                    "last_update": state.get("updated_at", 0),
                }
        if extra:
            hb.update(extra)
        self._atomic_json(
            os.path.join(self.state_dir, "heartbeat.json"), hb)

    # ── Equity curve ────────────────────────────────────────────────────────

    def append_equity(self, equity: float, cycle: int = 0):
        """Append a point to the equity curve."""
        with self._lock:
            self._equity_curve.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "equity": round(equity, 2),
                "cycle": cycle,
            })
            if len(self._equity_curve) > self._max_equity_points:
                self._equity_curve = self._equity_curve[-self._max_equity_points:]

    def save_equity_curve(self):
        """Persist equity curve to disk."""
        with self._lock:
            data = list(self._equity_curve[-10_000:])
        self._atomic_json(
            os.path.join(self.state_dir, "equity_curve.json"), data, indent=2)

    def load_equity_curve(self):
        """Load equity curve from disk."""
        path = os.path.join(self.state_dir, "equity_curve.json")
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    with self._lock:
                        self._equity_curve = data[-self._max_equity_points:]
                    logger.info("Equity curve loaded: %d points", len(self._equity_curve))
        except Exception:
            pass

    # ── Utilities ───────────────────────────────────────────────────────────

    @staticmethod
    def _atomic_json(path: str, data, indent: int = 0):
        """Write JSON atomically: temp file + os.replace."""
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent or None)
            os.replace(tmp, path)
        except Exception as e:
            logger.debug("Failed to write %s: %s", path, e)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    @staticmethod
    def load_json(path: str, default=None):
        """Load a JSON file, returning default on failure."""
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default
