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

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config
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
from solana_wallet import SolanaWallet
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
║       AUTONOMOUS AI TRADER  v3.0  —  LEVERAGED 24/7              ║
╠══════════════════════════════════════════════════════════════════╣
║  CEX │ Futures(2-8x) │ Scalps(5m) │ DEX │ Poly │ Stocks │ Forex  ║
║  Mode: {mode:<8}    Capital: ${capital:<12,.0f}                  ║
║  Scan: 15s cycle │ Scalp: 30s │ Futures: 60s │ DEX: 45s          ║
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
        # Polymarket disabled until API keys are configured
        # self.poly_trader = PolymarketTrader(private_key=config.POLYMARKET_PRIVATE_KEY)
        self.solana       = SolanaWallet(
            private_key_b58=config.PHANTOM_PRIVATE_KEY)              # Phantom wallet

        # ── WebSocket real-time feed ──────────────────────────────────────
        self._ws_feed = df_mod.get_ws_feed()   # Start WebSocket in background

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
        self._last_wallet_sync = 0.0   # Wallet drift check every 5 minutes
        self._day_start_eq   = self.portfolio.equity()
        self._last_day       = datetime.now(timezone.utc).date()
        self._equity_curve   = self._load_equity_curve()  # Persist chart history across restarts
        self._dex_positions: dict = {}  # addr → {buy_price, qty, chain, symbol}
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
        self.risk_mgr.reset_daily_loss_tracker()

        # Initialise settings.json if missing
        if not os.path.exists("settings.json"):
            self._atomic_json("settings.json", {
                "live_mode": True,
                "reset_paper": False,
            })

        # Sync capital with Phantom wallet whenever this is a fresh live start
        # (no prior live trades.json) OR we just wiped paper data above.
        _fresh_live = live and (not os.path.exists(config.TRADE_LOG_FILE) or _is_paper_data)
        if self.solana.is_connected and _fresh_live:
            try:
                sol_bal = self.solana.get_sol_balance()
                sol_usd = self.solana.get_portfolio_value_usd()
                if sol_usd > 0.50:
                    self.portfolio.cash            = sol_usd
                    self.portfolio.initial_capital  = sol_usd
                    self.portfolio.peak_equity      = sol_usd
                    self._day_start_eq              = sol_usd
                    # Reset daily loss tracker AFTER wallet sync so the baseline
                    # is the real SOL balance — not the fake $100k config default.
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
        print(BANNER.format(mode=mode, capital=equity))

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

        # ── PAUSED check ──────────────────────────────────────────────────
        if os.path.exists("PAUSED"):
            logger.info("Bot is PAUSED (remove PAUSED file to resume)")
            return

        self._cycle += 1
        t0 = time.time()
        now_dt = datetime.now(timezone.utc)
        now    = now_dt.strftime("%H:%M:%S UTC")
        # Real equity = cash (SOL in wallet) + open DEX position values
        dex_value = sum(
            pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
            * (1 + pos.get("current_pnl_pct", 0))
            for pos in self._dex_positions.values()
        )
        equity = self.portfolio.cash + dex_value
        logger.info("━━━ Cycle #%d  %s  SOL wallet: $%.2f  DEX positions: $%.2f  Total: $%.2f ━━━",
                    self._cycle, now, self.portfolio.cash, dex_value, equity)

        # ── Midnight reset: daily loss tracker + optional wallet sync ─────
        today = now_dt.date()
        if today != self._last_day:
            self._last_day = today
            self.risk_mgr.reset_daily_loss_tracker()
            # Reset daily P&L baseline with true equity (CEX + DEX)
            dex_val = sum(
                p.get("size_usd", 0) * p.get("remaining_fraction", 1.0)
                * (1 + p.get("current_pnl_pct", 0))
                for p in self._dex_positions.values()
            )
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
        # ── 13. Equity snapshot (cap in-memory at 2880 = 24h of 30s cycles) ──
        self._equity_curve.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "equity": round(equity, 2),
            "cycle": self._cycle,
        })
        if len(self._equity_curve) > 2_880:
            self._equity_curve = self._equity_curve[-2_880:]

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

        # ── 17. Wallet drift sync (every 5 min) ───────────────────────────
        # Detect if user deposited more SOL externally and credit it as cash.
        if (self.live and self.solana.is_connected
                and time.time() - self._last_wallet_sync > 300):
            self._sync_wallet_drift()
            self._last_wallet_sync = time.time()

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
            # Live wallet balances for dashboard — cached 60s to avoid RPC rate limits
            _sol, _usdc, _sol_usd, _cache_ts = self._wallet_balance_cache
            if self.solana.is_connected and (time.time() - _cache_ts > 60):
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
                # Recent closed trades (last 30) — written every cycle so dashboard
                # is always current, regardless of trades.json save frequency.
                "recent_trades": list(self.portfolio.closed_trades)[-30:],
                "signal_table": signal_table,
                # DEX positions detail for dashboard
                "dex_position_details": [
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
                    for pos in self._dex_positions.values()
                ],
            }
            self._atomic_json("bot_state.json", state)
            # Write equity curve every cycle so chart stays live (not just every 5 min)
            self._save_equity_curve()
            # Write portfolio + DEX positions every cycle for live position cards
            self.portfolio.save()
            self._save_dex_positions()
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
        """DEX Screener scan with safety checks, vol-adjusted sizing, concentration limits."""
        try:
            # Skip DEX scan on circuit breaker conditions
            if self.risk_mgr._max_drawdown_triggered():
                logger.info("DEX scan skipped: max drawdown guard active")
                return
            if self.risk_mgr._daily_loss_triggered():
                logger.info("DEX scan skipped: daily loss limit active")
                return

            tokens = self.dex_screener.get_multi_chain_opportunities()
            logger.info("DEX scan: %d opportunities found", len(tokens))

            budget = self.compounder.max_position_for_market("crypto_dex")
            traded = 0

            # Build set of symbols already held (catches same token on different pairs)
            held_symbols = {pos.get("symbol", "").upper()
                            for pos in self._dex_positions.values()}

            for token in tokens[:8]:   # Check top 8 (some filtered by safety)
                if token.score < config.DEX_MIN_SCORE:
                    continue
                if token.pair_address in self._dex_positions:
                    continue
                if token.base_symbol.upper() in held_symbols:
                    continue   # Already hold this token on a different pair
                if traded >= 2:
                    break

                # Concentration check
                allowed, reason = self.risk_mgr.check_dex_concentration(
                    self._dex_positions, token.dex_id)
                if not allowed:
                    logger.info("DEX concentration: %s", reason)
                    break

                # Safety report (already computed during scoring, use cached)
                safety = getattr(token, 'safety_report', None)
                if safety and not safety.is_safe_to_trade:
                    logger.warning("SKIP %s: %s - %s", token.base_symbol,
                                   safety.risk_level, ", ".join(safety.risk_flags[:2]))
                    continue

                # Volatility-adjusted sizing
                safety_score = safety.safety_score if safety else 0.5
                size_usd = self.risk_mgr.dex_position_size_usd(
                    token_score=token.score,
                    safety_score=safety_score,
                    liquidity_usd=token.liquidity_usd,
                    price_change_h1=token.price_change_h1,
                    price_change_h6=token.price_change_h6,
                )
                size_usd = min(size_usd, budget * 0.20, config.DEX_MAX_POSITION_USD)
                if size_usd < config.DEX_MIN_POSITION_USD:
                    continue

                self._open_dex_position(token, size_usd, safety)
                traded += 1

        except Exception as e:
            logger.warning("DEX scan error: %s", e)

    def _open_dex_position(self, token, size_usd: float, safety=None):
        """Open a DEX position with safety verification and MEV protection."""
        try:
            # ── Cash gate: ensure we can actually afford this ────────────────
            if size_usd > self.portfolio.cash * 0.99:
                logger.warning("Insufficient cash $%.2f for DEX buy $%.2f — skipping",
                               self.portfolio.cash, size_usd)
                return

            # ── Live wallet: verify SOL balance before attempting swap ───────
            if token.chain_id == "solana" and self.solana.is_connected:
                sol_bal_usd = self.solana.get_portfolio_value_usd()
                if sol_bal_usd < size_usd * 1.05:   # Need trade size + ~5% for fees
                    logger.warning("SOL balance $%.2f insufficient for $%.2f trade — skipping",
                                   sol_bal_usd, size_usd)
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
            )
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
            }

            if token.chain_id == "solana" and self.solana.is_connected:
                # Pass liquidity so wallet can compute dynamic slippage
                tx = self.solana.safe_buy_token(
                    token.base_address, size_usd,
                    safety_report=safety,
                    liquidity_usd=token.liquidity_usd)
                if tx and not tx.startswith("paper_"):
                    pos_data["tx"] = tx
                    self._dex_positions[token.pair_address] = pos_data
                    self.portfolio.cash -= size_usd
                    risk_str = safety.risk_level if safety else "?"
                    logger.info("DEX BUY %s $%.2f @ $%.8f score=%.2f safety=%s | tx=%s",
                                token.base_symbol, size_usd, token.price_usd,
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
                    continue

                # Batch-fetch all held Solana token prices in one API call
                mints = [pos["address"] for pos in self._dex_positions.values()
                         if pos.get("chain") == "solana"]
                if not mints:
                    continue

                prices = be.get_multi_price(mints)

                # Update each position with fresh price
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

                    # Spike exit: ≥15% surge in one 3s interval while in profit
                    prev = pos.get("fast_prev_price", entry)
                    if prev > 0 and pnl_pct > 0:
                        surge = (current - prev) / prev
                        if surge >= 0.12:   # 12% in 3s = pump — capture it
                            to_close.append(
                                (pair_addr, pos, current,
                                 f"FastMonitor spike: +{surge:.0%} in 3s (PnL +{pnl_pct:.0%})")
                            )
                            continue
                    pos["fast_prev_price"] = current

                    # Instant stop-loss check
                    stop_pct = pos.get("stop_pct", 0.20)
                    if pnl_pct <= -stop_pct:
                        to_close.append(
                            (pair_addr, pos, current,
                             f"FastMonitor stop-loss {pnl_pct:.0%}")
                        )

                for pair_addr, pos, current, reason in to_close:
                    if pair_addr in self._dex_positions:
                        logger.info("FAST EXIT %s @ $%.8f | %s",
                                    pos.get("symbol", "?"), current, reason)
                        ok = self._close_dex_position(pair_addr, pos, current, reason)
                        if ok is not False:
                            self._dex_positions.pop(pair_addr, None)

            except Exception as e:
                logger.debug("FastPriceMonitor error: %s", e)

    def _update_dex_positions(self):
        """Check open DEX positions for time exits, partial profits, and stop/TP."""
        closed = []
        for pair_addr, pos in list(self._dex_positions.items()):
            try:
                token = self.dex_screener.get_token_info(pos["address"], pos["chain"])
                if not token or token.price_usd <= 0:
                    continue

                entry   = pos["entry_price"]
                current = token.price_usd
                pnl_pct = (current - entry) / entry if entry > 0 else 0
                pos["current_price"]  = current
                pos["current_pnl_pct"] = pnl_pct
                # Refresh liquidity for dynamic slippage calculations on exit
                if token.liquidity_usd > 0:
                    pos["liquidity_usd"] = token.liquidity_usd

                # Update trailing high
                pos["peak_price"] = max(pos.get("peak_price", entry), current)
                peak = pos["peak_price"]
                trail_pct = (peak - current) / peak if peak > 0 else 0

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
                        closed_ok = self._close_dex_position(
                            pair_addr, pos, current, spike_reason)
                        if closed_ok is not False:
                            closed.append(pair_addr)
                        continue
                pos["prev_price"] = current

                # 1. TIME-BASED exits
                should_exit, time_reason = self.risk_mgr.check_time_exit(pos)
                if should_exit:
                    closed_ok = self._close_dex_position(pair_addr, pos, current, time_reason)
                    if closed_ok is not False:
                        closed.append(pair_addr)
                    continue

                # 2. DUST CLEANUP — remaining fraction too small to trade meaningfully
                remaining_now = pos.get("remaining_fraction", 1.0)
                if remaining_now < 0.02:
                    closed_ok = self._close_dex_position(pair_addr, pos, current, "Dust cleanup (<2% remaining)")
                    if closed_ok is not False:
                        closed.append(pair_addr)
                    continue

                # 3. PARTIAL PROFIT-TAKING
                sell_frac, partial_reason, partial_threshold = self.risk_mgr.get_partial_profit_action(pos)
                if sell_frac is not None:
                    self._execute_partial_profit(pair_addr, pos, sell_frac, partial_reason, partial_threshold)

                # 4. FULL EXIT conditions
                remaining = pos.get("remaining_fraction", 1.0)
                # Raise target as partials are taken
                adj_target = pos["target_pct"] * (1 + (1 - remaining) * 0.5)

                # Trailing stop: adaptive to the position's own stop distance
                # Only kicks in after we're in profit by at least 50% of the stop
                pos_stop  = pos.get("stop_pct", 0.20)
                trail_thr = pos_stop * 0.80   # e.g. 24% trail for a 30% stop
                profit_thr = pos_stop * 0.50  # must be in profit before trailing
                reason = None
                if pnl_pct >= adj_target:
                    reason = f"Take profit +{pnl_pct:.0%}"
                elif pnl_pct <= -pos_stop:
                    reason = f"Stop loss {pnl_pct:.0%}"
                elif trail_pct > trail_thr and pnl_pct > profit_thr:
                    reason = f"Trailing stop (peak=${peak:.8f}, trail={trail_pct:.0%})"

                if reason:
                    closed_ok = self._close_dex_position(pair_addr, pos, current, reason)
                    if closed_ok is not False:
                        closed.append(pair_addr)

            except Exception as e:
                logger.debug("DEX position update error: %s", e)

        for addr in closed:
            self._dex_positions.pop(addr, None)

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
                                                liquidity_usd=liq_usd)
            if sell_result:
                _sig, actual_usd = sell_result
                self.portfolio.cash += actual_usd
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

        self.portfolio.closed_trades.append({
            "asset_id": pair_addr,
            "symbol": pos["symbol"],
            "market": "dex",
            "side": "long",
            "entry_price": entry,
            "exit_price": current_price,
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct * 100, 2),
            "close_reason": reason,
            "safety_score": pos.get("safety_score"),
            "opened_at": pos["opened_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })
        # Enforce memory cap (same as portfolio.close_position does for CEX trades)
        from portfolio import _MAX_CLOSED_TRADES_MEMORY
        if len(self.portfolio.closed_trades) > _MAX_CLOSED_TRADES_MEMORY:
            self.portfolio._archive_old_trades()

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
                                                             liquidity_usd=liq_usd)
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
                        self._close_dex_position(pid, pos, current_price, reason)
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
                    return data[-2_880:]   # Cap at 24h
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
