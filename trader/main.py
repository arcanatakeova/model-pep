"""
AI Trader — Autonomous Multi-Market Trading Bot
================================================
Markets: Cryptocurrency, Forex, Stocks/ETFs
Mode:    Paper trading (default) | Live trading (requires API keys)

Usage:
    python main.py                    # Run live bot (paper trading)
    python main.py --backtest         # Run in analysis-only mode (no trades)
    python main.py --scan             # Single scan, print signals, then exit
    python main.py --status           # Print portfolio status then exit
    python main.py --report           # Print full performance report then exit
"""
import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import config
from portfolio import Portfolio
from risk_manager import RiskManager
from executor import TradeExecutor
from strategies import MarketScanner
import data_fetcher as df_mod

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)-8s] %(name)-20s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, mode="a"),
    ],
)
logger = logging.getLogger("ai_trader")


# ─── Banner ───────────────────────────────────────────────────────────────────

BANNER = r"""
╔══════════════════════════════════════════════════════════╗
║           AI AUTONOMOUS TRADER  v1.0                     ║
║   Markets: Crypto | Forex | Stocks                       ║
║   Mode: {mode:<8}  Capital: ${capital:<10,.0f}           ║
╚══════════════════════════════════════════════════════════╝
"""


def print_banner(mode: str, capital: float):
    print(BANNER.format(mode=mode, capital=capital))


# ─── Core Bot ─────────────────────────────────────────────────────────────────

class AITrader:
    """
    Main trading loop.
    Orchestrates: scan → signal → execute → manage → repeat.
    """

    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.running    = False

        # Core components
        self.portfolio = Portfolio(config.INITIAL_CAPITAL)
        self.risk_mgr  = RiskManager(self.portfolio)
        self.executor  = TradeExecutor(self.portfolio, self.risk_mgr)
        self.scanner   = MarketScanner()

        self._cycle_count = 0
        self._last_save   = time.time()
        self._snapshot_equity: list[dict] = []

        # Load previous state
        self.portfolio.load()
        self.risk_mgr.reset_daily_loss_tracker()

    def start(self):
        """Start the main trading loop."""
        self.running = True
        mode = "PAPER" if self.paper_mode else "LIVE"
        print_banner(mode, self.portfolio.equity())

        logger.info("=" * 60)
        logger.info("AI Trader starting | Mode: %s | Equity: $%.2f",
                    mode, self.portfolio.equity())
        logger.info("Watching: %d crypto, %d forex pairs, %d stocks",
                    len(config.CRYPTO_WATCHLIST) + config.CRYPTO_TOP_N,
                    len(config.FOREX_PAIRS),
                    len(config.STOCK_WATCHLIST))
        logger.info("=" * 60)

        # Graceful shutdown on Ctrl+C / SIGTERM
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        while self.running:
            try:
                self._run_cycle()
                self._sleep_until_next_cycle()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Unhandled error in main loop: %s", e, exc_info=True)
                time.sleep(30)

        self._cleanup()

    def _run_cycle(self):
        self._cycle_count += 1
        cycle_start = time.time()
        logger.info("─── Cycle #%d started at %s ───",
                    self._cycle_count, datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))

        # ── 1. Update existing positions (trailing stops, mark-to-market) ──
        logger.info("Updating %d open positions...", len(self.portfolio.open_positions))
        self.executor.update_all_positions()

        # ── 2. Market overview ─────────────────────────────────────────────
        snapshot = df_mod.get_market_snapshot()
        if snapshot:
            logger.info("Market: %s | BTC Dom: %.1f%% | Avg 24h: %+.2f%%",
                        snapshot.get("market_sentiment", "?").upper(),
                        snapshot.get("btc_dominance", 0),
                        snapshot.get("avg_24h_change", 0))

        # ── 3. Scan all markets ────────────────────────────────────────────
        logger.info("Scanning markets...")
        signals = self.scanner.scan_all()

        # ── 4. Process actionable signals ─────────────────────────────────
        actionable = [s for s in signals if s.signal != "HOLD"]
        logger.info("Found %d actionable signals (BUY/SELL) out of %d total",
                    len(actionable), len(signals))

        trades_opened = 0
        for signal in actionable:
            result = self.executor.process_signal(signal)
            if result:
                trades_opened += 1

        # ── 5. Risk report ─────────────────────────────────────────────────
        risk = self.risk_mgr.risk_report()
        logger.info("Portfolio: equity=$%.2f | cash=$%.2f | positions=%d/%d | dd=%.1f%%",
                    risk["equity"], risk["cash"],
                    risk["open_positions"], risk["max_positions"],
                    risk["current_drawdown_pct"])

        # ── 6. Snapshot for equity curve ──────────────────────────────────
        self._snapshot_equity.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "equity": risk["equity"],
            "cash": risk["cash"],
            "open": risk["open_positions"],
        })

        # ── 7. Periodic save ───────────────────────────────────────────────
        if time.time() - self._last_save > config.PORTFOLIO_SNAPSHOT_INTERVAL:
            self.portfolio.save()
            self._save_equity_curve()
            self._last_save = time.time()

        cycle_time = time.time() - cycle_start
        logger.info("Cycle #%d done in %.1fs | Trades opened: %d",
                    self._cycle_count, cycle_time, trades_opened)

    def _sleep_until_next_cycle(self):
        logger.info("Next scan in %ds...", config.SCAN_INTERVAL_SEC)
        remaining = config.SCAN_INTERVAL_SEC
        while remaining > 0 and self.running:
            time.sleep(min(10, remaining))
            remaining -= 10

    def _shutdown(self, signum, frame):
        logger.info("Shutdown signal received, stopping gracefully...")
        self.running = False

    def _cleanup(self):
        logger.info("Saving portfolio state...")
        self.portfolio.save()
        self._save_equity_curve()
        self._print_final_report()

    def _save_equity_curve(self):
        try:
            with open("equity_curve.json", "w") as f:
                json.dump(self._snapshot_equity, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save equity curve: %s", e)

    def _print_final_report(self):
        perf = self.portfolio.performance_summary()
        print("\n" + "=" * 60)
        print("  FINAL PERFORMANCE REPORT")
        print("=" * 60)
        print(f"  Equity:        ${perf['equity']:>12,.2f}")
        print(f"  Total Return:  {perf['total_return_pct']:>+11.2f}%")
        print(f"  Total Trades:  {perf['total_trades']:>12d}")
        print(f"  Win Rate:      {perf.get('win_rate_pct', 0):>11.1f}%")
        print(f"  Profit Factor: {perf.get('profit_factor', 0):>12.2f}")
        print(f"  Max Drawdown:  {perf.get('max_drawdown_pct', 0):>+11.2f}%")
        print(f"  Open Positions:{len(self.portfolio.open_positions):>12d}")
        if "markets" in perf:
            print("\n  By Market:")
            for market, stats in perf["markets"].items():
                print(f"    {market:<10}: {stats['trades']:>3} trades | "
                      f"WR: {stats.get('win_rate_pct', 0):>5.1f}% | "
                      f"PnL: ${stats['pnl_usd']:>+10.2f}")
        print("=" * 60 + "\n")


# ─── CLI Commands ──────────────────────────────────────────────────────────────

def cmd_scan():
    """Single scan: print top signals and exit."""
    print("Scanning markets...\n")
    scanner = MarketScanner()
    signals = scanner.scan_all()

    buy_signals  = [s for s in signals if s.signal == "BUY"][:10]
    sell_signals = [s for s in signals if s.signal == "SELL"][:10]

    print(f"{'─'*70}")
    print(f"  TOP BUY SIGNALS  ({len([s for s in signals if s.signal == 'BUY'])} total)")
    print(f"{'─'*70}")
    for s in buy_signals:
        print(f"  [{s.market.upper():<8}] {s.symbol:<12} | Score: {s.score:>+.3f} | "
              f"Conv: {s.conviction:.2f} | ${s.current_price:.4f} | {s.regime}")
        for r in s.reasons[:2]:
            print(f"             ↳ {r}")

    print(f"\n{'─'*70}")
    print(f"  TOP SELL SIGNALS  ({len([s for s in signals if s.signal == 'SELL'])} total)")
    print(f"{'─'*70}")
    for s in sell_signals:
        print(f"  [{s.market.upper():<8}] {s.symbol:<12} | Score: {s.score:>+.3f} | "
              f"Conv: {s.conviction:.2f} | ${s.current_price:.4f} | {s.regime}")
        for r in s.reasons[:2]:
            print(f"             ↳ {r}")
    print()


def cmd_status():
    """Print portfolio status and exit."""
    portfolio = Portfolio()
    portfolio.load()

    print(f"\n{'═'*60}")
    print("  PORTFOLIO STATUS")
    print(f"{'═'*60}")
    perf = portfolio.performance_summary()
    print(f"  Equity:     ${perf['equity']:>12,.2f}")
    print(f"  Cash:       ${perf['cash']:>12,.2f}")
    print(f"  Return:     {perf['total_return_pct']:>+11.2f}%")
    print(f"  Trades:     {perf['total_trades']:>12d}")

    positions = portfolio.open_positions_summary()
    if positions:
        print(f"\n  Open Positions ({len(positions)}):")
        print(f"  {'Symbol':<12} {'Side':<6} {'Entry':>10} {'Current':>10} {'PnL%':>8}")
        print(f"  {'─'*50}")
        for pos in positions:
            print(f"  {pos['symbol']:<12} {pos['side']:<6} "
                  f"${pos['entry_price']:>9.4f} ${pos['current_price']:>9.4f} "
                  f"{pos['unrealized_pnl_pct']:>+7.2f}%")
    else:
        print("\n  No open positions.")
    print(f"{'═'*60}\n")


def cmd_report():
    """Print full performance report and exit."""
    portfolio = Portfolio()
    portfolio.load()
    perf = portfolio.performance_summary()
    print(json.dumps(perf, indent=2))


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI Autonomous Trader — Multi-market trading bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                 # Start paper trading bot (default)
  python main.py --live          # Start live trading (requires API keys in .env)
  python main.py --scan          # One-shot market scan, print signals, exit
  python main.py --status        # Print portfolio status, exit
  python main.py --report        # Full JSON performance report, exit
        """,
    )
    parser.add_argument("--live",     action="store_true", help="Enable live trading")
    parser.add_argument("--scan",     action="store_true", help="One-shot scan and exit")
    parser.add_argument("--status",   action="store_true", help="Print portfolio status and exit")
    parser.add_argument("--report",   action="store_true", help="Print performance report and exit")
    parser.add_argument("--interval", type=int, default=None,
                        help=f"Override scan interval in seconds (default: {config.SCAN_INTERVAL_SEC})")
    args = parser.parse_args()

    if args.interval:
        config.SCAN_INTERVAL_SEC = args.interval

    if args.live:
        if not (config.BINANCE_API_KEY or config.COINBASE_API_KEY):
            print("ERROR: Live trading requires API keys. Set BINANCE_API_KEY in environment.")
            sys.exit(1)
        config.PAPER_TRADING = False

    if args.scan:
        cmd_scan()
    elif args.status:
        cmd_status()
    elif args.report:
        cmd_report()
    else:
        trader = AITrader(paper_mode=config.PAPER_TRADING)
        trader.start()


if __name__ == "__main__":
    main()
