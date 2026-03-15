"""
Backtest Harness — Validate Strategy Edge on Historical Data
=============================================================
Downloads 1 year of hourly OHLCV from CryptoCompare (free),
replays every signal through a simulated portfolio, and produces
a full professional-grade performance report.

Usage:
    python backtest.py                          # BTC, ETH, SOL — 365 days
    python backtest.py --symbol BTC --days 180
    python backtest.py --all                    # All watchlist symbols
    python backtest.py --plot                   # Show equity curve chart
    python backtest.py --strategy scalp         # Test scalp signals only
    python backtest.py --compare                # Baseline vs full ensemble

Output:
    backtest_results.json — Full trade log + metrics
    Console table — Win rate, Sharpe, Sortino, Profit Factor, Max DD, CAGR
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import requests

# ── Add trader directory to path ──────────────────────────────────────────────
TRADER_DIR = Path(__file__).parent
sys.path.insert(0, str(TRADER_DIR))

import config
from strategies.ensemble import EnsembleSignal, TradeSignal

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ─── Data Download ────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, days: int) -> pd.DataFrame:
    """
    Download hourly OHLCV from CryptoCompare.
    Max 2000 candles per call → need multiple calls for >83 days.
    """
    all_rows = []
    limit     = 2000
    remaining = days * 24
    to_ts     = None

    while remaining > 0:
        fetch_limit = min(limit, remaining)
        params = {"fsym": symbol, "tsym": "USD", "limit": fetch_limit}
        if to_ts:
            params["toTs"] = to_ts

        url = "https://min-api.cryptocompare.com/data/v2/histohour"
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ⚠ Failed to fetch {symbol} data: {e}")
            break

        if data.get("Response") != "Success":
            break

        rows = data["Data"]["Data"]
        if not rows:
            break

        all_rows = rows + all_rows
        remaining -= fetch_limit
        to_ts = rows[0]["time"] - 1   # Next batch ends where this one started
        time.sleep(0.3)               # Polite rate limit

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"volumefrom": "volume"})
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df[df["close"] > 0].drop_duplicates("timestamp").sort_values("timestamp")
    return df.reset_index(drop=True)


# ─── Backtest Engine ──────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    qty: float
    entry_bar: int
    exit_bar: int
    pnl_usd: float
    pnl_pct: float
    close_reason: str
    signal_score: float
    conviction: float
    regime: str


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    initial_capital: float
    final_equity: float
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_bars_held: float = 0.0
    cagr_pct: float = 0.0
    calmar_ratio: float = 0.0
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)

    @property
    def total_return_pct(self) -> float:
        return (self.final_equity - self.initial_capital) / self.initial_capital * 100


class Backtester:
    """
    Walks forward through historical OHLCV bar by bar.
    At each bar, runs the ensemble signal on the trailing window,
    then executes trades against the simulated portfolio.
    """

    def __init__(self, initial_capital: float = 10_000,
                 risk_per_trade: float = 0.02,
                 max_positions: int = 3,
                 slippage: float = 0.001,
                 commission: float = 0.001,
                 strategy: str = "ensemble"):
        self.capital       = initial_capital
        self.risk_pct      = risk_per_trade
        self.max_positions = max_positions
        self.slippage      = slippage
        self.commission    = commission
        self.strategy      = strategy
        self.signal_engine = EnsembleSignal()
        self._warmup       = 60    # Bars needed before first signal

    def run(self, df: pd.DataFrame, symbol: str, asset_id: str,
            days_tested: int) -> BacktestResult:
        """Walk-forward backtest over the full OHLCV dataframe."""
        cash           = self.capital
        open_trade: Optional[BacktestTrade] = None
        trades: list[BacktestTrade] = []
        equity_series: list[float] = []

        for i in range(self._warmup, len(df)):
            bar     = df.iloc[i]
            window  = df.iloc[max(0, i - 200): i + 1]
            price   = float(bar["close"])
            bar_dt  = bar["timestamp"]

            # Mark-to-market equity
            pos_value = 0.0
            if open_trade:
                if open_trade.side == "long":
                    pos_value = open_trade.qty * price
                else:
                    pos_value = open_trade.qty * (open_trade.entry_price - price + open_trade.entry_price)
            equity = cash + pos_value
            equity_series.append(equity)

            # ── Check exit conditions for open trade ─────────────────────────
            if open_trade:
                close_reason = None
                if open_trade.side == "long":
                    pnl_pct = (price - open_trade.entry_price) / open_trade.entry_price
                    if price >= open_trade.entry_price * (1 + config.TAKE_PROFIT_PCT):
                        close_reason = "Take profit"
                    elif price <= open_trade.entry_price * (1 - config.STOP_LOSS_PCT):
                        close_reason = "Stop loss"
                else:
                    pnl_pct = (open_trade.entry_price - price) / open_trade.entry_price
                    if price <= open_trade.entry_price * (1 - config.TAKE_PROFIT_PCT):
                        close_reason = "Take profit"
                    elif price >= open_trade.entry_price * (1 + config.STOP_LOSS_PCT):
                        close_reason = "Stop loss"

                if close_reason:
                    fill   = price * (1 - self.slippage if open_trade.side == "long" else 1 + self.slippage)
                    if open_trade.side == "long":
                        proceeds = open_trade.qty * fill
                        pnl_usd  = (fill - open_trade.entry_price) * open_trade.qty
                    else:
                        pnl_usd  = (open_trade.entry_price - fill) * open_trade.qty
                        proceeds = open_trade.qty * open_trade.entry_price + pnl_usd
                    cash += proceeds
                    pnl_pct = pnl_usd / (open_trade.entry_price * open_trade.qty) * 100

                    open_trade.exit_price  = fill
                    open_trade.exit_bar    = i
                    open_trade.pnl_usd     = round(pnl_usd, 4)
                    open_trade.pnl_pct     = round(pnl_pct, 2)
                    open_trade.close_reason = close_reason
                    trades.append(open_trade)
                    open_trade = None
                    continue

            # ── Generate signal on this bar ───────────────────────────────────
            if open_trade:
                continue   # Already in position — wait for exit

            sig = self.signal_engine.analyze(
                window.copy(), asset_id=asset_id, market="crypto", symbol=symbol)
            if not sig or sig.signal == "HOLD":
                continue

            # ── Open new position ─────────────────────────────────────────────
            fill = float(bar["close"]) * (1 + self.slippage if sig.signal == "BUY" else 1 - self.slippage)
            side = "long" if sig.signal == "BUY" else "short"

            stop_pct = abs(fill - sig.stop_loss) / fill if fill > 0 else config.STOP_LOSS_PCT
            if stop_pct <= 0:
                continue

            risk_usd = cash * self.risk_pct
            size_usd = min(risk_usd / stop_pct, cash * config.MAX_POSITION_PCT, cash * 0.95)
            if size_usd < 1:
                continue

            commission_usd = size_usd * self.commission
            if side == "long":
                cost = size_usd + commission_usd
                if cost > cash:
                    continue
                cash -= cost
                qty = size_usd / fill
            else:
                qty = size_usd / fill
                cash -= commission_usd

            open_trade = BacktestTrade(
                symbol=symbol, side=side,
                entry_price=fill, exit_price=0,
                qty=qty, entry_bar=i, exit_bar=0,
                pnl_usd=0, pnl_pct=0,
                close_reason="",
                signal_score=sig.score,
                conviction=sig.conviction,
                regime=sig.regime,
            )

        # ── Force-close any open trade at end ────────────────────────────────
        if open_trade:
            last_price = float(df.iloc[-1]["close"])
            if open_trade.side == "long":
                proceeds = open_trade.qty * last_price
                pnl_usd  = (last_price - open_trade.entry_price) * open_trade.qty
                cash += proceeds
            else:
                pnl_usd = (open_trade.entry_price - last_price) * open_trade.qty
                cash += open_trade.qty * open_trade.entry_price + pnl_usd
            pnl_pct = pnl_usd / (open_trade.entry_price * open_trade.qty) * 100
            open_trade.exit_price   = last_price
            open_trade.exit_bar     = len(df) - 1
            open_trade.pnl_usd      = round(pnl_usd, 4)
            open_trade.pnl_pct      = round(pnl_pct, 2)
            open_trade.close_reason = "End of backtest"
            trades.append(open_trade)

        final_equity = cash
        return self._compute_metrics(
            trades, equity_series, symbol, final_equity, days_tested)

    # ─────────────────────────────────────────────────────────────────────────
    # Metrics
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_metrics(self, trades: list[BacktestTrade],
                          equity_curve: list[float],
                          symbol: str, final_equity: float,
                          days: int) -> BacktestResult:
        result = BacktestResult(
            symbol=symbol, strategy=self.strategy,
            initial_capital=self.capital, final_equity=final_equity,
            equity_curve=equity_curve,
        )
        if not trades:
            return result

        wins   = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]

        result.total_trades   = len(trades)
        result.winning_trades = len(wins)
        result.losing_trades  = len(losses)
        result.total_pnl_usd  = round(sum(t.pnl_usd for t in trades), 2)
        result.win_rate_pct   = round(len(wins) / len(trades) * 100, 1) if trades else 0
        result.avg_win_pct    = round(np.mean([t.pnl_pct for t in wins]), 2) if wins else 0
        result.avg_loss_pct   = round(np.mean([t.pnl_pct for t in losses]), 2) if losses else 0
        result.avg_bars_held  = round(np.mean([t.exit_bar - t.entry_bar for t in trades]), 1)

        gross_win  = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))
        result.profit_factor  = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999.0

        # Drawdown
        peak = self.capital
        max_dd = 0.0
        for eq in equity_curve:
            peak  = max(peak, eq)
            dd    = (peak - eq) / peak
            max_dd = max(max_dd, dd)
        result.max_drawdown_pct = round(max_dd * 100, 2)

        # Daily returns from hourly equity curve
        if len(equity_curve) >= 24:
            eq_arr  = np.array(equity_curve)
            daily   = eq_arr[23::24]   # Sample every 24 bars
            d_rets  = np.diff(daily) / daily[:-1]
            result.daily_returns = d_rets.tolist()

            if len(d_rets) > 1:
                rf_daily = 0.00013   # ~5% annual risk-free
                excess   = d_rets - rf_daily
                result.sharpe_ratio  = round(float(np.mean(excess) / np.std(excess) * np.sqrt(252)), 2) if np.std(excess) > 0 else 0
                down     = d_rets[d_rets < 0]
                result.sortino_ratio = round(float(np.mean(d_rets) / np.std(down) * np.sqrt(252)), 2) if len(down) > 0 and np.std(down) > 0 else 0

        # CAGR
        if days > 0 and final_equity > 0:
            cagr = (final_equity / self.capital) ** (365 / days) - 1
            result.cagr_pct = round(cagr * 100, 1)

        # Calmar = CAGR / Max DD
        if result.max_drawdown_pct > 0:
            result.calmar_ratio = round(result.cagr_pct / result.max_drawdown_pct, 2)

        result.trades = trades
        return result


# ─── Report Formatting ────────────────────────────────────────────────────────

def print_report(result: BacktestResult, days: int):
    r = result
    divider = "═" * 68

    print(f"\n{divider}")
    print(f"  BACKTEST REPORT — {r.symbol} — {days} days — Strategy: {r.strategy.upper()}")
    print(divider)
    print(f"  Initial Capital:    ${r.initial_capital:>14,.2f}")
    print(f"  Final Equity:       ${r.final_equity:>14,.2f}  ({r.total_return_pct:+.2f}%)")
    print(f"  CAGR:               {r.cagr_pct:>14.1f}%  (annualised)")
    print(f"  Total P&L:          ${r.total_pnl_usd:>+14,.2f}")
    print(f"{divider}")
    print(f"  Total Trades:       {r.total_trades:>14d}")
    print(f"  Win Rate:           {r.win_rate_pct:>13.1f}%  ({r.winning_trades}W / {r.losing_trades}L)")
    print(f"  Avg Win:            {r.avg_win_pct:>+13.2f}%")
    print(f"  Avg Loss:           {r.avg_loss_pct:>+13.2f}%")
    print(f"  Profit Factor:      {r.profit_factor:>14.2f}  (> 1.5 is good, > 2.0 is excellent)")
    print(f"  Avg Bars Held:      {r.avg_bars_held:>14.1f}  hours")
    print(f"{divider}")
    print(f"  Sharpe Ratio:       {r.sharpe_ratio:>14.2f}  (> 1.0 is good, > 2.0 is excellent)")
    print(f"  Sortino Ratio:      {r.sortino_ratio:>14.2f}")
    print(f"  Max Drawdown:       {r.max_drawdown_pct:>13.2f}%")
    print(f"  Calmar Ratio:       {r.calmar_ratio:>14.2f}  (CAGR / Max DD)")

    # Quality rating
    print(f"\n  {'─'*46}")
    rating = _quality_rating(r)
    print(f"  Strategy Rating:    {rating}")
    print(f"{'═'*68}\n")

def _quality_rating(r: BacktestResult) -> str:
    score = 0
    if r.win_rate_pct >= 55:     score += 2
    elif r.win_rate_pct >= 50:   score += 1
    if r.profit_factor >= 2.0:   score += 2
    elif r.profit_factor >= 1.5: score += 1
    if r.sharpe_ratio >= 2.0:    score += 2
    elif r.sharpe_ratio >= 1.0:  score += 1
    if r.max_drawdown_pct <= 10: score += 2
    elif r.max_drawdown_pct <= 20: score += 1
    if r.cagr_pct >= 50:         score += 2
    elif r.cagr_pct >= 25:       score += 1

    grades = {
        range(9, 12): "🏆 EXCELLENT — deploy with confidence",
        range(6, 9):  "✅ GOOD — solid edge, deploy carefully",
        range(3, 6):  "⚠️  MARGINAL — needs improvement before live",
        range(0, 3):  "❌ POOR — do not deploy with real capital",
    }
    for r_range, label in grades.items():
        if score in r_range:
            return f"[{score}/10] {label}"
    return f"[{score}/10]"

def plot_equity_curve(results: list[BacktestResult]):
    """Plot equity curves using matplotlib (optional)."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.style as mplstyle
        mplstyle.use("dark_background")

        fig, axes = plt.subplots(len(results), 1, figsize=(14, 4 * len(results)))
        if len(results) == 1:
            axes = [axes]

        for ax, r in zip(axes, results):
            x = range(len(r.equity_curve))
            ax.plot(x, r.equity_curve, linewidth=1.5,
                    color="#34d058" if r.final_equity >= r.initial_capital else "#ef4444")
            ax.axhline(r.initial_capital, color="#555", linestyle="--", linewidth=0.8)
            ax.set_title(f"{r.symbol} — {r.strategy} | Return: {r.total_return_pct:+.1f}% | Sharpe: {r.sharpe_ratio:.2f}",
                         color="#c9d1d9", fontsize=12)
            ax.set_facecolor("#0d1117")
            ax.tick_params(colors="#7b8cb5")
            for spine in ax.spines.values():
                spine.set_edgecolor("#1e2740")
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

        fig.patch.set_facecolor("#0d1117")
        plt.tight_layout()
        plt.savefig("backtest_equity.png", dpi=150, bbox_inches="tight")
        print("📈 Equity curve saved to backtest_equity.png")
        plt.show()
    except ImportError:
        print("(Install matplotlib for equity curve plot: pip install matplotlib)")


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI Trader — Historical Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--symbol",  default="BTC",  help="Symbol to test (BTC/ETH/SOL)")
    parser.add_argument("--days",    type=int, default=365, help="Days of history to test")
    parser.add_argument("--capital", type=float, default=10_000, help="Starting capital ($)")
    parser.add_argument("--risk",    type=float, default=0.02, help="Risk per trade (fraction)")
    parser.add_argument("--all",     action="store_true", help="Test all watchlist symbols")
    parser.add_argument("--plot",    action="store_true", help="Plot equity curve")
    parser.add_argument("--save",    action="store_true", help="Save results to backtest_results.json")
    parser.add_argument("--strategy",default="ensemble", choices=["ensemble", "scalp"],
                        help="Strategy to test")
    parser.add_argument("--compare", action="store_true",
                        help="Run baseline RSI-only vs full ensemble comparison")
    args = parser.parse_args()

    symbols = (["BTC", "ETH", "SOL"] + config.SCALP_SYMBOLS) if args.all else [args.symbol]
    symbols = list(dict.fromkeys(symbols))  # Deduplicate, preserve order

    all_results = []

    for symbol in symbols:
        print(f"\n{'─'*68}")
        print(f"  Downloading {symbol} — {args.days} days of hourly data...")
        df = fetch_ohlcv(symbol, args.days)
        if df.empty:
            print(f"  ⚠ No data returned for {symbol} — skipping.")
            continue
        print(f"  ✓ {len(df)} hourly bars ({args.days}d) | "
              f"price range: ${df['close'].min():,.2f} – ${df['close'].max():,.2f}")

        if args.compare:
            # Run full ensemble
            bt_full = Backtester(args.capital, args.risk, strategy="ensemble")
            r_full  = bt_full.run(df.copy(), symbol, symbol.lower(), args.days)
            print_report(r_full, args.days)
            all_results.append(r_full)

            # Baseline: single RSI strategy (simpler, for comparison)
            # We'll just swap weights to RSI-only
            import copy
            simple_config = copy.deepcopy(config.STRATEGY_WEIGHTS)
            config.STRATEGY_WEIGHTS = {"rsi": 1.0, "macd": 0, "bollinger": 0,
                                        "ema_cross": 0, "momentum": 0, "volume": 0}
            bt_base = Backtester(args.capital, args.risk, strategy="rsi_only")
            r_base  = bt_base.run(df.copy(), symbol, symbol.lower(), args.days)
            config.STRATEGY_WEIGHTS = simple_config
            print_report(r_base, args.days)
            all_results.append(r_base)

            print(f"\n  Ensemble vs RSI-only: Return {r_full.total_return_pct:+.1f}% vs "
                  f"{r_base.total_return_pct:+.1f}% | Sharpe {r_full.sharpe_ratio:.2f} vs {r_base.sharpe_ratio:.2f}")
        else:
            bt = Backtester(args.capital, args.risk, strategy=args.strategy)
            r  = bt.run(df, symbol, symbol.lower(), args.days)
            print_report(r, args.days)
            all_results.append(r)

    if args.plot and all_results:
        plot_equity_curve(all_results)

    if args.save and all_results:
        out = []
        for r in all_results:
            d = {k: v for k, v in asdict(r).items() if k not in ("trades", "equity_curve", "daily_returns")}
            d["trades"] = [asdict(t) for t in r.trades]
            out.append(d)
        with open("backtest_results.json", "w") as f:
            json.dump(out, f, indent=2)
        print(f"\n✓ Results saved to backtest_results.json ({len(all_results)} strategy/symbol combos)")


if __name__ == "__main__":
    main()
