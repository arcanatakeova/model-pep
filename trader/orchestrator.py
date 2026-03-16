"""
Orchestrator — Container managing multiple autonomous trading subsystems.
=========================================================================
Runs SolanaTrader and PolymarketTrader as independent threads with:
  - Unified portfolio and risk management
  - Shared state for the dashboard (bot_state.json)
  - Health monitoring and automatic restart
  - Graceful shutdown coordination
  - Dashboard command processing (manual close, settings)

Usage:
  python orchestrator.py                    # Run both traders (live)
  python orchestrator.py --paper            # Paper trading mode
  python orchestrator.py --solana-only      # Only Solana DEX
  python orchestrator.py --polymarket-only  # Only Polymarket
  python orchestrator.py --scan             # One-shot scan all, exit
  python orchestrator.py --status           # Portfolio status, exit
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

# ── Environment loading (same as original main.py) ──────────────────────────
import pathlib

def _parse_env(path: pathlib.Path):
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

_here = pathlib.Path(__file__).resolve().parent
_root = _here.parent
_parse_env(_root / ".env")
_parse_env(_here / ".env")

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
    load_dotenv(_here / ".env", override=True)
except ImportError:
    pass

try:
    from secrets_manager import load_secrets
    load_secrets()
except Exception:
    pass

import config
from core import StateManager, setup_logging
from portfolio import Portfolio
from risk_manager import RiskManager
from compounding_engine import CompoundingEngine
from solana_trader import SolanaTrader
from polymarket.trader import PolymarketTrader

logger = logging.getLogger(__name__)

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║         AUTONOMOUS TRADING SYSTEM — ORCHESTRATOR v4.0           ║
╠══════════════════════════════════════════════════════════════════╣
║  Mode: {mode:<8}  Capital: ${capital:<12,.0f}                   ║
║  Traders: {traders:<50}  ║
╚══════════════════════════════════════════════════════════════════╝
"""


class Orchestrator:
    """
    Container that manages multiple independent trading subsystems.

    Each trader runs in its own thread with its own scan cadence.
    The orchestrator provides:
      - Shared portfolio (thread-safe)
      - Unified state output (bot_state.json)
      - Health monitoring
      - Dashboard command processing
      - Graceful shutdown coordination
    """

    def __init__(self, live: bool = True,
                 enable_solana: bool = True,
                 enable_polymarket: bool = True):
        self.live = live
        self.running = False
        config.PAPER_TRADING = not live

        # ── Shared infrastructure ───────────────────────────────────────────
        self.portfolio = Portfolio(config.INITIAL_CAPITAL)
        self.risk_mgr = RiskManager(self.portfolio)
        self.compounder = CompoundingEngine(self.portfolio, self.risk_mgr)
        self.state_mgr = StateManager()

        # Load saved state
        self._load_state(live)

        # ── Initialize traders ──────────────────────────────────────────────
        self.traders: dict[str, SolanaTrader | PolymarketTrader] = {}
        self._threads: dict[str, threading.Thread] = {}

        if enable_solana:
            self.traders["solana"] = SolanaTrader(
                portfolio=self.portfolio,
                risk_manager=self.risk_mgr,
                state_manager=self.state_mgr,
                live=live,
                compounder=self.compounder,
            )

        if enable_polymarket and config.POLYMARKET_PRIVATE_KEY:
            self.traders["polymarket"] = PolymarketTrader(
                portfolio=self.portfolio,
                risk_manager=self.risk_mgr,
                state_manager=self.state_mgr,
                live=live,
                compounder=self.compounder,
            )
        elif enable_polymarket and not config.POLYMARKET_PRIVATE_KEY:
            logger.info("Polymarket: no POLYMARKET_PRIVATE_KEY — skipping")

        # ── Orchestrator state ──────────────────────────────────────────────
        self._day_start_eq = self.portfolio.equity()
        self._last_day = datetime.now(timezone.utc).date()

    def _load_state(self, live: bool):
        """Load portfolio and equity curve, handling paper/live mode transitions."""
        _saved_mode = ""
        try:
            with open(config.TRADE_LOG_FILE) as f:
                _saved_mode = json.load(f).get("mode", "")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        _file_exists = os.path.exists(config.TRADE_LOG_FILE)
        _is_paper_data = _file_exists and (_saved_mode != "live")

        if live and _is_paper_data:
            logger.warning("Saved portfolio was paper data — wiping for live start")
            for fname in (config.TRADE_LOG_FILE, "dex_positions.json", "equity_curve.json"):
                try:
                    os.unlink(fname)
                except FileNotFoundError:
                    pass
        else:
            self.portfolio.load()

        self.state_mgr.load_equity_curve()
        self.risk_mgr.reset_daily_loss_tracker()

        # Initialize settings
        if not os.path.exists("settings.json"):
            StateManager._atomic_json("settings.json", {
                "live_mode": True,
                "reset_paper": False,
            })

    # ── Main Loop ───────────────────────────────────────────────────────────

    def start(self):
        """Start all traders and the orchestrator monitoring loop."""
        self.running = True
        trader_names = ", ".join(self.traders.keys()) or "none"
        equity = self.portfolio.equity()

        print(BANNER.format(
            mode="LIVE" if self.live else "PAPER",
            capital=equity,
            traders=trader_names,
        ))

        logger.info("=" * 68)
        logger.info("Orchestrator starting | Mode: %s | Equity: $%.2f | Traders: %s",
                    "LIVE" if self.live else "PAPER", equity, trader_names)
        logger.info("=" * 68)

        # Register signal handlers
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # Remove stale PAUSED file
        if self.live and os.path.exists("PAUSED"):
            os.unlink("PAUSED")

        # Start each trader in its own thread
        for name, trader in self.traders.items():
            t = trader.start_threaded()
            self._threads[name] = t
            logger.info("Started trader: %s (thread=%s)", name, t.name)

        # Orchestrator monitoring loop
        while self.running:
            try:
                self._orchestrator_cycle()
                time.sleep(5)  # 5-second orchestrator cadence
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Orchestrator error: %s", e, exc_info=True)
                time.sleep(10)

        self._cleanup()

    def _orchestrator_cycle(self):
        """Orchestrator heartbeat: state output, health checks, commands."""
        # Day boundary
        today = datetime.now(timezone.utc).date()
        if today != self._last_day:
            self._last_day = today
            self._day_start_eq = self.portfolio.equity()
            self.risk_mgr.reset_daily_loss_tracker()
            logger.info("Daily reset: equity=$%.2f", self._day_start_eq)

        # Process dashboard commands
        self._process_commands()
        self._apply_settings()

        # Compute aggregated state
        equity = self.portfolio.equity()

        # Add Solana DEX value if Solana trader exists
        solana = self.traders.get("solana")
        if solana and isinstance(solana, SolanaTrader):
            equity = solana._compute_equity()

        daily_pnl = equity - self._day_start_eq

        # Update global state
        self.state_mgr.update_global_state({
            "mode": "live" if self.live else "paper",
            "equity": round(equity, 2),
            "cash": round(self.portfolio.cash, 2),
            "initial_capital": self.portfolio.initial_capital,
            "daily_pnl_usd": round(daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl / self._day_start_eq * 100, 2)
                if self._day_start_eq > 0 else 0,
            "peak_equity": round(self.portfolio.peak_equity, 2),
            "open_positions": len(self.portfolio.open_positions),
            "futures_enabled": config.FUTURES_ENABLED,
        })

        # Write aggregated state for dashboard
        self.state_mgr.write_bot_state(self.portfolio)
        self.state_mgr.write_heartbeat({"equity": round(equity, 2)})

        # Equity curve
        self.state_mgr.append_equity(equity)

        # Health checks
        self._check_trader_health()

        # Periodic saves
        self.portfolio.save()

    def _check_trader_health(self):
        """Monitor trader threads and log health status."""
        for name, trader in self.traders.items():
            health = trader.health_check()
            if not health["running"] and self.running:
                thread = self._threads.get(name)
                if thread and not thread.is_alive():
                    logger.error("[%s] Trader thread died — restarting", name)
                    t = trader.start_threaded()
                    self._threads[name] = t
            elif health["stale"]:
                logger.warning("[%s] Trader stale (last cycle %.0fs ago)",
                               name, health["last_cycle_age_sec"])

    def _process_commands(self):
        """Process dashboard commands (manual close, etc.)."""
        try:
            with open("commands.json") as f:
                cmds = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        pending = cmds.get("pending", [])
        if not pending:
            return

        for cmd in pending:
            action = cmd.get("action")
            pid = cmd.get("id", "")
            market = cmd.get("market", "")
            reason = cmd.get("reason", "Dashboard manual close")

            if action != "close":
                logger.warning("Unknown command: %s", cmd)
                continue

            try:
                if market == "dex":
                    solana = self.traders.get("solana")
                    if solana and isinstance(solana, SolanaTrader):
                        solana.close_position(pid, reason)
                        logger.info("Manual close DEX: %s", pid)
                elif market == "polymarket":
                    poly = self.traders.get("polymarket")
                    if poly and isinstance(poly, PolymarketTrader):
                        poly.close_position(pid, reason)
                        logger.info("Manual close Polymarket: %s", pid)
                else:
                    # CEX position
                    pos = self.portfolio.open_positions.get(pid)
                    if pos:
                        current = pos.get("current_price") or pos.get("entry_price", 0)
                        self.portfolio.close_position(pid, current, reason)
                        logger.info("Manual close CEX: %s", pid)
            except Exception as e:
                logger.error("Command error %s: %s", pid, e)

        cmds["pending"] = []
        StateManager._atomic_json("commands.json", cmds)

    def _apply_settings(self):
        """Read and apply dashboard settings."""
        try:
            with open("settings.json") as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        if not settings.get("live_mode", True):
            settings["live_mode"] = True
            StateManager._atomic_json("settings.json", settings)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _shutdown(self, *_):
        logger.info("Shutdown signal received...")
        self.running = False
        for name, trader in self.traders.items():
            trader.stop()

    def _cleanup(self):
        """Wait for all traders to stop and save final state."""
        logger.info("Waiting for traders to stop...")
        for name, thread in self._threads.items():
            thread.join(timeout=30)
            if thread.is_alive():
                logger.warning("[%s] Thread did not stop within 30s", name)

        self.portfolio.save()
        self.state_mgr.save_equity_curve()
        self.compounder.save_state()

        # Print final report
        self._print_report()
        logger.info("Orchestrator stopped cleanly.")

    def _print_report(self):
        """Print final performance report."""
        perf = self.portfolio.performance_summary()
        growth = self.compounder.growth_summary()
        equity = perf["equity"]
        ret = perf["total_return_pct"]

        print(f"\n{'='*70}")
        print(f"  FINAL REPORT  |  Equity: ${equity:,.2f}  |  Return: {ret:+.2f}%")
        print(f"{'='*70}")
        print(f"  Trades: {perf['total_trades']}  |  Win Rate: {perf.get('win_rate_pct',0):.1f}%  "
              f"|  Profit Factor: {perf.get('profit_factor',0):.2f}")
        print(f"  Max DD: {perf.get('max_drawdown_pct',0):.1f}%")

        print(f"\n  Trader Status:")
        for name, trader in self.traders.items():
            health = trader.health_check()
            print(f"    {name:<16} cycles={health['cycle']} errors={health['error_count']}")

        proj = growth["projections"]
        print(f"\n  Projections from ${equity:,.0f}:")
        print(f"    30d:  ${proj['30d_target']:>12,.0f}")
        print(f"    365d: ${proj['365d_target']:>12,.0f}")
        print(f"{'='*70}\n")

    # ── Status queries ──────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Full orchestrator status."""
        return {
            "mode": "live" if self.live else "paper",
            "running": self.running,
            "equity": round(self.portfolio.equity(), 2),
            "traders": {
                name: trader.get_status()
                for name, trader in self.traders.items()
            },
        }


# ── CLI Commands ────────────────────────────────────────────────────────────

def cmd_scan():
    """One-shot scan all markets."""
    from dex_screener import DexScreener

    print("\nScanning all markets...\n")

    # Solana DEX
    print(f"{'─'*72}")
    print(f"  SOLANA DEX — HOT ON-CHAIN TOKENS")
    print(f"{'─'*72}")
    dex = DexScreener()
    tokens = dex.get_multi_chain_opportunities()[:10]
    for t in tokens:
        age = f"{t.age_hours:.0f}h" if t.age_hours else "?"
        print(f"  {t.base_symbol:<12} score={t.score:.3f} "
              f"+{t.price_change_h1:.1f}%/1h vol=${t.volume_h1:,.0f} "
              f"liq=${t.liquidity_usd:,.0f} age={age}")

    # Polymarket
    if config.POLYMARKET_PRIVATE_KEY:
        print(f"\n{'─'*72}")
        print(f"  POLYMARKET — PREDICTION MARKET EDGES")
        print(f"{'─'*72}")
        try:
            poly = PolymarketTrader(
                Portfolio(config.INITIAL_CAPITAL),
                RiskManager(Portfolio(config.INITIAL_CAPITAL)),
                StateManager(),
            )
            signals = poly.scan_markets()
            for s in signals:
                print(f"  {s.side:<4} '{s.market.question[:55]}' "
                      f"score={s.score:.2f} edge={s.edge_pct*100:.1f}%")
        except Exception as e:
            print(f"  Error: {e}")
    print()


def cmd_status():
    """Portfolio status."""
    portfolio = Portfolio()
    portfolio.load()
    compounder = CompoundingEngine(portfolio, None)
    growth = compounder.growth_summary()
    perf = portfolio.performance_summary()

    print(f"\n{'='*70}")
    print(f"  PORTFOLIO STATUS")
    print(f"{'='*70}")
    print(f"  Equity:       ${perf['equity']:>14,.2f}")
    print(f"  Cash:         ${perf['cash']:>14,.2f}")
    print(f"  Total Return: {perf['total_return_pct']:>+13.2f}%")
    print(f"  Trades:       {perf['total_trades']:>14d}")
    print(f"  Win Rate:     {perf.get('win_rate_pct',0):>13.1f}%")

    # DEX positions
    try:
        with open("dex_positions.json") as f:
            dex_pos = json.load(f)
        if dex_pos:
            print(f"\n  Solana DEX ({len(dex_pos)} open):")
            for pair, pos in dex_pos.items():
                print(f"    {pos.get('symbol','?'):<10} "
                      f"entry=${pos.get('entry_price',0):.8f} "
                      f"size=${pos.get('size_usd',0):.2f}")
    except FileNotFoundError:
        pass

    proj = growth["projections"]
    print(f"\n  Projections:")
    print(f"    30d:  ${proj['30d_target']:>12,.0f}")
    print(f"    365d: ${proj['365d_target']:>12,.0f}")
    print(f"{'='*70}\n")


def cmd_report():
    """Full JSON report."""
    portfolio = Portfolio()
    portfolio.load()
    compounder = CompoundingEngine(portfolio, None)
    print(json.dumps({
        "performance": portfolio.performance_summary(),
        "growth": compounder.growth_summary(),
        "open_positions": portfolio.open_positions_summary(),
    }, indent=2))


# ── Entry Point ─────────────────────────────────────────────────────────────

def main():
    import argparse

    setup_logging(config.LOG_FILE, config.LOG_LEVEL)

    parser = argparse.ArgumentParser(
        description="Autonomous Trading System — Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--paper", action="store_true",
                        help="Paper trading mode")
    parser.add_argument("--solana-only", action="store_true",
                        help="Only run Solana DEX trader")
    parser.add_argument("--polymarket-only", action="store_true",
                        help="Only run Polymarket trader")
    parser.add_argument("--scan", action="store_true",
                        help="One-shot scan all markets, exit")
    parser.add_argument("--status", action="store_true",
                        help="Portfolio status, exit")
    parser.add_argument("--report", action="store_true",
                        help="Full JSON report, exit")
    parser.add_argument("--interval", type=int, default=None,
                        help="Override scan interval (seconds)")
    args = parser.parse_args()

    if args.interval:
        config.SCAN_INTERVAL_SEC = args.interval
        config.DEX_SCAN_INTERVAL_SEC = args.interval

    if args.scan:
        cmd_scan()
        return
    if args.status:
        cmd_status()
        return
    if args.report:
        cmd_report()
        return

    # Determine mode
    live = not args.paper
    if live and not any([config.PHANTOM_PRIVATE_KEY,
                         config.POLYMARKET_PRIVATE_KEY,
                         config.BINANCE_API_KEY]):
        print("WARNING: No API keys found — running in PAPER mode.")
        live = False

    # Determine which traders to enable
    enable_solana = not args.polymarket_only
    enable_polymarket = not args.solana_only

    orch = Orchestrator(
        live=live,
        enable_solana=enable_solana,
        enable_polymarket=enable_polymarket,
    )
    orch.start()


if __name__ == "__main__":
    main()
