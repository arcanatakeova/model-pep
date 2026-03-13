"""
AI Trader — Autonomous Multi-Market Compounding Bot
====================================================
Runs every 60 seconds, 24/7, trading all available markets.

Markets traded:
  1. Crypto CEX      — CoinGecko / CryptoCompare signals (top 50 coins)
  2. DEX Tokens      — DEX Screener hot tokens (Solana, Base, BSC, etc.)
  3. Solana On-Chain — Phantom wallet + Jupiter DEX swaps
  4. Polymarket      — Prediction market edge trading
  5. Stocks / ETFs   — Yahoo Finance (QQQ, SPY, NVDA, etc.)
  6. Forex           — Major currency pairs

Usage:
  python main.py                  # Start live paper-trading bot (default)
  python main.py --live           # Enable real trades (needs keys in .env)
  python main.py --scan           # One-shot scan, print all signals, exit
  python main.py --status         # Portfolio status + compound growth, exit
  python main.py --report         # Full JSON report, exit
  python main.py --growth         # Show projected compound growth table
"""
import argparse
import json
import logging
import os
import signal
import sys
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
from strategies import MarketScanner
from dex_screener import DexScreener
from polymarket import PolymarketTrader
from solana_wallet import SolanaWallet
import data_fetcher as df_mod

# ─── Logging ──────────────────────────────────────────────────────────────────
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

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║          AUTONOMOUS AI TRADER  v2.0  —  ALL MARKETS              ║
╠══════════════════════════════════════════════════════════════════╣
║  Crypto CEX │ DEX Tokens │ Solana │ Polymarket │ Stocks │ Forex  ║
║  Mode: {mode:<8}    Capital: ${capital:<12,.0f}                  ║
║  Scan interval: every 60 seconds, 24/7                           ║
╚══════════════════════════════════════════════════════════════════╝
"""


# ─── Main Trader ──────────────────────────────────────────────────────────────

class AITrader:
    """
    Fully autonomous trading loop.
    Deposits money into Phantom → bot runs forever compounding.
    """

    def __init__(self, live: bool = False):
        self.live    = live
        self.running = False
        config.PAPER_TRADING = not live

        # ── Core ──────────────────────────────────────────────────────────
        self.portfolio  = Portfolio(config.INITIAL_CAPITAL)
        self.risk_mgr   = RiskManager(self.portfolio)
        self.executor   = TradeExecutor(self.portfolio, self.risk_mgr)
        self.compounder = CompoundingEngine(self.portfolio, self.risk_mgr)

        # ── Market Scanners ───────────────────────────────────────────────
        self.cex_scanner  = MarketScanner()                          # crypto/forex/stocks
        self.dex_screener = DexScreener()                            # on-chain tokens
        self.poly_trader  = PolymarketTrader(
            private_key=config.POLYMARKET_PRIVATE_KEY)               # prediction markets
        self.solana       = SolanaWallet(
            private_key_b58=config.PHANTOM_PRIVATE_KEY)              # Phantom wallet

        # ── State ─────────────────────────────────────────────────────────
        self._cycle        = 0
        self._last_save    = time.time()
        self._last_poly    = 0.0
        self._last_dex     = 0.0
        self._equity_curve = []
        self._dex_positions: dict = {}  # addr → {buy_price, qty, chain, symbol}

        # Load saved state
        self.portfolio.load()
        self.risk_mgr.reset_daily_loss_tracker()

        # Sync initial capital with Phantom wallet if connected
        if self.solana.is_connected and live:
            wallet_value = self.solana.get_portfolio_value_usd()
            if wallet_value > 10:
                self.portfolio.cash = wallet_value
                self.portfolio.initial_capital = wallet_value
                logger.info("Phantom wallet synced: $%.2f", wallet_value)

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
        self._cycle += 1
        t0 = time.time()
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        equity = self.portfolio.equity()
        logger.info("━━━ Cycle #%d  %s  Equity: $%.2f ━━━",
                    self._cycle, now, equity)

        # ── 1. Update all open positions (stops, trailing, PnL) ───────────
        self.executor.update_all_positions()
        self._update_dex_positions()

        # ── 2. Market overview ────────────────────────────────────────────
        self._log_market_snapshot()

        # ── 3. CEX Scanner: Crypto + Stocks + Forex ───────────────────────
        self._run_cex_scan()

        # ── 4. DEX Screener: On-chain token sniping ───────────────────────
        now_ts = time.time()
        if now_ts - self._last_dex >= config.DEX_SCAN_INTERVAL_SEC:
            self._run_dex_scan()
            self._last_dex = now_ts

        # ── 5. Polymarket: Prediction market edges ────────────────────────
        if now_ts - self._last_poly >= config.POLYMARKET_SCAN_INTERVAL_SEC:
            self._run_polymarket_scan()
            self._last_poly = now_ts

        # ── 6. Compound: Reinvest + rebalance ─────────────────────────────
        alloc = self.compounder.on_cycle_complete()
        logger.info("Compound allocations: " + " | ".join(
            f"{k}: ${v:.0f}" for k, v in alloc.items()))

        # ── 7. Equity snapshot ────────────────────────────────────────────
        self._equity_curve.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "equity": round(equity, 2),
            "cycle": self._cycle,
        })

        # ── 8. Risk report ────────────────────────────────────────────────
        risk = self.risk_mgr.risk_report()
        logger.info("Risk: pos=%d/%d cash=$%.0f dd=%.1f%% daily=%.1f%%",
                    risk["open_positions"], risk["max_positions"],
                    risk["cash"], risk["current_drawdown_pct"],
                    risk["daily_loss_pct"])

        # ── 9. Periodic saves ─────────────────────────────────────────────
        if time.time() - self._last_save > config.PORTFOLIO_SNAPSHOT_INTERVAL:
            self.portfolio.save()
            self._save_equity_curve()
            self._last_save = time.time()

        elapsed = time.time() - t0
        logger.info("Cycle #%d done in %.1fs", self._cycle, elapsed)

    # ─────────────────────────────────────────────────────────────────────────
    # Market Subsystems
    # ─────────────────────────────────────────────────────────────────────────

    def _run_cex_scan(self):
        """CEX scan: crypto (CoinGecko/CC), stocks (yfinance), forex."""
        try:
            signals = self.cex_scanner.scan_all(max_workers=10)
            actionable = [s for s in signals if s.signal != "HOLD"]
            logger.info("CEX scan: %d signals (%d actionable)",
                        len(signals), len(actionable))
            for sig in actionable[:8]:   # Top 8 by score
                # Scale position size by compound engine
                self._execute_cex_signal(sig)
        except Exception as e:
            logger.warning("CEX scan error: %s", e)

    def _execute_cex_signal(self, signal):
        """Execute a CEX trade with compound-scaled position sizing."""
        market_key = {"crypto": "crypto_cex", "stocks": "stocks",
                      "forex": "forex"}.get(signal.market, "crypto_cex")
        max_pos = self.compounder.max_position_for_market(market_key)
        scale   = self.compounder.get_position_scale_factor()

        # Temporarily override risk manager's position size via scale
        original_risk = config.RISK_PER_TRADE_PCT
        config.RISK_PER_TRADE_PCT = min(
            original_risk * scale,
            0.04,   # Never more than 4% per trade
        )
        try:
            self.executor.process_signal(signal)
        finally:
            config.RISK_PER_TRADE_PCT = original_risk

    def _run_dex_scan(self):
        """DEX Screener scan for on-chain momentum tokens."""
        try:
            tokens = self.dex_screener.get_multi_chain_opportunities()
            logger.info("DEX scan: %d opportunities found", len(tokens))

            budget = self.compounder.max_position_for_market("crypto_dex")
            traded = 0

            for token in tokens[:5]:   # Top 5 DEX opportunities
                if token.score < config.DEX_MIN_SCORE:
                    continue
                if token.pair_address in self._dex_positions:
                    continue
                if traded >= 2:        # Max 2 new DEX positions per cycle
                    break

                size_usd = min(
                    budget * 0.20,                 # 20% of DEX budget per token
                    config.DEX_MAX_POSITION_USD,   # Absolute cap
                    self.portfolio.cash * 0.05,    # Max 5% of cash
                )
                if size_usd < 10:
                    continue

                self._open_dex_position(token, size_usd)
                traded += 1

        except Exception as e:
            logger.warning("DEX scan error: %s", e)

    def _open_dex_position(self, token, size_usd: float):
        """Open a position on a DEX token via Phantom/Jupiter or paper."""
        try:
            if token.chain_id == "solana" and self.solana.is_connected:
                # Real swap via Phantom → Jupiter
                tx = self.solana.buy_token(token.base_address, size_usd,
                                           slippage_bps=config.SOL_MAX_SLIPPAGE_BPS)
                if tx:
                    self._dex_positions[token.pair_address] = {
                        "symbol": token.base_symbol,
                        "address": token.base_address,
                        "chain": token.chain_id,
                        "entry_price": token.price_usd,
                        "size_usd": size_usd,
                        "tx": tx,
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "stop_pct": 0.15,    # 15% stop on volatile DEX tokens
                        "target_pct": 0.40,  # 40% take-profit target
                        "score": token.score,
                        "signals": token.signals,
                    }
                    self.portfolio.cash -= size_usd
                    logger.info("DEX BUY %s/%s $%.2f @ $%.8f score=%.2f | %s",
                                token.chain_id.upper(), token.base_symbol,
                                size_usd, token.price_usd, token.score,
                                ", ".join(token.signals[:2]))
            else:
                # Paper mode or non-Solana chain
                self._dex_positions[token.pair_address] = {
                    "symbol": token.base_symbol,
                    "address": token.base_address,
                    "chain": token.chain_id,
                    "entry_price": token.price_usd,
                    "size_usd": size_usd,
                    "tx": f"paper_{int(time.time())}",
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "stop_pct": 0.15,
                    "target_pct": 0.40,
                    "score": token.score,
                    "signals": token.signals,
                }
                self.portfolio.cash -= size_usd
                logger.info("PAPER DEX BUY %s/%s $%.2f @ $%.8f | %s",
                            token.chain_id.upper(), token.base_symbol,
                            size_usd, token.price_usd, ", ".join(token.signals[:2]))

        except Exception as e:
            logger.warning("DEX position open failed (%s): %s", token.base_symbol, e)

    def _update_dex_positions(self):
        """Check open DEX positions for exit conditions."""
        closed = []
        for pair_addr, pos in self._dex_positions.items():
            try:
                token = self.dex_screener.get_token_info(pos["address"], pos["chain"])
                if not token or token.price_usd <= 0:
                    continue

                entry   = pos["entry_price"]
                current = token.price_usd
                pnl_pct = (current - entry) / entry if entry > 0 else 0

                # Update trailing high
                pos["peak_price"] = max(pos.get("peak_price", entry), current)
                trail_pct = (pos["peak_price"] - current) / pos["peak_price"] if pos["peak_price"] > 0 else 0

                reason = None
                if pnl_pct >= pos["target_pct"]:
                    reason = f"Take profit +{pnl_pct:.0%}"
                elif pnl_pct <= -pos["stop_pct"]:
                    reason = f"Stop loss {pnl_pct:.0%}"
                elif trail_pct > 0.12 and pnl_pct > 0.05:
                    reason = f"Trailing stop (peak={pos['peak_price']:.8f})"

                if reason:
                    self._close_dex_position(pair_addr, pos, current, reason)
                    closed.append(pair_addr)

            except Exception as e:
                logger.debug("DEX position update error: %s", e)

        for addr in closed:
            self._dex_positions.pop(addr, None)

    def _close_dex_position(self, pair_addr: str, pos: dict, current_price: float, reason: str):
        """Close a DEX position."""
        entry   = pos["entry_price"]
        size    = pos["size_usd"]
        pnl_pct = (current_price - entry) / entry if entry > 0 else 0
        pnl_usd = size * pnl_pct
        proceeds = size + pnl_usd

        if pos["chain"] == "solana" and self.solana.is_connected and "paper" not in pos.get("tx", ""):
            self.solana.sell_token(pos["address"], proceeds)
        else:
            self.portfolio.cash += proceeds

        sign = "+" if pnl_usd >= 0 else ""
        logger.info("DEX CLOSE %s/%s %s%.2f%% ($%s%.2f) | %s",
                    pos["chain"].upper(), pos["symbol"],
                    sign, pnl_pct * 100, sign, pnl_usd, reason)

        # Record in portfolio for stats
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
            "opened_at": pos["opened_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })

    def _run_polymarket_scan(self):
        """Scan Polymarket for prediction market edge plays."""
        try:
            signals = self.poly_trader.find_edges(
                min_edge=config.POLYMARKET_MIN_EDGE,
                min_volume=config.POLYMARKET_MIN_VOLUME,
            )
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
        """Log a brief market overview."""
        try:
            snap = df_mod.get_market_snapshot()
            if snap:
                logger.info("Market: %-8s | BTC Dom: %.1f%% | Avg24h: %+.1f%%",
                            snap.get("market_sentiment", "?").upper(),
                            snap.get("btc_dominance", 0),
                            snap.get("avg_24h_change", 0))
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
        self._save_equity_curve()
        self._print_report()
        logger.info("Trader stopped cleanly.")

    def _save_equity_curve(self):
        try:
            with open("equity_curve.json", "w") as f:
                json.dump(self._equity_curve[-10_000:], f, indent=2)
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
        print(f"\n  Open positions ({len(positions)}):")
        for p in positions:
            print(f"    {p['symbol']:<12} {p['side']:<5} "
                  f"entry=${p['entry_price']:.4f} now=${p['current_price']:.4f} "
                  f"pnl={p['unrealized_pnl_pct']:>+.1f}%")
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
    parser.add_argument("--live",    action="store_true", help="Enable real trades (needs API keys in .env)")
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
        if args.live:
            if not any([config.BINANCE_API_KEY, config.PHANTOM_PRIVATE_KEY,
                        config.POLYMARKET_PRIVATE_KEY, config.COINBASE_API_KEY]):
                print("WARNING: --live mode but no API keys found in .env")
                print("Running in paper mode. Set keys to enable real trades.")
        trader = AITrader(live=args.live)
        trader.start()


if __name__ == "__main__":
    main()
