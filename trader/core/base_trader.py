"""
BaseTrader — Abstract base class for all autonomous trading subsystems.

Each trader (Solana DEX, Polymarket, future CEX) inherits from this and
implements its own scan/trade/exit cycle while sharing:
  - Portfolio state (thread-safe)
  - Risk management
  - State persistence
  - Lifecycle management (start/stop/health)
"""
from __future__ import annotations

import abc
import logging
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class BaseTrader(abc.ABC):
    """
    Abstract autonomous trader with a run loop, health checks, and graceful shutdown.

    Subclasses must implement:
      - name: str property identifying this trader
      - _init_components(): set up trader-specific modules
      - _run_cycle(): one scan/trade/exit iteration
      - _cleanup(): release trader-specific resources
    """

    def __init__(self, portfolio, risk_manager, state_manager, live: bool = True):
        self.portfolio = portfolio
        self.risk_mgr = risk_manager
        self.state_mgr = state_manager
        self.live = live

        self.running = False
        self._cycle = 0
        self._last_cycle_ts: float = 0.0
        self._last_cycle_ms: float = 0.0
        self._error_count = 0
        self._consecutive_errors = 0
        self._max_consecutive_errors = 20
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # ── Abstract interface ──────────────────────────────────────────────────

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short identifier: 'solana', 'polymarket', etc."""
        ...

    @property
    @abc.abstractmethod
    def scan_interval_sec(self) -> float:
        """Seconds between scan cycles."""
        ...

    @abc.abstractmethod
    def _init_components(self):
        """Initialize trader-specific modules (scanners, wallets, etc.)."""
        ...

    @abc.abstractmethod
    def _run_cycle(self):
        """Execute one full scan → evaluate → trade → exit cycle."""
        ...

    @abc.abstractmethod
    def _cleanup(self):
        """Release trader-specific resources on shutdown."""
        ...

    @abc.abstractmethod
    def get_status(self) -> dict:
        """Return current trader status for dashboard/monitoring."""
        ...

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self):
        """Start the trading loop. Call from main thread or as a thread target."""
        self.running = True
        self._init_components()
        logger.info("[%s] Starting | mode=%s", self.name, "LIVE" if self.live else "PAPER")

        while self.running:
            try:
                t0 = time.time()
                self._cycle += 1
                self._run_cycle()
                self._last_cycle_ms = (time.time() - t0) * 1000
                self._last_cycle_ts = time.time()
                self._consecutive_errors = 0
                self._interruptible_sleep(self.scan_interval_sec)
            except Exception as e:
                self._error_count += 1
                self._consecutive_errors += 1
                logger.error("[%s] Cycle error (#%d): %s",
                             self.name, self._consecutive_errors, e, exc_info=True)
                if self._consecutive_errors >= self._max_consecutive_errors:
                    logger.critical(
                        "[%s] %d consecutive errors — halting trader",
                        self.name, self._consecutive_errors)
                    self.running = False
                    break
                backoff = min(5 * self._consecutive_errors, 60)
                self._interruptible_sleep(backoff)

        self._cleanup()
        logger.info("[%s] Stopped cleanly", self.name)

    def start_threaded(self) -> threading.Thread:
        """Start the trader in a daemon thread. Returns the thread."""
        t = threading.Thread(target=self.start, name=f"Trader-{self.name}", daemon=True)
        t.start()
        self._threads.append(t)
        return t

    def stop(self):
        """Signal the trader to stop after the current cycle."""
        logger.info("[%s] Stop requested", self.name)
        self.running = False

    def health_check(self) -> dict:
        """Return health status for monitoring."""
        now = time.time()
        stale = (now - self._last_cycle_ts) > (self.scan_interval_sec * 3) if self._last_cycle_ts > 0 else False
        return {
            "name": self.name,
            "running": self.running,
            "cycle": self._cycle,
            "last_cycle_ms": round(self._last_cycle_ms, 1),
            "last_cycle_age_sec": round(now - self._last_cycle_ts, 1) if self._last_cycle_ts > 0 else -1,
            "stale": stale,
            "error_count": self._error_count,
            "consecutive_errors": self._consecutive_errors,
            "live": self.live,
        }

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _interruptible_sleep(self, seconds: float):
        """Sleep that breaks early when self.running becomes False."""
        end = time.time() + seconds
        while time.time() < end and self.running:
            time.sleep(min(1.0, end - time.time()))

    def _start_background_thread(self, target, name: str) -> threading.Thread:
        """Launch a daemon thread and track it."""
        t = threading.Thread(target=target, name=f"{self.name}-{name}", daemon=True)
        t.start()
        self._threads.append(t)
        logger.info("[%s] Background thread started: %s", self.name, name)
        return t
