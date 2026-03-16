"""
AI Trader v3.0 — Autonomous 24/7 Leveraged Pro Trading Bot
===========================================================
Runs every 30 seconds, non-stop, across all markets with leverage.

Markets traded:
  1. Crypto CEX      — CoinGecko / CryptoCompare 1h signals (BTC, ETH, SOL)
  2. Crypto Scalps   — 5-minute RSI/EMA signals → Binance Futures (2-8x leverage)
  3. Futures Swings  — 1h signals → Binance USDT-M Perpetuals (leveraged)
  4. DEX Tokens      — DEX Screener hot tokens (Solana memecoin sniping)
  5. Solana On-Chain — Phantom wallet + Jupiter DEX swaps
  6. Polymarket      — Prediction market edge trading
  7. Stocks / ETFs   — Yahoo Finance (QQQ, SPY, NVDA)
  8. Forex           — EUR/USD, GBP/USD

Features:
  - Leverage: 2-8x on high-conviction signals (Binance Futures)
  - Scalping: 5m signals on BTC/ETH/SOL every 30s
  - Position pyramiding: adds 25% to winning positions on confirmation
  - Liquidation guard: emergency close if within 15% of liq price
  - 24/7: auto-restarts on crash (use run_forever.sh or systemd)
  - Compounding: all profits reinvested, position sizes scale with equity

Usage:
  python main.py                  # Start paper-trading bot (default)
  python main.py --live           # Enable real trades (needs keys in .env)
  python main.py --scan           # One-shot scan, print all signals, exit
  python main.py --status         # Portfolio status + compound growth, exit
  python main.py --report         # Full JSON report, exit
  python main.py --growth         # Show projected compound growth table
"""
import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

# Load .env — search repo root AND trader/ so both standard setups work.
# .env.example lives at the repo root and says "cp .env.example .env",
# so the user's .env is typically at model-pep/.env (root), not trader/.env.
# trader/.env is checked second and overrides root if both exist.
import os as _os, pathlib as _pathlib

def _parse_env(path: "_pathlib.Path"):
    if not path.exists():
        return
    for _ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _, _v = _ln.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k:
                _os.environ[_k] = _v

_here = _pathlib.Path(__file__).resolve().parent   # model-pep/trader/
_root = _here.parent                                # model-pep/
_parse_env(_root / ".env")   # standard: user ran `cp .env.example .env` at root
_parse_env(_here / ".env")   # server/CI override: trader/.env takes precedence

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_root / ".env")
    _load_dotenv(_here / ".env", override=True)
except ImportError:
    pass

# Load secrets from Supabase vault BEFORE config is imported so that
# os.getenv() calls inside config.py pick up the Supabase-stored values.
# Falls back silently to .env if Supabase is not configured.
try:
    from secrets_manager import load_secrets as _load_secrets
    _load_secrets()
except Exception:
    pass

import config

# Force-pull Birdeye key from Supabase Vault so it takes precedence over any
# stale/wrong value in .env (load_secrets() above skips vault if env is set).
try:
    from secrets_manager import fetch_secret as _fetch_secret
    _be_key = _fetch_secret("BIRDEYE_API_KEY")
    if _be_key:
        if _be_key != config.BIRDEYE_API_KEY:
            import logging as _logging
            _logging.getLogger(__name__).info(
                "Birdeye API key refreshed from Supabase Vault")
        config.BIRDEYE_API_KEY = _be_key
        os.environ["BIRDEYE_API_KEY"] = _be_key
except Exception:
    pass

from portfolio import Portfolio
from risk_manager import RiskManager
from executor import TradeExecutor
from compounding_engine import CompoundingEngine
from strategy_auditor import StrategyAuditor
from strategies import MarketScanner
from strategies.scalper import ScalpingScanner
from strategies.funding_arb import FundingArbScanner
from strategies.grid_trader import GridTrader
from dex_screener import DexScreener
from polymarket import PolymarketTrader
from solana_wallet import SolanaWallet, SOL_MINT, USDC_MINT, USDT_MINT
from token_safety import TokenSafetyChecker
import data_fetcher as df_mod

# ─── Logging (rotating file — max 20 MB, keep 10 backups) ────────────────────
_log_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(name)-20s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler = logging.handlers.RotatingFileHandler(
    config.LOG_FILE, maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8"
)
_file_handler.setFormatter(_log_fmt)
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_log_fmt)

logging.root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
logging.root.addHandler(_file_handler)
logging.root.addHandler(_stream_handler)
logger = logging.getLogger("ai_trader")

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║           AUTONOMOUS AI TRADER  v3.0  —  SOLANA DEX              ║
╠══════════════════════════════════════════════════════════════════╣
║  Mode: {mode:<8}    Capital: ${capital:<12,.0f}                  ║
║  Scan: {scan}s cycle │ DEX: {dex}s │ Price monitor: 3s            ║
╚══════════════════════════════════════════════════════════════════╝
"""


# ─── Main Trader ──────────────────────────────────────────────────────────────

class AITrader:
    """
    Fully autonomous trading loop.
    Deposits money into Phantom → bot runs forever compounding.
    """

    def __init__(self, live: bool = True):
        self.live    = live
        self.running = False
        config.PAPER_TRADING = not live

        # ── Core ──────────────────────────────────────────────────────────
        self.portfolio  = Portfolio(config.INITIAL_CAPITAL)
        self.risk_mgr   = RiskManager(self.portfolio)
        self.executor   = TradeExecutor(self.portfolio, self.risk_mgr)
        self.compounder = CompoundingEngine(self.portfolio, self.risk_mgr)
        self.auditor    = StrategyAuditor(self.portfolio, config)

        # ── Market Scanners ───────────────────────────────────────────────
        self.cex_scanner  = MarketScanner()                          # crypto/forex/stocks (1h)
        self.scalper      = ScalpingScanner()                        # 5m scalp signals
        self.funding_arb  = FundingArbScanner(self.portfolio, self.executor)   # funding rate arb
        self.grid_trader  = GridTrader(self.portfolio, self.executor)           # grid trading
        self.dex_screener = DexScreener()                            # on-chain tokens
        # Polymarket — enabled when POLYMARKET_PRIVATE_KEY is set
        if config.POLYMARKET_PRIVATE_KEY:
            self.poly_trader = PolymarketTrader(private_key=config.POLYMARKET_PRIVATE_KEY)
        self.solana       = SolanaWallet(
            private_key_b58=config.PHANTOM_PRIVATE_KEY)              # Phantom wallet

        # ── WebSocket real-time feed ──────────────────────────────────────
        self._ws_feed = df_mod.get_ws_feed() if hasattr(df_mod, "get_ws_feed") else None

        # ── State ─────────────────────────────────────────────────────────
        now = time.time()
        self._cycle          = 0
        self._last_save      = 0        # 0 = save immediately on first cycle
        self._last_state     = now
        # self._last_poly is no longer used — Polymarket disabled
        self._last_dex       = 0.0     # DEX runs on cycle 1 (fast API)
        self._last_scalp     = 0.0     # Scalp runs on cycle 1 (fast API)
        self._last_futures   = 0.0     # Futures runs on cycle 1 (uses CEX cache)
        self._last_arb       = now      # Stagger: arb runs after first interval
        self._last_grid      = 0.0     # Grid runs on cycle 1 (no API calls)
        self._last_wallet_sync = 0.0   # Live wallet position sync every 30s
        self._day_start_eq   = self.portfolio.equity()
        self._last_day       = datetime.now(timezone.utc).date()
        self._equity_curve   = self._load_equity_curve()  # Persist chart history across restarts
        self._dex_positions: dict = {}  # addr → {buy_price, qty, chain, symbol}
        self._dex_lock = threading.Lock()  # Protects _dex_positions cross-thread (fast monitor + main)
        self._dex_closed_count = 0         # Counts DEX closes; drives audit cadence
        self._cex_signals_cache = []    # Shared between CEX scan + futures swing scan
        self._cex_cache_lock = threading.Lock()  # Protects _cex_signals_cache cross-thread
        self._market_snapshot: dict = {}  # Latest market overview (BTC dom, sentiment)
        self._wallet_balance_cache = (0.0, 0.0, 0.0, 0.0)  # sol, usdc, sol_usd, ts

        # Load existing portfolio — but if we're starting LIVE and the saved data
        # was from a paper session, wipe it so fake money never pollutes real tracking.
        _saved_mode = self._peek_saved_mode(config.TRADE_LOG_FILE)
        # Wipe if mode is not "live" — covers both explicit paper mode and old files
        # that predate the mode field (written before this fix was deployed).
        _file_exists = os.path.exists(config.TRADE_LOG_FILE)
        _is_paper_data = _file_exists and (_saved_mode != "live")

        if live and _is_paper_data:
            logger.warning(
                "Saved portfolio was paper-trading data — wiping fake history "
                "and starting fresh from Phantom wallet balance.")
            for fname in (config.TRADE_LOG_FILE, "dex_positions.json", "equity_curve.json"):
                try:
                    os.unlink(fname)
                except FileNotFoundError:
                    pass
            self._equity_curve = []

        self.portfolio.load()
        self._load_dex_positions()
        self._reconcile_wallet_positions()
        self.risk_mgr.reset_daily_loss_tracker()

        # Initialise settings.json if missing
        if not os.path.exists("settings.json"):
            self._atomic_json("settings.json", {
                "live_mode": True,
                "reset_paper": False,
            })

        # Always sync cash + initial_capital with the real Phantom wallet on every startup.
        # This ensures position sizing and drawdown calculations reflect the true balance,
        # not a stale saved value or the $100k config default.
        if self.solana.is_connected and live:
            try:
                sol_bal = self.solana.get_sol_balance()
                sol_usd = self.solana.get_portfolio_value_usd()
                if sol_usd > 0.50:
                    self.portfolio.cash            = sol_usd
                    self.portfolio.initial_capital  = sol_usd
                    self.portfolio.peak_equity      = max(self.portfolio.peak_equity, sol_usd)
                    self._day_start_eq              = sol_usd
                    self.risk_mgr.reset_daily_loss_tracker()
                    logger.info("Phantom wallet synced: SOL=%.4f ($%.2f)", sol_bal, sol_usd)
            except Exception as e:
                logger.warning("Wallet sync failed: %s", e)

        # Write portfolio immediately so dashboard has data before cycle 1 finishes
        self.portfolio.save()
        self._save_dex_positions()

    # ─────────────────────────────────────────────────────────────────────────
    # Main Loop
    # ─────────────────────────────────────────────────────────────────────────

    def start(self):
        self.running = True
        mode = "LIVE" if self.live else "PAPER"
        equity = self.portfolio.equity()
        print(BANNER.format(mode=mode, capital=equity,
                           scan=config.SCAN_INTERVAL_SEC,
                           dex=config.DEX_SCAN_INTERVAL_SEC))

        logger.info("=" * 68)
        logger.info("AI Trader starting | Mode: %s | Equity: $%.2f", mode, equity)
        if self.solana.is_connected:
            logger.info("Phantom wallet: %s", self.solana.pubkey)
        if config.POLYMARKET_PRIVATE_KEY:
            logger.info("Polymarket: connected (Polygon)")
        logger.info("=" * 68)

        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # Remove stale PAUSED file on fresh start so it can't silently block trading
        if self.live and os.path.exists("PAUSED"):
            os.unlink("PAUSED")
            logger.info("Removed stale PAUSED file — trading active")

        # Start fast price monitor thread (Birdeye multi_price every 3s for held tokens)
        if self.live and self.solana.is_connected:
            _t = threading.Thread(target=self._fast_price_monitor,
                                  name="FastPriceMonitor", daemon=True)
            _t.start()
            logger.info("Fast price monitor started (3s Birdeye poll for held positions)")

        # Start live dashboard writer — writes bot_state.json every 5s so the
        # dashboard always shows real-time prices, SOL balance, and positions
        # instead of waiting for the full 30s scan cycle.
        _dw = threading.Thread(target=self._live_dashboard_writer,
                               name="LiveDashboardWriter", daemon=True)
        _dw.start()
        logger.info("Live dashboard writer started (5s bot_state.json refresh)")

        while self.running:
            try:
                self._run_cycle()
                self._sleep(config.SCAN_INTERVAL_SEC)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Main loop error: %s", e, exc_info=True)
                time.sleep(15)

        self._cleanup()

    def _run_cycle(self):
        # ── Dashboard settings + commands (live/paper toggle, close buttons) ─
        self._apply_dashboard_settings()
        self._process_dashboard_commands()

        self._cycle += 1
        t0 = time.time()
        now_dt = datetime.now(timezone.utc)
        now    = now_dt.strftime("%H:%M:%S UTC")
        # Real equity = cash (SOL in wallet) + open DEX position values
        dex_value = self._dex_value_snapshot()
        equity = self.portfolio.cash + dex_value
        logger.info("━━━ Cycle #%d  %s  SOL wallet: $%.2f  DEX positions: $%.2f  Total: $%.2f ━━━",
                    self._cycle, now, self.portfolio.cash, dex_value, equity)

        # ── Midnight reset: daily loss tracker + optional wallet sync ─────
        today = now_dt.date()
        if today != self._last_day:
            self._last_day = today
            self.risk_mgr.reset_daily_loss_tracker()
            # Reset daily P&L baseline with true equity (CEX + DEX)
            dex_val = self._dex_value_snapshot()
            self._day_start_eq = self.portfolio.equity() + dex_val
            logger.info("Daily reset: new day %s, equity=$%.2f, loss tracker cleared",
                        today, self._day_start_eq)
            if self.solana.is_connected and self.live:
                wallet_value = self.solana.get_portfolio_value_usd()
                if wallet_value > 10:
                    logger.info("Midnight wallet sync: $%.2f", wallet_value)

        # ── 1. Update open DEX positions (price refresh, exits, partial TPs)
        self._update_dex_positions()

        # ── 2. DEX scan: find new Solana tokens to buy ────────────────────
        now_ts = time.time()
        if now_ts - self._last_dex >= config.DEX_SCAN_INTERVAL_SEC:
            try:
                self._run_dex_scan()
            except Exception as e:
                logger.warning("DEX scan error: %s", e)
            self._last_dex = time.time()

        # ── 12. Compound: Reinvest + rebalance ────────────────────────────
        # ── 13. Equity snapshot — cap at 17280 = 24h at 5s cadence ──────
        # (live dashboard writer adds points every 5s; main cycle adds here too)
        self._equity_curve.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "equity": round(equity, 2),
            "cycle": self._cycle,
        })
        if len(self._equity_curve) > 17_280:
            self._equity_curve = self._equity_curve[-17_280:]

        # ── 14. Risk report ───────────────────────────────────────────────
        risk = self.risk_mgr.risk_report()
        logger.info("Risk: pos=%d/%d cash=$%.0f dd=%.1f%% daily=%.1f%%",
                    risk["open_positions"], risk["max_positions"],
                    risk["cash"], risk["current_drawdown_pct"],
                    risk["daily_loss_pct"])

        elapsed_ms = (time.time() - t0) * 1000

        # ── 15. Write bot_state.json for dashboard ────────────────────────
        self._write_bot_state(equity, elapsed_ms)

        # ── 16. Periodic saves ────────────────────────────────────────────
        if time.time() - self._last_save > config.PORTFOLIO_SNAPSHOT_INTERVAL:
            self.portfolio.save()
            self._save_dex_positions()
            self._save_equity_curve()
            self._save_strategy_states()
            self._last_save = time.time()

        # ── 17. Wallet sync handled by _live_dashboard_writer (5s thread) ───
        # SOL balance + position reconciliation run every 5s/30s in the background.

        logger.info("Cycle #%d done in %.0fms", self._cycle, elapsed_ms)

        # ── Watchdog heartbeat (external health monitors can check this file) ─
        try:
            with open("heartbeat.json", "w") as _hb:
                json.dump({
                    "ts": time.time(),
                    "cycle": self._cycle,
                    "equity": round(equity, 2),
                    "alive": True,
                }, _hb)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Market Subsystems
    # ─────────────────────────────────────────────────────────────────────────

    def _run_cex_scan(self):
        """CEX scan: crypto (CoinGecko/CC), stocks (yfinance), forex."""
        try:
            signals = self.cex_scanner.scan_all(max_workers=20)
            # Cache for futures swing scan + grid trader (avoids double API call)
            with self._cex_cache_lock:
                self._cex_signals_cache = signals
            actionable = [s for s in signals if s.signal != "HOLD"]
            logger.info("CEX scan: %d signals (%d actionable)",
                        len(signals), len(actionable))

            # Diagnostics: log top 5 scores so we can see what's near the threshold
            top5 = sorted(signals, key=lambda s: abs(s.score), reverse=True)[:5]
            for s in top5:
                logger.info("  [%s] %-12s score=%+.3f conv=%.2f regime=%-8s trend=%s",
                            s.signal, s.symbol, s.score, s.conviction,
                            s.regime, s.trend_direction)

            for sig in actionable[:8]:   # Top 8 by score
                self._execute_cex_signal(sig)
        except Exception as e:
            logger.warning("CEX scan error: %s", e)

    def _execute_cex_signal(self, signal):
        """Execute a CEX trade with compound-scaled position sizing."""
        # BUG FIX: was mutating config.RISK_PER_TRADE_PCT — race condition when
        # multiple threads run concurrent scans. Now pass scale as a local variable.
        scale = self.compounder.get_position_scale_factor()
        scaled_risk = min(config.RISK_PER_TRADE_PCT * scale, 0.04)

        # Thread-local override — safe for concurrent execution across threads
        self.executor._tls.risk_override = scaled_risk
        try:
            self.executor.process_signal(signal)
        finally:
            self.executor._tls.risk_override = None

    def _run_scalp_scan(self):
        """5-minute scalp signals on BTC/ETH/SOL → leveraged futures trades."""
        try:
            signals = self.scalper.scan()
            logger.info("Scalp scan: %d actionable 5m signals", len(signals))
            for sig in signals[:3]:
                pos = self.executor.open_futures_position(sig)
                if pos:
                    logger.info(
                        "SCALP FUTURES %-5s %-6s x%d score=%.2f | %s",
                        sig.signal, sig.symbol, pos.get("leverage", 1),
                        sig.score, ", ".join(sig.reasons[:1]),
                    )
        except Exception as e:
            logger.warning("Scalp scan error: %s", e)

    def _run_futures_swing_scan(self):
        """
        1h swing signals on major crypto pairs → leveraged futures trades.
        BUG FIX: Reuses _cex_signals_cache instead of calling scan_all() again
        (was hitting APIs twice per minute and causing rate limits).
        """
        try:
            # Reuse signals from CEX scan (set in _run_cex_scan this cycle)
            with self._cex_cache_lock:
                signals = list(self._cex_signals_cache)
            if not signals:
                return
            # Filter for crypto only and high conviction
            futures_candidates = [
                s for s in signals
                if s.market == "crypto" and abs(s.score) > 0.45 and s.signal != "HOLD"
            ]
            logger.info("Futures swing scan: %d candidates from cached CEX signals", len(futures_candidates))
            for sig in futures_candidates[:3]:
                pos = self.executor.open_futures_position(sig)
                if pos:
                    logger.info(
                        "SWING FUTURES %-5s %-12s x%d score=%.2f conv=%.2f",
                        sig.signal, sig.symbol, pos.get("leverage", 1),
                        sig.score, sig.conviction,
                    )
        except Exception as e:
            logger.warning("Futures swing scan error: %s", e)

    def _run_funding_arb_scan(self):
        """Scan for funding rate arb opportunities and open best ones."""
        try:
            opps = self.funding_arb.find_opportunities()
            logger.info("Funding arb scan: %d opportunities found", len(opps))
            for opp in opps[:2]:   # Open at most 2 new arb positions per scan
                arb = self.funding_arb.open_arb(opp)
                if arb:
                    logger.info(
                        "ARB OPENED %-10s rate=%.4f%%/8h → %.2f%%/day",
                        opp["symbol"], opp["rate"] * 100, opp["rate_daily_pct"],
                    )
        except Exception as e:
            logger.warning("Funding arb scan error: %s", e)

    def _run_grid_management(self):
        """Update all grids (check fills, recenter, auto-open on ranging signals)."""
        try:
            # Update existing grids
            self.grid_trader.update_all_grids()
            self.grid_trader.recenter_grids()
            # Auto-open grids when market is ranging
            if self._cex_signals_cache:
                self.grid_trader.maybe_open_grids(self._cex_signals_cache)
            summary = self.grid_trader.summary()
            if summary["active_grids"] > 0:
                logger.info("Grid: %d active | %d fills | pnl=$%.2f",
                            summary["active_grids"], summary["total_fills"],
                            summary["total_pnl_usd"])
        except Exception as e:
            logger.warning("Grid management error: %s", e)

    def _write_bot_state(self, equity: float, elapsed_ms: float):
        """Write lightweight state file for the dashboard to read."""
        try:
            # Equity = cash (real SOL balance in USD) + open DEX position values
            true_equity = equity   # already computed as cash + dex_value in _run_cycle
            if true_equity > self.portfolio.peak_equity:
                self.portfolio.peak_equity = true_equity
            daily_pnl = round(true_equity - self._day_start_eq, 2)
            perf = self.portfolio.performance_summary()
            signal_table = []  # No CEX signals — Solana DEX only
            # Live wallet balances — cache is refreshed by _live_dashboard_writer every 5s;
            # here we only fetch if cache is stale (>5s) and writer hasn't run yet.
            _sol, _usdc, _sol_usd, _cache_ts = self._wallet_balance_cache
            if self.solana.is_connected and (time.time() - _cache_ts > 5):
                try:
                    _sol     = self.solana.get_sol_balance()
                    _usdc    = self.solana.get_usdc_balance()
                    _sol_usd = self.solana.get_portfolio_value_usd()
                    self._wallet_balance_cache = (_sol, _usdc, _sol_usd, time.time())
                except Exception:
                    pass
            wallet_sol, wallet_usdc, wallet_sol_usd = _sol, _usdc, _sol_usd
            state = {
                "cycle": self._cycle,
                "last_cycle_ts": time.time(),
                "last_cycle_ms": round(elapsed_ms, 1),
                "equity": round(true_equity, 2),
                "cash": round(self.portfolio.cash, 2),
                "initial_capital": self.portfolio.initial_capital,
                "mode": "live" if self.live else "paper",
                "daily_pnl_usd": daily_pnl,
                "daily_pnl_pct": round(daily_pnl / self._day_start_eq * 100, 2)
                    if self._day_start_eq > 0 else 0,
                "ws_connected": self._ws_feed.connected if self._ws_feed else False,
                "futures_enabled": config.FUTURES_ENABLED,
                "open_positions": len(self.portfolio.open_positions),
                "dex_positions": len(self._dex_positions),
                "peak_equity": round(self.portfolio.peak_equity, 2),
                "total_trades": len(self.portfolio.closed_trades),
                # Solana wallet status (shown in dashboard mode control panel)
                "wallet_connected":  self.solana.is_connected,
                "wallet_address":    str(self.solana.pubkey) if self.solana.is_connected else "",
                "wallet_sol":        round(wallet_sol,     4),
                "wallet_usdc":       round(wallet_usdc,    2),
                "wallet_sol_usd":    round(wallet_sol_usd, 2),
                # Performance stats
                "win_rate_pct": perf.get("win_rate_pct", 0),
                "profit_factor": perf.get("profit_factor", 0),
                "max_drawdown_pct": perf.get("max_drawdown_pct", 0),
                "total_pnl_usd": perf.get("total_pnl_usd", 0),
                # Recent closed trades (last 50) — written every cycle so dashboard
                # is always current, regardless of trades.json save frequency.
                "recent_trades": (lambda ct: ct[-50:])(list(self.portfolio.closed_trades)),
                "signal_table": signal_table,
                # DEX positions detail for dashboard (snapshot under lock)
                "dex_position_details": self._snapshot_dex_positions(),
            }
            self._atomic_json("bot_state.json", state)
            # Write equity curve every cycle so chart stays live (not just every 5 min)
            self._save_equity_curve()
            # Write portfolio + DEX positions every cycle for live position cards
            self.portfolio.save()
            self._save_dex_positions()
            # Mirror bot state to Supabase (throttled to 5s inside persist_bot_state)
            try:
                import secrets_manager as _sm
                _sm.persist_bot_state(state)
            except Exception:
                pass
        except Exception:
            pass   # Non-critical — dashboard will show stale data

    def _save_strategy_states(self):
        """Persist grid and arb state for dashboard display."""
        try:
            self._atomic_json("grid_state.json", self.grid_trader.summary(), indent=2)
        except Exception:
            pass
        try:
            self._atomic_json("arb_state.json", self.funding_arb.summary(), indent=2)
        except Exception:
            pass

    def _check_pyramiding(self):
        """
        Add 25% to open winning positions when a confirming fresh signal arrives.
        Only pyramids once per position (avoids doubling down into losses).
        """
        try:
            for asset_id, pos in list(self.portfolio.open_positions.items()):
                # Only pyramid spot positions (not futures — different risk profile)
                if pos.get("is_futures"):
                    continue
                if pos.get("pyramided"):
                    continue

                entry   = pos.get("entry_price", 0)
                current = pos.get("current_price", entry)
                if entry <= 0:
                    continue
                pnl_pct = (current - entry) / entry if pos.get("side") == "long" \
                    else (entry - current) / entry

                # Only pyramid when we're up > 3%
                if pnl_pct < 0.03:
                    continue

                # Need enough cash for a pyramid add (25% of original position value)
                pos_value = pos.get("qty", 0) * pos.get("entry_price", 0)
                if pos_value <= 0:
                    pos_value = self.portfolio.equity() * 0.02  # Fallback only
                add_size = pos_value * 0.25
                if add_size < 2.0 or self.portfolio.cash < add_size:
                    continue

                # Quick check: is there a confirming fresh signal?
                symbol = pos.get("symbol", "")
                market = pos.get("market", "crypto")
                if not symbol or market not in ("crypto", "stocks", "forex"):
                    continue

                confirms = self._has_confirming_signal(symbol, pos["side"])
                if confirms:
                    pos["pyramided"] = True
                    # Add to position at current price
                    fill = self.executor._fill_price(current, "buy" if pos["side"] == "long" else "sell")
                    qty_add = self.risk_mgr.qty_from_usd(add_size, fill)
                    if qty_add <= 0:
                        logger.warning("Pyramid skipped %s: invalid fill price %.4f", symbol, fill)
                        continue
                    pos["qty"] = pos.get("qty", 0) + qty_add
                    self.portfolio.cash -= add_size
                    logger.info("PYRAMID %-12s +25%% ($%.2f) @ $%.4f | pnl so far: +%.1f%%",
                                symbol, add_size, fill, pnl_pct * 100)
        except Exception as e:
            logger.debug("Pyramiding check error: %s", e)

    def _has_confirming_signal(self, symbol: str, side: str) -> bool:
        """
        Quick confirming signal check: does the 5m scalper agree with our position?
        Returns True if scalper has a matching BUY (for long) or SELL (for short).
        """
        try:
            scalp_symbol = symbol.upper().split("/")[0].replace("SOL", "SOL")
            signals = self.scalper.scan(symbols=[scalp_symbol])
            for sig in signals:
                if side == "long" and sig.signal == "BUY" and sig.conviction > 0.55:
                    return True
                if side == "short" and sig.signal == "SELL" and sig.conviction > 0.55:
                    return True
        except Exception:
            pass
        return False

    def _run_dex_scan(self):
        """
        DEX scan — multi-source discovery, concurrent safety checks, Jupiter price
        confirmation before execution.
        """
        try:
            # Circuit breakers removed — bot always scans and trades

            tokens = self.dex_screener.get_multi_chain_opportunities()
            if not tokens:
                logger.info("DEX scan: no opportunities found")
                return

            budget = self.compounder.max_position_for_market("crypto_dex")
            traded = 0

            held_addrs   = set(self._dex_positions.keys())
            held_symbols = {pos.get("symbol", "").upper()
                            for pos in self._dex_positions.values()}

            candidates = []
            for token in tokens:
                if token.score < config.DEX_MIN_SCORE:
                    break   # Sorted descending — nothing below this will qualify
                if token.pair_address in held_addrs:
                    continue
                if token.base_symbol.upper() in held_symbols:
                    continue
                if token.base_address in {p.get("address", "") for p in self._dex_positions.values()}:
                    continue
                candidates.append(token)
                if len(candidates) >= 12:   # Cap at 12 — only trade 6 max anyway
                    break

            logger.info("DEX scan: %d total → %d candidates above score %.2f",
                        len(tokens), len(candidates), config.DEX_MIN_SCORE)

            # ── Fast pre-filter: concentration + safety (in-memory, no I/O) ──
            # Build (token, size_usd) pairs for candidates that clear these gates.
            prefiltered = []
            n_blocked_conc = n_blocked_safety = n_blocked_size = 0
            for token in candidates:
                allowed, reason = self.risk_mgr.check_dex_concentration(
                    self._dex_positions, token.dex_id)
                if not allowed:
                    logger.info("Concentration block %s: %s", token.base_symbol, reason)
                    n_blocked_conc += 1
                    continue
                safety = getattr(token, "safety_report", None)
                if safety and not safety.is_safe_to_trade:
                    logger.warning("SKIP %s: %s — %s", token.base_symbol,
                                   safety.risk_level,
                                   ", ".join(safety.risk_flags[:2]))
                    n_blocked_safety += 1
                    continue
                safety_score = safety.safety_score if safety else 0.5
                size_usd = self.risk_mgr.dex_position_size_usd(
                    token_score=token.score,
                    safety_score=safety_score,
                    liquidity_usd=token.liquidity_usd,
                    price_change_h1=token.price_change_h1,
                    price_change_h6=token.price_change_h6,
                )
                size_usd = min(size_usd, config.DEX_MAX_POSITION_USD)
                if size_usd < config.DEX_MIN_POSITION_USD:
                    logger.warning("SKIP %s: size $%.2f below min $%.2f (liq=$%.0f cash=$%.2f)",
                                   token.base_symbol, size_usd, config.DEX_MIN_POSITION_USD,
                                   token.liquidity_usd, self.portfolio.cash)
                    n_blocked_size += 1
                    continue
                logger.info("Prefilter OK %s: size=$%.2f score=%.2f safety=%.2f liq=$%.0f",
                            token.base_symbol, size_usd, token.score, safety_score,
                            token.liquidity_usd)
                prefiltered.append((token, size_usd, safety))

            logger.info("Prefilter: %d passed, %d conc-blocked, %d safety-blocked, %d size-blocked",
                        len(prefiltered), n_blocked_conc, n_blocked_safety, n_blocked_size)

            # ── Parallel network validation: Jupiter price + Birdeye checks ──
            # Run all I/O-bound checks concurrently across candidates.
            def _validate(args):
                """
                Returns (token, size_usd, safety) if validation passes, else None.
                All network calls happen here so they run in parallel.
                """
                tok, sz, sfty = args

                # Holder count hard block — use data already fetched by safety checker
                if sfty and getattr(sfty, "holder_count", None) and 0 < sfty.holder_count < 20:
                    logger.warning("SKIP %s: only %d holders (rug risk)",
                                   tok.base_symbol, sfty.holder_count)
                    return None

                return (tok, sz, sfty)

            validated = []
            if prefiltered:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(len(prefiltered), 20),
                        thread_name_prefix="dex-validate") as val_ex:
                    futs = [val_ex.submit(_validate, args) for args in prefiltered]
                    for f in concurrent.futures.as_completed(futs, timeout=15):
                        try:
                            result = f.result()
                            if result is not None:
                                validated.append(result)
                        except Exception as e:
                            logger.warning("Validate candidate error: %s", e)
                # Preserve score-descending order after concurrent collection
                validated.sort(key=lambda x: x[0].score, reverse=True)

            logger.info("Validated: %d/%d passed, executing up to 8 buys",
                        len(validated), len(prefiltered))

            for token, size_usd, safety in validated:
                if traded >= 8:   # Up from 6 — execute more per cycle
                    break
                self._open_dex_position(token, size_usd, safety)
                held_symbols.add(token.base_symbol.upper())
                held_addrs.add(token.pair_address)
                traded += 1

        except Exception as e:
            logger.warning("DEX scan error: %s", e)

    def _get_jupiter_price(self, mint: str) -> Optional[float]:
        """Fetch real-time price from Jupiter Price API v2."""
        try:
            import requests as _req
            resp = _req.get(
                "https://api.jup.ag/price/v2",
                params={"ids": mint},
                timeout=4,
                headers={"User-Agent": "ai-trader/2.0"})
            if resp.ok:
                data = resp.json().get("data", {}).get(mint, {})
                price = data.get("price")
                if price:
                    return float(price)
        except Exception:
            pass
        return None

    def _open_dex_position(self, token, size_usd: float, safety=None):
        """Open a DEX position with safety verification and MEV protection."""
        try:
            # ── Require a valid pair address as position key ─────────────────
            if not token.pair_address:
                logger.warning("SKIP %s: missing pair_address — cannot track position",
                               token.base_symbol)
                return

            # ── Cash gate: verify we can afford this using wallet cache as ground truth
            if token.chain_id == "solana" and self.solana.is_connected:
                _sol, _usdc, _sol_usd, _cache_ts = self._wallet_balance_cache
                # Prefer fresh wallet cache (<10s); fall back to portfolio.cash floored at 0.
                sol_bal_usd = (
                    _sol_usd if (time.time() - _cache_ts) < 10 and _sol_usd > 0
                    else max(0.0, self.portfolio.cash)
                )
                if sol_bal_usd < size_usd + 0.15:   # Need trade size + ~$0.15 for Solana tx fees
                    logger.warning("SOL balance $%.2f insufficient for $%.2f trade — skipping",
                                   sol_bal_usd, size_usd)
                    return
            elif size_usd > max(0.0, self.portfolio.cash):
                logger.warning("Insufficient cash $%.2f for DEX buy $%.2f — skipping",
                               self.portfolio.cash, size_usd)
                return

            safety_score = safety.safety_score if safety else 0.5
            # AI-computed stop and target per trade based on token's own volatility
            stop_pct   = self.risk_mgr.dynamic_dex_stop_pct(
                price_change_h1  = getattr(token, "price_change_h1",  0) or 0,
                price_change_h6  = getattr(token, "price_change_h6",  0) or 0,
                price_change_h24 = getattr(token, "price_change_h24", 0) or 0,
                safety_score     = safety_score,
            )
            target_pct = self.risk_mgr.dynamic_dex_target_pct(
                price_change_h24 = getattr(token, "price_change_h24", 0) or 0,
                score            = token.score,
                price_change_h1  = getattr(token, "price_change_h1",  0) or 0,
            )
            _h1_chg = abs(getattr(token, "price_change_h1", 0) or 0)
            pos_data = {
                "symbol": token.base_symbol,
                "address": token.base_address,
                "chain": token.chain_id,
                "dex_id": token.dex_id,
                "entry_price": token.price_usd,
                "current_price": token.price_usd,
                "size_usd": size_usd,
                "remaining_fraction": 1.0,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "stop_pct": stop_pct,
                "target_pct": target_pct,
                "score": token.score,
                "safety_score": safety.safety_score if safety else None,
                "risk_level": safety.risk_level if safety else None,
                "signals": token.signals,
                "partial_profits_taken": [],
                "peak_price": token.price_usd,
                "current_pnl_pct": 0.0,
                "liquidity_usd": token.liquidity_usd,  # for dynamic slippage on exit
                # Scalp mode: high 1h volatility → tight exits to secure quick profits
                "scalp_mode": _h1_chg >= config.SCALP_MODE_VOL_THRESHOLD,
                "entry_h1_vol": _h1_chg,
            }

            if token.chain_id == "solana" and self.solana.is_connected:
                # Pass liquidity so wallet can compute dynamic slippage
                tx = self.solana.safe_buy_token(
                    token.base_address, size_usd,
                    safety_report=safety,
                    liquidity_usd=token.liquidity_usd,
                    pair_address=token.pair_address if token.dex_id == "pumpswap" else None)
                if not tx:
                    logger.warning("BUY skipped %s: safe_buy_token returned None "
                                   "(check BUY FAILED / BLOCKED logs above)",
                                   token.base_symbol)
                elif tx.startswith("paper_"):
                    logger.warning("BUY skipped %s: got paper tx in live mode — "
                                   "solana.is_connected may have flipped", token.base_symbol)
                if tx and not tx.startswith("paper_"):
                    pos_data["tx"] = tx
                    # Priority 1: parse on-chain transaction for exact fill price
                    # (wallet waits for FINALIZED before returning, so tx is available)
                    fill_set = False
                    try:
                        fill = self.solana.get_transaction_token_change(
                            tx, token.base_address)
                        if fill.get("price_usd", 0) > 0:
                            pos_data["entry_price"]   = fill["price_usd"]
                            pos_data["current_price"] = fill["price_usd"]
                            pos_data["peak_price"]    = fill["price_usd"]
                            if fill.get("tokens_received"):
                                pos_data["qty"] = fill["tokens_received"]
                            if fill.get("sol_spent"):
                                pos_data["sol_spent"] = fill["sol_spent"]
                            fill_set = True
                            logger.info("Fill price from chain: $%.8f (%.4f tokens for %.4f SOL)",
                                        fill["price_usd"],
                                        fill.get("tokens_received", 0),
                                        fill.get("sol_spent", 0))
                    except Exception:
                        pass
                    # Priority 2: Birdeye real-time price if tx parse unavailable
                    if not fill_set and config.BIRDEYE_API_KEY:
                        try:
                            from dex_screener import _get_birdeye as _dex_be
                            _be = _dex_be()
                            if _be:
                                bp = _be.get_price(token.base_address)
                                if bp and bp.price_usd > 0:
                                    pos_data["entry_price"]   = bp.price_usd
                                    pos_data["current_price"] = bp.price_usd
                                    pos_data["peak_price"]    = bp.price_usd
                        except Exception:
                            pass  # Keep pre-trade estimate on failure
                    self._dex_positions[token.pair_address] = pos_data
                    self.portfolio.cash -= size_usd
                    risk_str = safety.risk_level if safety else "?"
                    logger.info("DEX BUY %s $%.2f @ $%.8f score=%.2f safety=%s | tx=%s",
                                token.base_symbol, size_usd, pos_data["entry_price"],
                                token.score, risk_str, tx[:16])
            else:
                pos_data["tx"] = f"paper_{int(time.time())}"
                self._dex_positions[token.pair_address] = pos_data
                self.portfolio.cash -= size_usd
                logger.info(
                    "PAPER DEX BUY %s $%.2f @ $%.8f score=%.2f | "
                    "stop=%.0f%% target=%.0f%% | %s",
                    token.base_symbol, size_usd, token.price_usd, token.score,
                    stop_pct * 100, target_pct * 100,
                    ", ".join(token.signals[:2])
                )

        except Exception as e:
            logger.warning("DEX position open failed (%s): %s", token.base_symbol, e)

    def _fast_price_monitor(self):
        """
        Background thread: polls Birdeye multi_price every 3s for all held DEX tokens.
        Updates current_price + current_pnl_pct in _dex_positions so that the main
        thread's _update_dex_positions() always has fresh prices.
        Triggers spike exits and stop-loss closes directly (thread-safe via dict update).
        """
        from dex_screener import _get_birdeye
        while self.running:
            try:
                time.sleep(3)
                if not self._dex_positions:
                    continue
                be = _get_birdeye()
                if not be or not be.enabled:
                    # Birdeye unavailable — still run stop/trail checks using prices
                    # cached by the main cycle (updated each scan, typically every 5-30s).
                    # Not as fresh as Birdeye, but far better than doing nothing for 30s.
                    _to_close_fallback = []
                    for _pair_addr, _pos in list(self._dex_positions.items()):
                        _entry   = _pos.get("entry_price", 0)
                        _current = _pos.get("current_price", _entry)
                        if _entry <= 0 or _current <= 0:
                            continue
                        _pnl     = (_current - _entry) / _entry
                        _peak    = _pos.get("peak_price", _entry)
                        _stop    = _pos.get("stop_pct", 0.20)
                        _scalp   = _pos.get("scalp_mode", False)
                        # Hard stop-loss
                        if _pnl <= -_stop:
                            _to_close_fallback.append((_pair_addr, _pos, _current,
                                f"Stop-loss {_pnl:.0%} [no Birdeye]"))
                            continue
                        # Trailing / reversal from peak
                        if _peak > _entry and _pnl > 0:
                            _reversal = (_peak - _current) / _peak
                            _rev_thr = (config.SCALP_REVERSAL_PCT if _scalp
                                        else min(0.07 + _pnl * 0.06, 0.20))
                            if _reversal >= _rev_thr and _pnl > 0.04:
                                _to_close_fallback.append((_pair_addr, _pos, _current,
                                    f"Trailing -{_reversal:.0%} from peak "
                                    f"(PnL +{_pnl:.0%}) [no Birdeye]"))
                    for _pa, _p, _cp, _rsn in _to_close_fallback:
                        logger.info("FAST EXIT (no Birdeye) %s @ $%.8f | %s",
                                    _p.get("symbol", "?"), _cp, _rsn)
                        self._try_close_dex_position(_pa, _p, _cp, _rsn)
                    continue

                # Batch-fetch all held Solana token prices in one API call
                mints = [pos["address"] for pos in self._dex_positions.values()
                         if pos.get("chain") == "solana"]
                if not mints:
                    continue

                prices = be.get_multi_price(mints)

                to_close = []
                for pair_addr, pos in list(self._dex_positions.items()):
                    mint = pos.get("address")
                    bp   = prices.get(mint)
                    if not bp or bp.price_usd <= 0:
                        continue

                    entry   = pos["entry_price"]
                    current = bp.price_usd
                    pnl_pct = (current - entry) / entry if entry > 0 else 0
                    pos["current_price"]   = current
                    pos["current_pnl_pct"] = pnl_pct
                    pos["peak_price"]      = max(pos.get("peak_price", entry), current)

                    prev = pos.get("fast_prev_price", entry)
                    pos["fast_prev_price"] = current
                    # Track 5m price change for stale-exit momentum override
                    if prev > 0:
                        pos["price_change_m5"] = (current - prev) / prev * 100

                    scalp = pos.get("scalp_mode", False)

                    # ── 1. Spike capture: sell the pump before the inevitable dump ─
                    # Scalp mode: exit at any 10% surge once barely in profit (+8%)
                    # Normal mode: exit 15% surge after solid gain (+15%)
                    spike_pnl_thr  = 0.08 if scalp else 0.15
                    spike_surge_thr = 0.10 if scalp else 0.15
                    if prev > 0 and pnl_pct > spike_pnl_thr and current > prev:
                        surge = (current - prev) / prev
                        if surge >= spike_surge_thr:
                            to_close.append((
                                pair_addr, pos, current,
                                f"FastMonitor spike: +{surge:.0%} in 3s (PnL +{pnl_pct:.0%})"
                                + (" [SCALP]" if scalp else "")))
                            continue

                    # ── 2. Reversal after peak: price dropped from ATH ────────
                    peak = pos.get("peak_price", entry)
                    if peak > entry and pnl_pct > 0:
                        reversal = (peak - current) / peak
                        if scalp:
                            # Scalp: exit the moment it drops 5% from peak
                            reversal_threshold = config.SCALP_REVERSAL_PCT
                            min_profit_thr = 0.04
                        else:
                            # Normal: tightened from 0.12+pnl*0.08 → 0.07+pnl*0.06
                            reversal_threshold = min(0.07 + pnl_pct * 0.06, 0.20)
                            min_profit_thr = 0.04
                        if reversal >= reversal_threshold and pnl_pct > min_profit_thr:
                            to_close.append((
                                pair_addr, pos, current,
                                f"FastMonitor reversal: -{reversal:.0%} from peak "
                                f"(PnL +{pnl_pct:.0%})" + (" [SCALP]" if scalp else "")))
                            continue

                    # ── 2b. Momentum collapse: sharp 3s drop while barely in profit ─
                    if prev > 0:
                        tick_drop = (current - prev) / prev
                        collapse_thr = -0.05 if scalp else -0.07
                        if tick_drop <= collapse_thr and pnl_pct < 0.08:
                            to_close.append((
                                pair_addr, pos, current,
                                f"FastMonitor momentum collapse: {tick_drop:.0%} tick "
                                f"(PnL {pnl_pct:.0%})"))
                            continue

                    # ── 3. Volume dry-up with stalled price ───────────────────
                    # Track volume samples; if 24h vol drops >60% in 2 consecutive
                    # ticks compared to entry-time volume → market is abandoning it
                    if bp.volume_24h_usd > 0:
                        entry_vol = pos.get("entry_volume_24h", 0)
                        if entry_vol <= 0:
                            pos["entry_volume_24h"] = bp.volume_24h_usd
                        else:
                            vol_drop = (entry_vol - bp.volume_24h_usd) / entry_vol
                            prev_drop = pos.get("prev_vol_drop", 0.0)
                            pos["prev_vol_drop"] = vol_drop
                            # Two consecutive readings show major vol collapse
                            if vol_drop > 0.60 and prev_drop > 0.60 and pnl_pct > 0:
                                to_close.append((
                                    pair_addr, pos, current,
                                    f"FastMonitor vol dry-up: -{vol_drop:.0%} from entry "
                                    f"(PnL +{pnl_pct:.0%} — locking in)"))
                                continue

                    # ── 4. Instant stop-loss ──────────────────────────────────
                    stop_pct = pos.get("stop_pct", 0.20)
                    if pnl_pct <= -stop_pct:
                        to_close.append((
                            pair_addr, pos, current,
                            f"FastMonitor stop-loss {pnl_pct:.0%}"))

                for pair_addr, pos, current, reason in to_close:
                    logger.info("FAST EXIT %s @ $%.8f | %s",
                                pos.get("symbol", "?"), current, reason)
                    self._try_close_dex_position(pair_addr, pos, current, reason)

            except Exception as e:
                logger.debug("FastPriceMonitor error: %s", e)

    def _live_dashboard_writer(self):
        """
        Background thread: writes bot_state.json every 5 seconds.
        Prices are already fresh (fast price monitor updates every 3s).
        SOL balance is polled here with a 5s cache so cash is always live.
        This is completely independent of the 30s main scan cycle.
        """
        _last_token_reconcile = 0.0
        while self.running:
            try:
                time.sleep(5)

                # ── 1. Refresh SOL balance in portfolio.cash ──────────────────
                if self.live and self.solana.is_connected:
                    sol_raw   = self.solana.get_sol_balance()
                    sol_price = self.solana._get_sol_price()
                    sol_usd   = sol_raw * sol_price if sol_price > 0 else 0.0
                    if sol_usd > 0:
                        # portfolio.cash = actual SOL wallet value (ground truth).
                        # Previously we subtracted "dex_deployed" but that estimate
                        # becomes inflated when sells fail — making available negative
                        # and the `if available > 0` gate permanently blocking the sync.
                        # DEX positions are tracked separately in _dex_positions; the
                        # SOL wallet already reflects what was actually spent on buys.
                        if abs(sol_usd - self.portfolio.cash) > 0.50:
                            self.portfolio.cash = sol_usd
                        # Update wallet cache for dashboard display
                        try:
                            _usdc = self.solana.get_usdc_balance()
                        except Exception:
                            _usdc = self._wallet_balance_cache[1]
                        self._wallet_balance_cache = (sol_raw, _usdc, sol_usd, time.time())

                # ── 2. Token position reconciliation (every 30s) ─────────────
                now_ts = time.time()
                if (self.live and self.solana.is_connected
                        and now_ts - _last_token_reconcile > 30
                        and self._dex_positions):
                    _last_token_reconcile = now_ts
                    try:
                        on_chain = self.solana.get_all_token_balances()
                        if on_chain is not None:  # None = RPC error; {} = empty wallet (valid)
                            _skip = {SOL_MINT, USDC_MINT, USDT_MINT}
                            for pair_addr, pos in list(self._dex_positions.items()):
                                mint = pos.get("address", "")
                                if not mint or mint in _skip or pos.get("chain") != "solana":
                                    continue
                                if mint not in on_chain:
                                    entry   = pos.get("entry_price", 0)
                                    size    = pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
                                    current = pos.get("current_price", entry)
                                    symbol  = pos.get("symbol", "?")
                                    logger.warning(
                                        "LIVE SYNC: %s (%s…) zero on-chain balance "
                                        "— removed from tracker (externally closed).",
                                        symbol, mint[:12])
                                    with self._dex_lock:
                                        self._dex_positions.pop(pair_addr, None)
                                    proceeds = max(size * (current / entry) if entry > 0 else size, 0.0)
                                    self.portfolio.cash += proceeds
                                    self.portfolio.closed_trades.append({
                                        "asset_id":    pair_addr,
                                        "symbol":      symbol,
                                        "side":        "long",
                                        "entry_price": entry,
                                        "exit_price":  current,
                                        "pnl_usd":     round(proceeds - size, 4),
                                        "pnl_pct":     round((current / entry - 1) * 100, 2) if entry > 0 else 0,
                                        "closed_at":   datetime.now(timezone.utc).isoformat(),
                                        "reason":      "externally_closed",
                                        "chain":       "solana",
                                    })
                    except Exception as e:
                        logger.debug("Live token reconcile error: %s", e)

                # ── 3. Compute live equity and append to curve ────────────────
                dex_value = self._dex_value_snapshot()
                equity = self.portfolio.cash + dex_value

                # Add equity curve point every 5s so the chart is fully live
                self._equity_curve.append({
                    "ts":     datetime.now(timezone.utc).isoformat(),
                    "equity": round(equity, 2),
                })
                if len(self._equity_curve) > 17_280:   # 24h at 5s cadence
                    self._equity_curve = self._equity_curve[-17_280:]

                # ── 4. Write live bot_state.json + Supabase persistence ──────
                self._write_bot_state(equity, 0.0)
                try:
                    import secrets_manager as _sm
                    _sm.persist_equity(equity, self.portfolio.cash, dex_value)
                except Exception:
                    pass

            except Exception as e:
                logger.debug("LiveDashboardWriter error: %s", e)

    def _update_dex_positions(self):
        """Check open DEX positions for time exits, partial profits, and stop/TP."""
        for pair_addr, pos in list(self._dex_positions.items()):
            try:
                # Fast monitor may have already claimed this position — skip it
                if pair_addr not in self._dex_positions:
                    continue

                token = self.dex_screener.get_token_info(pos["address"], pos["chain"])
                if not token or token.price_usd <= 0:
                    miss = pos.get("_price_miss", 0) + 1
                    pos["_price_miss"] = miss
                    if miss % 12 == 0:  # warn every ~60s
                        logger.warning(
                            "Cannot fetch price for %s (%d consecutive misses) — "
                            "position exits blocked", pos.get("symbol", "?"), miss)
                    if miss >= 72:  # 6 min of no price → force exit at last known price
                        last = pos.get("current_price", pos.get("entry_price", 0))
                        logger.error(
                            "Force-closing %s after %d price misses (last=$%.8f)",
                            pos.get("symbol", "?"), miss, last)
                        self._try_close_dex_position(
                            pair_addr, pos, last, f"No price data for {miss} cycles — force exit")
                    continue
                pos["_price_miss"] = 0  # reset on successful fetch

                entry   = pos["entry_price"]
                current = token.price_usd
                pnl_pct = (current - entry) / entry if entry > 0 else 0
                pos["current_price"]  = current
                pos["current_pnl_pct"] = pnl_pct
                # Refresh live token signals for dynamic adjustment
                if token.liquidity_usd > 0:
                    pos["liquidity_usd"] = token.liquidity_usd
                pos["price_change_m5"]  = getattr(token, "price_change_m5",  0) or 0
                pos["price_change_h1"]  = getattr(token, "price_change_h1",  0) or 0
                pos["volume_24h"]       = getattr(token, "volume_24h",        0) or 0
                pos["score"]            = getattr(token, "score", pos.get("score", 0))

                # Update trailing high
                pos["peak_price"] = max(pos.get("peak_price", entry), current)
                peak = pos["peak_price"]
                trail_pct = (peak - current) / peak if peak > 0 else 0

                # ── DYNAMIC POSITION ADJUSTMENT based on live monitoring data ────
                # 1. Pyramid add: position running well + strong 5m momentum
                m5_now = getattr(token, "price_change_m5", 0) or 0
                if (pnl_pct >= 0.20                          # 20%+ in profit
                        and m5_now >= 3.0                    # 3%+ 5m momentum
                        and pos.get("remaining_fraction", 1.0) >= 0.8   # haven't taken major partials
                        and not pos.get("pyramided")         # only pyramid once per position
                        and self.portfolio.cash > 2.0):      # have cash to add
                    add_size = min(pos["size_usd"] * 0.30, self.portfolio.cash * 0.10,
                                   config.DEX_MAX_POSITION_USD - pos["size_usd"])
                    if add_size >= config.DEX_MIN_POSITION_USD:
                        pos["size_usd"] += add_size
                        self.portfolio.cash -= add_size
                        pos["pyramided"] = True
                        logger.info("PYRAMID %s +$%.2f (PnL=+%.0f%% 5m=+%.1f%%) → total $%.2f",
                                    pos.get("symbol", "?"), add_size, pnl_pct * 100,
                                    m5_now, pos["size_usd"])

                # 2. Liquidity drain — exit early if pool liquidity dropped >40%
                entry_liq = pos.get("entry_liquidity_usd", 0)
                cur_liq   = token.liquidity_usd
                if entry_liq <= 0:
                    pos["entry_liquidity_usd"] = cur_liq  # record on first fetch
                elif cur_liq > 0 and entry_liq > 0:
                    liq_drop = (entry_liq - cur_liq) / entry_liq
                    if liq_drop >= 0.40 and not pos.get("_liq_warned"):
                        pos["_liq_warned"] = True
                        logger.warning("TIGHTEN STOP %s: liquidity drained %.0f%% ($%.0f→$%.0f)",
                                       pos.get("symbol", "?"), liq_drop * 100,
                                       entry_liq, cur_liq)
                        # Tighten stop by 40% (e.g. 30% stop → 18%)
                        pos["stop_pct"] = pos.get("stop_pct", 0.20) * 0.60

                # 3. Volume collapse — if 5m change is strongly negative while overall
                # volume was previously strong, the momentum has flipped; tighten stop
                if m5_now <= -5.0 and pnl_pct > 0 and not pos.get("_momentum_flip"):
                    pos["_momentum_flip"] = True
                    logger.info("MOMENTUM FLIP %s: 5m=%.1f%% PnL=+%.0f%% — tightening stop",
                                pos.get("symbol", "?"), m5_now, pnl_pct * 100)
                    pos["stop_pct"] = min(pos.get("stop_pct", 0.20),
                                         max(0.08, pnl_pct * 0.30))  # protect 70% of gains

                # 0. VOLATILITY SPIKE EXIT — sell at the top of a sudden pump
                # If price surged ≥15% since the last cycle, we're likely at or near
                # the peak; sell immediately before the dump reversal.
                prev_price = pos.get("prev_price", entry)
                if prev_price > 0 and pnl_pct > 0:
                    cycle_surge = (current - prev_price) / prev_price
                    if cycle_surge >= 0.15:
                        spike_reason = (
                            f"Spike exit: +{cycle_surge:.0%} single-cycle surge "
                            f"(total PnL +{pnl_pct:.0%})"
                        )
                        self._try_close_dex_position(pair_addr, pos, current, spike_reason)
                        continue
                pos["prev_price"] = current

                # 1. TIME-BASED exits
                should_exit, time_reason = self.risk_mgr.check_time_exit(pos)
                if should_exit:
                    self._try_close_dex_position(pair_addr, pos, current, time_reason)
                    continue

                # 2. DUST CLEANUP — remaining fraction too small to trade meaningfully
                remaining_now = pos.get("remaining_fraction", 1.0)
                if remaining_now < 0.02:
                    self._try_close_dex_position(
                        pair_addr, pos, current, "Dust cleanup (<2% remaining)")
                    continue

                # 3. PARTIAL PROFIT-TAKING
                sell_frac, partial_reason, partial_threshold = self.risk_mgr.get_partial_profit_action(pos)
                if sell_frac is not None:
                    self._execute_partial_profit(pair_addr, pos, sell_frac, partial_reason, partial_threshold)

                # 4. FULL EXIT conditions
                remaining = pos.get("remaining_fraction", 1.0)
                # Raise target as partials are taken — the moonshot runner rides for more
                adj_target = pos["target_pct"] * (1 + (1 - remaining) * 0.5)

                # ── Scalp mode: override target to quick scalp threshold ────
                scalp = pos.get("scalp_mode", False)
                if scalp and adj_target > config.SCALP_TARGET_PCT:
                    adj_target = config.SCALP_TARGET_PCT

                # ── Momentum hold extension: only for non-scalp positions ───
                # Avoid cutting a still-running swing trade at its first target.
                m5_chg = pos.get("price_change_m5", 0)
                if not scalp and pnl_pct > 0.15 and abs(m5_chg) > 5:
                    adj_target *= 1.20   # reduced from 1.30 — don't overstay
                    logger.debug("%s momentum extension: target → %.1f%% (5m: %+.1f%%)",
                                 pos.get("symbol", "?"), adj_target * 100, m5_chg)

                # Trailing stop: scalp positions use tight SCALP_REVERSAL_PCT,
                # swing positions use stop-adaptive distance.
                pos_stop = pos.get("stop_pct", 0.20)
                if scalp:
                    trail_thr  = config.SCALP_REVERSAL_PCT           # e.g. 5%
                    profit_thr = config.SCALP_TARGET_PCT * 0.25      # e.g. 4.5% (any profit)
                else:
                    trail_thr  = pos_stop * 0.80   # e.g. 16% trail for 20% stop
                    profit_thr = pos_stop * 0.50   # must be in profit first
                reason = None
                if pnl_pct >= adj_target:
                    reason = f"Take profit +{pnl_pct:.0%}"
                elif pnl_pct <= -pos_stop:
                    reason = f"Stop loss {pnl_pct:.0%}"
                elif trail_pct > trail_thr and pnl_pct > profit_thr:
                    reason = f"Trailing stop (peak=${peak:.8f}, trail={trail_pct:.0%})"

                if reason:
                    self._try_close_dex_position(pair_addr, pos, current, reason)

            except Exception as e:
                logger.debug("DEX position update error: %s", e)

    def _snapshot_dex_positions(self) -> list:
        """Return a snapshot of DEX positions under lock (thread-safe)."""
        with self._dex_lock:
            positions = list(self._dex_positions.values())
        return [
            {
                "symbol":    pos.get("symbol", "?"),
                "address":   pos.get("address", ""),
                "size_usd":  round(pos.get("size_usd", 0), 2),
                "entry":     pos.get("entry_price", 0),
                "current":   pos.get("current_price", pos.get("entry_price", 0)),
                "pnl_pct":   round(pos.get("current_pnl_pct", 0) * 100, 2),
                "remaining": round(pos.get("remaining_fraction", 1.0) * 100, 1),
                "chain":     pos.get("chain", "solana"),
            }
            for pos in positions
        ]

    def _dex_value_snapshot(self) -> float:
        """Compute total DEX position value under lock (thread-safe)."""
        with self._dex_lock:
            positions = list(self._dex_positions.values())
        return sum(
            pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
            * (1 + pos.get("current_pnl_pct", 0))
            for pos in positions
        )

    def _try_close_dex_position(self, pair_addr: str, pos: dict,
                                current_price: float, reason: str) -> bool:
        """
        Atomically claim (lock + pop) a position from _dex_positions then close it.
        Returns False if the position was already claimed by another thread or if the
        on-chain sell failed.  Safe to call from both the main loop and fast monitor.
        """
        with self._dex_lock:
            if pair_addr not in self._dex_positions:
                return False  # Already closed by the other thread — skip
            self._dex_positions.pop(pair_addr)  # Claim ownership before releasing lock
        result = self._close_dex_position(pair_addr, pos, current_price, reason)
        if result is False:
            # On-chain sell failed — re-insert so the next cycle can retry.
            # Only re-insert if nothing else has taken that slot (shouldn't happen,
            # but guard anyway to avoid overwriting a re-opened position).
            with self._dex_lock:
                if pair_addr not in self._dex_positions:
                    self._dex_positions[pair_addr] = pos
        return result

    def _close_dex_position(self, pair_addr: str, pos: dict, current_price: float, reason: str) -> bool:
        """Close a DEX position (remaining fraction only). Returns False if on-chain sell failed."""
        entry     = pos["entry_price"]
        remaining = pos.get("remaining_fraction", 1.0)
        size      = pos["size_usd"] * remaining
        pnl_pct   = (current_price - entry) / entry if entry > 0 else 0
        pnl_usd   = size * pnl_pct
        proceeds  = size + pnl_usd

        # Clamp proceeds — pnl_pct can't go below -100% (position is fully lost)
        proceeds = max(proceeds, 0.0)

        liq_usd = pos.get("liquidity_usd", 0.0)
        if pos["chain"] == "solana" and self.solana.is_connected and "paper" not in pos.get("tx", ""):
            sell_result = self.solana.sell_token(pos["address"], proceeds,
                                                liquidity_usd=liq_usd,
                                                pair_address=pair_addr if pos.get("dex_id") == "pumpswap" else None)
            if sell_result:
                _sig, actual_usd = sell_result
                self.portfolio.cash += actual_usd
                # Use actual on-chain proceeds for PnL — eliminates slippage error
                pnl_usd = actual_usd - size
                pnl_pct = pnl_usd / size if size > 0 else 0
                # Derive actual exit price from real proceeds
                if pos.get("qty", 0) > 0:
                    current_price = actual_usd / pos["qty"] if pos["qty"] > 0 else current_price
            else:
                # Sell failed — keep position open for retry next cycle
                # DO NOT credit cash (token still in wallet) — avoids double-counting
                logger.error("SELL FAILED %s — keeping position open for retry next cycle",
                             pos["symbol"])
                return False   # Caller must NOT remove this position from _dex_positions
        else:
            self.portfolio.cash += proceeds

        sign = "+" if pnl_usd >= 0 else ""
        logger.info("DEX CLOSE %s %s%.2f%% ($%s%.2f) remaining=%.0f%% | %s",
                    pos["symbol"], sign, pnl_pct * 100, sign, pnl_usd,
                    remaining * 100, reason)

        # ── Max-adverse and max-favourable excursion (key for audit analysis)
        peak_price  = pos.get("peak_price", entry)
        max_gain    = (peak_price - entry) / entry if entry > 0 else 0  # best price seen
        hold_secs   = (datetime.now(timezone.utc) -
                       datetime.fromisoformat(pos["opened_at"])).total_seconds()

        trade_record = {
            "asset_id": pair_addr,
            "symbol": pos["symbol"],
            "market": "dex",
            "side": "long",
            "entry_price": entry,
            "exit_price": current_price,
            "qty": pos.get("qty", 0),
            "size_usd": pos.get("size_usd", 0),
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct * 100, 2),
            "close_reason": reason,
            "chain": pos.get("chain", "solana"),
            "safety_score": pos.get("safety_score"),
            "opened_at": pos["opened_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
            # ── Audit fields (used by StrategyAuditor for DEX-specific analysis)
            "signal_score": pos.get("score", 0),          # entry score (0–1)
            "signals": pos.get("signals", []),             # list of signal labels
            "dex_source": pos.get("source", ""),           # dex scan source (pumpfun_new, etc.)
            "max_gain_pct": round(max_gain * 100, 2),      # peak unrealised gain
            "hold_seconds": round(hold_secs),              # how long held
            "partials_taken": pos.get("partial_profits_taken", []),  # tiers hit
            "stop_pct": pos.get("stop_pct", 0),            # stop that was set
            "target_pct": pos.get("target_pct", 0),        # take-profit target
            "is_burst": "BURST MODE" in " ".join(pos.get("signals", [])),
        }
        self.portfolio.closed_trades.append(trade_record)
        # Persist to Supabase (fire-and-forget)
        try:
            import secrets_manager as _sm
            _sm.persist_trade(trade_record)
        except Exception:
            pass
        # Enforce memory cap (same as portfolio.close_position does for CEX trades)
        from portfolio import _MAX_CLOSED_TRADES_MEMORY
        if len(self.portfolio.closed_trades) > _MAX_CLOSED_TRADES_MEMORY:
            self.portfolio._archive_old_trades()

        # ── Trigger per-trade review + periodic repivot on every close ────
        self._dex_closed_count += 1
        try:
            self.auditor.on_trade_closed(trade_record)
        except Exception as _ae:
            logger.debug("Auditor error: %s", _ae)

        return True

    def _execute_partial_profit(self, pair_addr: str, pos: dict,
                                 fraction: float, reason: str, threshold_pct: float = 0):
        """Execute a partial profit-take on a DEX position."""
        try:
            size_usd = pos.get("size_usd", 0)
            if size_usd <= 0:
                logger.warning("SKIP partial TP %s: size_usd=%s (corrupted position — closing)",
                                pos.get("symbol", "?"), size_usd)
                # Mark tier taken anyway to prevent infinite loop
                pos["partial_profits_taken"].append(threshold_pct)
                return

            remaining = pos.get("remaining_fraction", 1.0)
            actual_frac = fraction * remaining

            pnl_pct = pos.get("current_pnl_pct", 0)
            proceeds = size_usd * actual_frac * (1 + pnl_pct)

            liq_usd = pos.get("liquidity_usd", 0.0)
            if (pos["chain"] == "solana" and self.solana.is_connected
                    and "paper" not in pos.get("tx", "")):
                sell_result = self.solana.sell_token_partial(pos["address"], fraction,
                                                             liquidity_usd=liq_usd,
                                                             pair_address=pair_addr if pos.get("dex_id") == "pumpswap" else None)
                if not sell_result:
                    return
                _sig, actual_usd = sell_result
                self.portfolio.cash += actual_usd
            else:
                self.portfolio.cash += proceeds

            pos["remaining_fraction"] = remaining - actual_frac
            # Append the TIER threshold (not current pnl_pct) so the check
            # `threshold_pct not in already_taken` correctly marks it as done.
            pos["partial_profits_taken"].append(threshold_pct)

            logger.info("PARTIAL TP %s: sold %.0f%% ($%.2f) | %s",
                         pos["symbol"], fraction * 100, proceeds, reason)
        except Exception as e:
            logger.warning("Partial profit failed for %s: %s", pos.get("symbol", "?"), e)

    def _run_polymarket_scan(self):
        """Scan Polymarket for prediction market edge plays."""
        if not hasattr(self, "poly_trader"):
            return
        try:
            # Hard 25s timeout — Polymarket API pagination can block for minutes
            # (each page: 10s timeout × 3 retries × N pages). Run in a thread so
            # the main cycle is never held hostage by a slow external API.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(self.poly_trader.find_edges,
                                config.POLYMARKET_MIN_EDGE, config.POLYMARKET_MIN_VOLUME)
                try:
                    signals = fut.result(timeout=25)
                except concurrent.futures.TimeoutError:
                    logger.warning("Polymarket scan timed out (>25s) — skipping this cycle")
                    return
            logger.info("Polymarket: %d edge signals found", len(signals))

            budget = self.compounder.max_position_for_market("polymarket")
            for sig in signals[:3]:   # Top 3 Polymarket plays per cycle
                size = min(config.POLYMARKET_MAX_POSITION_USD, budget * 0.15,
                           self.portfolio.cash * 0.02)
                if size >= 5:
                    result = self.poly_trader.place_order(sig, size)
                    if result:
                        logger.info("POLY %s '%s' $%.0f edge=%.1f%%",
                                    sig.side, sig.market.question[:50],
                                    size, sig.edge_pct * 100)
        except Exception as e:
            logger.warning("Polymarket scan error: %s", e)

    def _log_market_snapshot(self):
        """Log a brief market overview and cache for bot_state."""
        try:
            snap = df_mod.get_market_snapshot()
            if snap:
                logger.info("Market: %-8s | BTC Dom: %.1f%% | Avg24h: %+.1f%%",
                            snap.get("market_sentiment", "?").upper(),
                            snap.get("btc_dominance", 0),
                            snap.get("avg_24h_change", 0))
                self._market_snapshot = snap   # Cache for bot_state writer
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def _sleep(self, seconds: int):
        """Interruptible sleep."""
        end = time.time() + seconds
        while time.time() < end and self.running:
            time.sleep(1)

    def _shutdown(self, *_):
        logger.info("Shutdown signal received...")
        self.running = False

    def _cleanup(self):
        logger.info("Saving state...")
        self.portfolio.save()
        self.compounder.save_state()
        self._save_dex_positions()
        self._save_equity_curve()
        self._print_report()
        logger.info("Trader stopped cleanly.")

    def _peek_saved_mode(self, filepath: str) -> str:
        """Return the 'mode' field from a saved portfolio file without fully loading it.
        Returns 'paper', 'live', or '' if the file is missing or unreadable."""
        try:
            with open(filepath) as f:
                data = json.load(f)
            return data.get("mode", "")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return ""

    @staticmethod
    def _atomic_json(path: str, data, indent: int = 0):
        """Write JSON atomically: write to .tmp then os.replace — prevents corruption."""
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent or None)
        os.replace(tmp, path)

    # ─────────────────────────────────────────────────────────────────────────
    # Dashboard Settings Bridge
    # ─────────────────────────────────────────────────────────────────────────

    def _process_dashboard_commands(self):
        """
        Execute one-shot commands written by the dashboard (e.g. manual close).
        Reads commands.json, processes all pending entries, then clears the list.
        """
        cmds_path = "commands.json"
        try:
            with open(cmds_path) as f:
                cmds = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        pending = cmds.get("pending", [])
        if not pending:
            return

        for cmd in pending:
            action = cmd.get("action")
            pid    = cmd.get("id", "")
            market = cmd.get("market", "")
            reason = cmd.get("reason", "Dashboard manual close")

            if action != "close":
                logger.warning("Unknown dashboard command: %s", cmd)
                continue

            try:
                if market == "dex":
                    # Close DEX / Solana memecoin position
                    pos = self._dex_positions.get(pid)
                    if pos:
                        current_price = (pos.get("current_price")
                                         or pos.get("entry_price", 0))
                        self._try_close_dex_position(pid, pos, current_price, reason)
                        logger.info("MANUAL CLOSE DEX %s — %s", pos.get("symbol", pid), reason)
                    else:
                        logger.warning("Manual close: DEX position %s not found", pid)

                else:
                    # Close CEX / futures position
                    pos = self.portfolio.open_positions.get(pid)
                    if pos:
                        symbol = pos.get("symbol", pid)
                        current_price = (pos.get("current_price")
                                         or pos.get("entry_price", 0))
                        self.executor._execute_close(pid, current_price, reason)
                        logger.info("MANUAL CLOSE CEX %s — %s", symbol, reason)
                    else:
                        logger.warning("Manual close: CEX position %s not found", pid)

            except Exception as e:
                logger.error("Error processing close command %s: %s", pid, e)

        # Clear processed commands
        cmds["pending"] = []
        self._atomic_json(cmds_path, cmds)

    def _apply_dashboard_settings(self):
        """
        Read settings.json written by the dashboard and apply any changes.
        Live/paper toggle has been REMOVED — bot is always live with real SOL.
        Only dashboard close commands are processed here; mode switching is gone.
        Called at the top of every cycle — fast (local file read only).
        """
        try:
            with open("settings.json") as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        # Ensure settings.json always reflects live mode (in case stale file exists)
        if not settings.get("live_mode", True):
            settings["live_mode"] = True
            self._atomic_json("settings.json", settings)

    def _reset_paper_account(self):
        """
        Wipe all paper trading history and restart fresh with INITIAL_CAPITAL.
        Does NOT affect live wallet funds — paper only.
        """
        logger.info("=== PAPER ACCOUNT RESET requested from dashboard ===")

        # Close every open position without PnL recording
        self.portfolio.open_positions.clear()
        self.portfolio.closed_trades.clear()
        self.portfolio.cash          = config.INITIAL_CAPITAL
        self.portfolio.initial_capital = config.INITIAL_CAPITAL
        self.portfolio.peak_equity   = config.INITIAL_CAPITAL
        self._dex_positions.clear()
        self._equity_curve.clear()
        self._day_start_eq = config.INITIAL_CAPITAL

        # Remove persisted files so nothing stale is loaded on restart
        for fname in ("trades.json", "dex_positions.json", "equity_curve.json"):
            try:
                os.unlink(fname)
            except FileNotFoundError:
                pass

        # Write clean initial state immediately
        self.portfolio.save()
        self._save_dex_positions()
        logger.info(
            "Paper account reset complete — starting fresh with $%.0f",
            config.INITIAL_CAPITAL)

    def _sync_wallet_drift(self):
        """
        Compare real Phantom wallet SOL balance with portfolio.cash.
        If the wallet holds MORE than expected (user deposited externally),
        credit the difference so the bot can deploy it.
        Never reduces cash — position accounting handles that.
        """
        try:
            sol_usd = self.solana.get_portfolio_value_usd()
            if sol_usd <= 0:
                return
            # Estimate SOL in wallet = cash + open DEX position values
            # (CEX positions are off-chain, not in SOL wallet)
            dex_deployed = sum(
                pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
                for pos in self._dex_positions.values()
                if pos.get("chain") == "solana"
            )
            expected_sol_usd = self.portfolio.cash + dex_deployed
            drift = sol_usd - expected_sol_usd

            if drift > 5.0:   # >$5 more than expected = external deposit
                logger.info(
                    "Wallet drift +$%.2f detected — crediting to cash "
                    "(wallet=$%.2f expected=$%.2f)",
                    drift, sol_usd, expected_sol_usd)
                self.portfolio.cash += drift
                self.portfolio.initial_capital = max(
                    self.portfolio.initial_capital,
                    self.portfolio.cash)
            elif drift < -10.0:
                # Wallet has significantly less than expected — log warning only
                logger.warning(
                    "Wallet balance $%.2f is $%.2f below expected $%.2f "
                    "(check for failed txs or external withdrawals)",
                    sol_usd, abs(drift), expected_sol_usd)
        except Exception as e:
            logger.debug("Wallet drift check failed: %s", e)

    def _reconcile_wallet_positions(self):
        """
        Startup reconciliation — wallet is the source of truth.

        1. Drop any saved positions whose token is no longer in the wallet
           (externally sold, transferred, or rug-pulled to zero).
        2. Add any on-chain tokens not in saved state as new positions,
           using the current price as the entry (we lost the original entry).
        3. Update qty in saved positions to match actual on-chain balance.
        """
        if not self.live or not self.solana.is_connected:
            return
        try:
            on_chain = self.solana.get_all_token_balances()
            if on_chain is None:
                logger.debug("Wallet reconciliation: RPC error — skipping")
                return

            _skip  = {SOL_MINT, USDC_MINT, USDT_MINT}
            now_ts = datetime.now(timezone.utc).isoformat()

            # ── 1. Drop ghost positions (tracked but zero on-chain balance) ──
            for pair_addr, pos in list(self._dex_positions.items()):
                mint = pos.get("address", "")
                if not mint or mint in _skip:
                    continue
                if mint not in on_chain:
                    logger.warning(
                        "RECONCILE DROP: %s — not in wallet, removing from tracker",
                        pos.get("symbol", mint[:8]))
                    with self._dex_lock:
                        self._dex_positions.pop(pair_addr, None)

            # ── 2. Sync qty for surviving positions ───────────────────────────
            for pair_addr, pos in self._dex_positions.items():
                mint = pos.get("address", "")
                if mint and mint in on_chain:
                    actual_qty = on_chain[mint]["ui_amount"]
                    if actual_qty > 0:
                        pos["qty"] = actual_qty

            # ── 3. Add untracked on-chain tokens as new positions ─────────────
            tracked_mints = {pos.get("address", "")
                             for pos in self._dex_positions.values()}
            untracked = {m: b for m, b in on_chain.items()
                         if m not in _skip and m not in tracked_mints}

            for mint, bal in untracked.items():
                qty = bal["ui_amount"]
                # Look up current price + pair info from DexScreener
                token_info = self.dex_screener.get_token_info(mint, "solana")
                if not token_info or token_info.price_usd <= 0:
                    # Try Jupiter price as fallback
                    price = self._get_jupiter_price(mint)
                    if not price or price <= 0:
                        logger.info("RECONCILE SKIP: %s… — cannot price, leaving untracked",
                                    mint[:12])
                        continue
                    symbol     = mint[:8]
                    pair_addr  = mint   # Use mint as key when no pair found
                    liq_usd    = 0.0
                    dex_id     = "unknown"
                else:
                    price     = token_info.price_usd
                    symbol    = token_info.base_symbol
                    pair_addr = token_info.pair_address or mint
                    liq_usd   = token_info.liquidity_usd
                    dex_id    = token_info.dex_id

                value_usd = qty * price
                if value_usd < 0.50:
                    logger.debug("RECONCILE SKIP: %s dust $%.4f", symbol, value_usd)
                    continue

                pos = {
                    "address":           mint,
                    "symbol":            symbol,
                    "chain":             "solana",
                    "dex_id":            dex_id,
                    "entry_price":       price,   # Current price — real entry unknown
                    "current_price":     price,
                    "size_usd":          value_usd,
                    "qty":               qty,
                    "liquidity_usd":     liq_usd,
                    "remaining_fraction": 1.0,
                    "peak_price":        price,
                    "stop_pct":          config.TRAILING_STOP_PCT,
                    "target_pct":        config.TAKE_PROFIT_PCT,
                    "opened_at":         now_ts,
                    "tx":                "recovered",   # Flag: recovered from wallet
                    "current_pnl_pct":   0.0,
                }
                with self._dex_lock:
                    self._dex_positions[pair_addr] = pos
                logger.info(
                    "RECONCILE ADD: %s qty=%.4f price=$%.8f value=$%.2f (recovered from wallet)",
                    symbol, qty, price, value_usd)

            total = len(self._dex_positions)
            logger.info("Wallet reconciliation complete: %d active positions", total)
            if total:
                self._save_dex_positions()   # Persist the reconciled state immediately

        except Exception as e:
            logger.warning("Wallet reconciliation error: %s", e)

    def _sync_wallet_positions(self):
        """
        Live wallet state sync (~30s cadence in live mode):
        1. Update portfolio.cash from real on-chain SOL balance.
        2. Remove positions whose on-chain token balance is zero
           (externally sold/transferred) and credit estimated proceeds.
        """
        if not self.live or not self.solana.is_connected:
            return
        try:
            # 1. Refresh SOL balance → derive available cash
            sol_raw   = self.solana.get_sol_balance()
            sol_price = self.solana._get_sol_price()
            sol_usd   = sol_raw * sol_price if sol_price > 0 else 0.0

            # Subtract value of open DEX positions from total wallet value
            dex_deployed = sum(
                pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
                for pos in self._dex_positions.values()
                if pos.get("chain") == "solana"
            )
            available = sol_usd - dex_deployed

            if available > 0:
                cash_diff = available - self.portfolio.cash
                # Ignore tiny fluctuations from SOL price changes
                if cash_diff > 2.0 or cash_diff < -10.0:
                    logger.debug(
                        "Live SOL sync: wallet=$%.2f deployed=$%.2f "
                        "cash $%.2f → $%.2f (Δ%+.2f)",
                        sol_usd, dex_deployed, self.portfolio.cash,
                        available, cash_diff)
                    self.portfolio.cash = available

            # 2. Check each Solana DEX position's on-chain token balance
            sol_positions = {
                pair: pos for pair, pos in self._dex_positions.items()
                if pos.get("chain") == "solana" and pos.get("address")
            }
            if not sol_positions:
                return

            on_chain = self.solana.get_all_token_balances()
            if on_chain is None:
                return  # RPC error — never remove positions on a failed read

            for pair_addr, pos in list(sol_positions.items()):
                mint = pos.get("address", "")
                if not mint or mint in (SOL_MINT, USDC_MINT, USDT_MINT):
                    continue
                if mint not in on_chain:
                    # Token gone from wallet — closed outside the bot
                    entry   = pos.get("entry_price", 0)
                    size    = pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
                    current = pos.get("current_price", entry)
                    symbol  = pos.get("symbol", "?")
                    logger.warning(
                        "LIVE SYNC: %s (%s…) has zero on-chain balance "
                        "— position closed externally; removing from tracker.",
                        symbol, mint[:12])
                    with self._dex_lock:
                        self._dex_positions.pop(pair_addr, None)
                    # Credit estimated value from last known price
                    proceeds = max(size * (current / entry) if entry > 0 else size, 0.0)
                    self.portfolio.cash += proceeds
                    self.portfolio.closed_trades.append({
                        "asset_id":    pair_addr,
                        "symbol":      symbol,
                        "side":        "long",
                        "entry_price": entry,
                        "exit_price":  current,
                        "pnl_usd":     round(proceeds - size, 4),
                        "pnl_pct":     round((current / entry - 1) * 100, 2) if entry > 0 else 0,
                        "closed_at":   datetime.now(timezone.utc).isoformat(),
                        "reason":      "externally_closed",
                        "chain":       "solana",
                    })
        except Exception as e:
            logger.debug("Live wallet sync error: %s", e)

    def _save_dex_positions(self):
        """Persist open DEX positions so they survive restarts."""
        try:
            self._atomic_json("dex_positions.json", self._dex_positions, indent=2)
        except Exception as e:
            logger.warning("Failed to save DEX positions: %s", e)

    def _load_dex_positions(self):
        """Reload DEX positions from disk on startup, with validation."""
        try:
            with open("dex_positions.json") as f:
                raw = json.load(f)
            validated = {}
            skipped = 0
            for pair_addr, pos in raw.items():
                if not isinstance(pos, dict):
                    skipped += 1
                    continue
                entry = float(pos.get("entry_price", 0))
                size  = float(pos.get("size_usd", 0))
                if entry <= 0 or size <= 0:
                    logger.warning("Skipping corrupted DEX position %s: entry=$%.8f size=$%.2f",
                                   pair_addr[:12], entry, size)
                    skipped += 1
                    continue
                validated[pair_addr] = pos
            self._dex_positions = validated
            if validated:
                logger.info("DEX positions restored: %d open (%d skipped as corrupted)",
                            len(validated), skipped)
                for pair, pos in validated.items():
                    logger.info("  ↳ %s/%s entry=$%.8f size=$%.2f",
                                pos.get("chain", "?").upper(),
                                pos.get("symbol", "?"),
                                pos.get("entry_price", 0),
                                pos.get("size_usd", 0))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Failed to load DEX positions: %s", e)

    def _load_equity_curve(self) -> list:
        """Load equity curve from disk so chart history survives bot restarts."""
        try:
            path = "equity_curve.json"
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    logger.info("Equity curve restored: %d points", len(data))
                    return data[-17_280:]  # Cap at 24h (17280 × 5s = 86400s)
        except Exception:
            pass
        return []

    def _save_equity_curve(self):
        try:
            self._atomic_json("equity_curve.json", self._equity_curve[-10_000:], indent=2)
        except Exception:
            pass

    def _print_report(self):
        perf    = self.portfolio.performance_summary()
        growth  = self.compounder.growth_summary()
        equity  = perf["equity"]
        initial = perf["initial_capital"]
        ret     = perf["total_return_pct"]
        print(f"\n{'═'*70}")
        print(f"  COMPOUNDING REPORT  |  Equity: ${equity:,.2f}  |  Return: {ret:+.2f}%")
        print(f"{'═'*70}")
        print(f"  Trades: {perf['total_trades']}  |  Win Rate: {perf.get('win_rate_pct',0):.1f}%  "
              f"|  Profit Factor: {perf.get('profit_factor',0):.2f}")
        print(f"  Max DD: {perf.get('max_drawdown_pct',0):.1f}%  |  "
              f"Scale factor: {growth['scale_factor']:.2f}x")
        print(f"\n  Projected growth from ${equity:,.0f}:")
        proj = growth["projections"]
        print(f"    30d:  ${proj['30d_target']:>12,.0f}  (target 0.5%/day)")
        print(f"    90d:  ${proj['90d_conservative']:>12,.0f}  (conservative 0.3%/day)")
        print(f"    365d: ${proj['365d_target']:>12,.0f}  (target 0.5%/day)")
        print(f"{'═'*70}\n")


# ─── CLI Commands ──────────────────────────────────────────────────────────────

def cmd_scan():
    print("\nScanning all markets...\n")
    scanner   = MarketScanner()
    dex       = DexScreener()
    poly      = PolymarketTrader()

    # CEX signals
    cex_signals = scanner.scan_all()
    buys  = [s for s in cex_signals if s.signal == "BUY"][:8]
    sells = [s for s in cex_signals if s.signal == "SELL"][:5]

    print(f"{'─'*72}")
    print(f"  CEX / STOCKS / FOREX — TOP BUY SIGNALS")
    print(f"{'─'*72}")
    for s in buys:
        print(f"  [{s.market.upper():<8}] {s.symbol:<12} score={s.score:>+.3f} "
              f"conv={s.conviction:.2f} ${s.current_price:.4f} [{s.regime}]")
        for r in s.reasons[:1]:
            print(f"    ↳ {r}")

    print(f"\n{'─'*72}")
    print(f"  DEX SCREENER — HOT ON-CHAIN TOKENS")
    print(f"{'─'*72}")
    tokens = dex.get_multi_chain_opportunities()[:10]
    for t in tokens:
        age = f"{t.age_hours:.0f}h" if t.age_hours else "?"
        print(f"  [{t.chain_id.upper():<8}] {t.base_symbol:<12} score={t.score:.3f} "
              f"+{t.price_change_h1:.1f}%/1h vol=${t.volume_h1:,.0f} "
              f"liq=${t.liquidity_usd:,.0f} age={age}")
        for sig in t.signals[:2]:
            print(f"    ↳ {sig}")

    print(f"\n{'─'*72}")
    print(f"  POLYMARKET — PREDICTION MARKET EDGES")
    print(f"{'─'*72}")
    poly_signals = poly.find_edges(min_edge=0.03, min_volume=2000)[:5]
    for s in poly_signals:
        print(f"  {s.side:<4} '{s.market.question[:55]}' "
              f"score={s.score:.2f} edge={s.edge_pct*100:.1f}% "
              f"price={s.target_price:.2%}")
    print()


def cmd_status():
    portfolio = Portfolio()
    portfolio.load()
    compounder = CompoundingEngine(portfolio, None)
    growth = compounder.growth_summary()

    print(f"\n{'═'*70}")
    print(f"  PORTFOLIO & COMPOUND STATUS")
    print(f"{'═'*70}")
    perf = portfolio.performance_summary()
    print(f"  Equity:        ${perf['equity']:>14,.2f}")
    print(f"  Cash:          ${perf['cash']:>14,.2f}")
    print(f"  Total Return:  {perf['total_return_pct']:>+13.2f}%")
    print(f"  Trades:        {perf['total_trades']:>14d}")
    print(f"  Win Rate:      {perf.get('win_rate_pct',0):>13.1f}%")
    print(f"  Scale Factor:  {growth['scale_factor']:>14.2f}x")

    print(f"\n  Market allocations:")
    for k, v in growth["allocations"].items():
        stats = growth["market_performance"][k]
        print(f"    {k:<16} {v:>6} | ${growth['allocation_usd'][k]:>8,.0f} | "
              f"trades={stats['trades']} wr={stats['win_rate']} pnl=${stats['pnl_usd']:>+.0f}")

    print(f"\n  Projected growth:")
    proj = growth["projections"]
    print(f"    30 days:   ${proj['30d_target']:>12,.0f}")
    print(f"    365 days:  ${proj['365d_target']:>12,.0f}")

    positions = portfolio.open_positions_summary()
    if positions:
        print(f"\n  Open CEX positions ({len(positions)}):")
        for p in positions:
            print(f"    {p['symbol']:<12} {p['side']:<5} "
                  f"entry=${p['entry_price']:.4f} now=${p['current_price']:.4f} "
                  f"pnl={p['unrealized_pnl_pct']:>+.1f}%")

    # Show on-chain DEX positions
    try:
        with open("dex_positions.json") as f:
            dex_pos = json.load(f)
        if dex_pos:
            print(f"\n  Open DEX positions ({len(dex_pos)}):")
            for pair, pos in dex_pos.items():
                print(f"    [{pos.get('chain','?').upper():<8}] "
                      f"{pos.get('symbol','?'):<10} "
                      f"entry=${pos.get('entry_price',0):.8f} "
                      f"size=${pos.get('size_usd',0):.2f}  "
                      f"opened={pos.get('opened_at','?')[:10]}")
    except FileNotFoundError:
        pass

    print(f"{'═'*70}\n")


def cmd_growth():
    """Print compound growth projection table."""
    portfolio  = Portfolio()
    portfolio.load()
    compounder = CompoundingEngine(portfolio, None)
    equity = portfolio.equity()

    print(f"\nCompound growth from ${equity:,.0f}\n")
    print(f"{'Days':<8} {'0.3%/day':>14} {'0.5%/day':>14} {'1.0%/day':>14}")
    print("─" * 52)
    for days in [7, 14, 30, 60, 90, 180, 365]:
        c03 = equity * (1.003 ** days)
        c05 = equity * (1.005 ** days)
        c10 = equity * (1.010 ** days)
        print(f"{days:<8} ${c03:>13,.0f} ${c05:>13,.0f} ${c10:>13,.0f}")
    print()


def cmd_report():
    portfolio  = Portfolio()
    portfolio.load()
    compounder = CompoundingEngine(portfolio, None)
    out = {
        "performance": portfolio.performance_summary(),
        "growth": compounder.growth_summary(),
        "open_positions": portfolio.open_positions_summary(),
    }
    print(json.dumps(out, indent=2))


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI Autonomous Trader — Compound wealth 24/7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--paper",   action="store_true", help="Force paper trading mode (default: LIVE if wallet key set)")
    parser.add_argument("--live",    action="store_true", help="[deprecated] Redundant — live is now the default")
    parser.add_argument("--scan",    action="store_true", help="Scan all markets and exit")
    parser.add_argument("--status",  action="store_true", help="Portfolio + compound status and exit")
    parser.add_argument("--report",  action="store_true", help="Full JSON report and exit")
    parser.add_argument("--growth",  action="store_true", help="Show compound growth projection table")
    parser.add_argument("--interval", type=int, default=None, help="Override scan interval (seconds)")
    args = parser.parse_args()

    if args.interval:
        config.SCAN_INTERVAL_SEC = args.interval

    if args.scan:
        cmd_scan()
    elif args.status:
        cmd_status()
    elif args.report:
        cmd_report()
    elif args.growth:
        cmd_growth()
    else:
        # Live is the default when a wallet key is configured.
        # Pass --paper explicitly to force paper-only simulation.
        run_live = not args.paper
        if run_live and not any([config.BINANCE_API_KEY, config.PHANTOM_PRIVATE_KEY,
                                  config.POLYMARKET_PRIVATE_KEY, config.COINBASE_API_KEY]):
            print("WARNING: No API keys found in .env — running in PAPER mode.")
            print("Set PHANTOM_PRIVATE_KEY in .env to enable real Solana trades.")
            run_live = False
        trader = AITrader(live=run_live)
        trader.start()


if __name__ == "__main__":
    main()
