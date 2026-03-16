"""
PolymarketTrader — Standalone autonomous Polymarket prediction market trader.
=============================================================================
Wraps the existing PolymarketEngine in a BaseTrader-compatible lifecycle
so it can run independently or be managed by the orchestrator.

Handles:
  - Market scanning on configurable interval
  - LLM-based probability estimation
  - News sentiment analysis
  - Smart money tracking
  - Position management with stop-loss and take-profit
  - WebSocket real-time price feeds

Usage (standalone):
  python -m polymarket.trader                # Live mode
  python -m polymarket.trader --paper        # Paper trading
  python -m polymarket.trader --scan         # One-shot scan, exit
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.base_trader import BaseTrader
from core.state_manager import StateManager
from portfolio import Portfolio
from risk_manager import RiskManager
from compounding_engine import CompoundingEngine
from .engine import PolymarketEngine

logger = logging.getLogger(__name__)


class PolymarketTrader(BaseTrader):
    """
    Autonomous Polymarket prediction market trader.

    Scans for mispriced markets using LLM probability estimation,
    news sentiment, and smart money tracking. Executes trades via
    the Polymarket CLOB API on Polygon.
    """

    @property
    def name(self) -> str:
        return "polymarket"

    @property
    def scan_interval_sec(self) -> float:
        return getattr(config, "POLYMARKET_SCAN_INTERVAL_SEC", 300)

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager,
                 state_manager: StateManager, live: bool = True,
                 compounder: CompoundingEngine = None,
                 private_key: str = ""):
        super().__init__(portfolio, risk_manager, state_manager, live)
        self.compounder = compounder or CompoundingEngine(portfolio, risk_manager)
        self._private_key = private_key or config.POLYMARKET_PRIVATE_KEY
        self.engine: Optional[PolymarketEngine] = None
        self._total_trades = 0
        self._day_start_eq = 0.0
        self._last_day = datetime.now(timezone.utc).date()

    def _init_components(self):
        """Initialize the Polymarket engine and its sub-components."""
        if not self._private_key:
            logger.warning("[polymarket] No private key configured — running in read-only mode")

        self.engine = PolymarketEngine(
            private_key=self._private_key,
            chain_id=137,  # Polygon mainnet
        )

        # Start WebSocket feed if configured
        if getattr(config, "POLYMARKET_WS_ENABLED", True):
            try:
                self.engine.start()
            except Exception as e:
                logger.warning("[polymarket] WebSocket start failed: %s", e)

        self._day_start_eq = self.portfolio.equity()
        logger.info("[polymarket] Initialized | key=%s | ws=%s",
                    "set" if self._private_key else "none",
                    "connected" if getattr(config, "POLYMARKET_WS_ENABLED", True) else "disabled")

    def _run_cycle(self):
        """Execute one full Polymarket scan/trade cycle."""
        now_dt = datetime.now(timezone.utc)

        # Day boundary reset
        today = now_dt.date()
        if today != self._last_day:
            self._last_day = today
            self._day_start_eq = self.portfolio.equity()
            logger.info("[polymarket] Daily reset: equity=$%.2f", self._day_start_eq)

        # Update existing positions (price refresh + exits)
        self.engine.update_positions()

        # Run the full scan-and-trade cycle
        try:
            results = self.engine.scan_and_trade(
                self.portfolio, self.risk_mgr, self.compounder)
            if results:
                self._total_trades += len(results)
                for r in results:
                    action = r.get("action", "?")
                    market = r.get("market", "?")
                    side = r.get("side", "?")
                    size = r.get("size_usdc", 0)
                    edge = r.get("edge_pct", 0)
                    logger.info("[polymarket] %s %s '%s' $%.2f edge=%.1f%%",
                                action.upper(), side, market, size, edge)
        except Exception as e:
            logger.warning("[polymarket] Scan error: %s", e)

        # Report state
        self._report_state()

    def _cleanup(self):
        """Shut down WebSocket and save positions."""
        if self.engine:
            try:
                self.engine.stop()
            except Exception as e:
                logger.debug("[polymarket] Cleanup error: %s", e)
        logger.info("[polymarket] Stopped, %d total trades", self._total_trades)

    def get_status(self) -> dict:
        """Return current Polymarket trader status."""
        engine_status = {}
        if self.engine:
            try:
                engine_status = self.engine.get_status()
            except Exception:
                pass

        poly_data = engine_status.get("polymarket", {})
        return {
            "running": self.running,
            "cycle": self._cycle,
            "last_cycle_ms": round(self._last_cycle_ms, 1),
            "total_trades": self._total_trades,
            "positions": poly_data.get("open_positions", 0),
            "total_exposure": poly_data.get("total_exposure", 0),
            "total_pnl": poly_data.get("total_pnl", 0),
            "ws_connected": engine_status.get("ws_connected", False),
            "daily_pnl_usd": round(self.portfolio.equity() - self._day_start_eq, 2),
            "position_details": poly_data.get("positions", []),
        }

    def _report_state(self):
        """Report state to the state manager for dashboard."""
        self.state_mgr.update_trader_state(self.name, self.get_status())

    # ── Manual Close (dashboard command) ────────────────────────────────────

    def close_position(self, condition_id: str, reason: str = "Manual close"):
        """Close a specific position by condition_id (called by orchestrator)."""
        if self.engine:
            try:
                self.engine.positions.close_position(condition_id, reason)
            except Exception as e:
                logger.warning("[polymarket] Manual close failed: %s", e)

    # ── One-shot scan ───────────────────────────────────────────────────────

    def scan_markets(self, min_edge: float = 0.03, min_volume: int = 2000,
                     limit: int = 5) -> list:
        """Scan Polymarket for opportunities without trading. Returns signals."""
        if not self.engine:
            self._init_components()
        markets = self.engine.api_client.get_active_markets(
            limit=100, min_volume=min_volume)
        signals = self.engine.strategies.scan_all(markets, min_edge=min_edge)
        return signals[:limit]


# ── Standalone entry point ──────────────────────────────────────────────────

def main():
    """Run the Polymarket trader as a standalone program."""
    import argparse
    import signal as sig_mod
    import pathlib

    # Load environment
    def _parse_env(path):
        if not path.exists():
            return
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    os.environ[k] = v

    here = pathlib.Path(__file__).resolve().parent.parent  # trader/
    root = here.parent
    _parse_env(root / ".env")
    _parse_env(here / ".env")

    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
        load_dotenv(here / ".env", override=True)
    except ImportError:
        pass

    try:
        from secrets_manager import load_secrets
        load_secrets()
    except Exception:
        pass

    from core.logging_setup import setup_logging
    setup_logging(config.LOG_FILE, config.LOG_LEVEL)

    parser = argparse.ArgumentParser(description="Polymarket Autonomous Trader")
    parser.add_argument("--paper", action="store_true", help="Paper trading mode")
    parser.add_argument("--scan", action="store_true", help="One-shot scan, exit")
    args = parser.parse_args()

    live = not args.paper
    config.PAPER_TRADING = not live

    portfolio = Portfolio(config.INITIAL_CAPITAL)
    risk_mgr = RiskManager(portfolio)
    state_mgr = StateManager()
    compounder = CompoundingEngine(portfolio, risk_mgr)

    trader = PolymarketTrader(
        portfolio, risk_mgr, state_mgr,
        live=live, compounder=compounder)

    if args.scan:
        trader._init_components()
        signals = trader.scan_markets()
        print(f"\nPolymarket — Top {len(signals)} edges:\n")
        for s in signals:
            print(f"  {s.side:<4} '{s.market.question[:55]}' "
                  f"score={s.score:.2f} edge={s.edge_pct*100:.1f}% "
                  f"price={s.target_price:.2%}")
        print()
        return

    def shutdown(*_):
        trader.stop()

    sig_mod.signal(sig_mod.SIGINT, shutdown)
    sig_mod.signal(sig_mod.SIGTERM, shutdown)

    trader.start()


if __name__ == "__main__":
    main()
