"""
Compounding Engine — Autonomous Wealth Reinvestment
====================================================
The core of the money-making loop.

Every cycle:
1. Audit all profits from closed trades
2. Calculate optimal reinvestment allocation
3. Increase position sizes as equity grows
4. Track compound growth curve
5. Rebalance between market types based on performance

Philosophy:
- NEVER withdraw profits — reinvest everything
- Let position sizes grow proportionally with equity
- Rotate capital into highest-performing market types
- Scale aggression as the system proves itself
"""
from __future__ import annotations
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class CompoundingEngine:
    """
    Manages autonomous profit compounding and capital allocation.
    Works alongside the Portfolio to continuously grow the base.
    """

    ALLOCATION_FILE = "allocation_state.json"

    def __init__(self, portfolio, risk_manager):
        self.portfolio    = portfolio
        self.risk_manager = risk_manager

        # Market type allocations (fraction of total equity)
        self.allocations = {
            "crypto_cex":  0.35,   # CoinGecko/CryptoCompare signals (CEX)
            "crypto_dex":  0.25,   # DEX Screener / on-chain tokens
            "polymarket":  0.15,   # Prediction markets
            "stocks":      0.15,   # Stocks / ETFs
            "forex":       0.10,   # Forex pairs
        }

        # Performance tracking per market type
        self.market_stats: dict[str, dict] = {
            k: {"trades": 0, "wins": 0, "total_pnl": 0.0, "win_rate": 0.5}
            for k in self.allocations
        }

        # Compounding milestones
        self.milestones = [
            (1_000,    "Micro"),
            (5_000,    "Small"),
            (10_000,   "Standard"),
            (25_000,   "Growing"),
            (50_000,   "Accelerating"),
            (100_000,  "Compounding"),
            (250_000,  "Scaling"),
            (500_000,  "Institutional"),
            (1_000_000, "Million"),
        ]

        self._equity_at_last_rebalance = 0.0
        self._cycle_count = 0

        self.load_state()

    # ─── Core Loop ────────────────────────────────────────────────────────────

    def on_cycle_complete(self) -> dict:
        """
        Called after every trading cycle.
        Returns updated allocation recommendations.
        """
        self._cycle_count += 1
        equity = self.portfolio.equity()

        # Update market stats from recent closed trades
        self._update_market_stats()

        # Rebalance every 10 cycles or when equity changes >5%
        if (self._cycle_count % 10 == 0 or
                abs(equity - self._equity_at_last_rebalance) / max(self._equity_at_last_rebalance, 1) > 0.05):
            self._rebalance_allocations()
            self._equity_at_last_rebalance = equity

        # Check milestones
        self._check_milestones(equity)

        # Save state
        if self._cycle_count % 5 == 0:
            self.save_state()

        return self.get_allocation_usd()

    def get_allocation_usd(self) -> dict[str, float]:
        """
        Returns dollar allocation per market type based on current equity.
        These are the MAXIMUM amounts to deploy per market.
        """
        equity = self.portfolio.equity()
        return {
            market: round(equity * frac, 2)
            for market, frac in self.allocations.items()
        }

    def get_position_scale_factor(self) -> float:
        """
        Returns a multiplier for position sizes based on equity growth.
        Scales up as the portfolio grows, maintaining percentage risk.
        As capital grows, we can take advantage of better liquidity.
        """
        equity  = self.portfolio.equity()
        initial = self.portfolio.initial_capital

        if initial <= 0:
            return 1.0

        growth = equity / initial
        # Aggressive compounding: scale factor grows with equity
        # $10k → 1.0x, $20k → 1.2x, $50k → 1.5x, $100k → 2.0x
        if growth < 1.0:
            return 0.75   # Protect capital during drawdown
        elif growth < 2.0:
            return 1.0
        elif growth < 5.0:
            return 1.0 + (growth - 2.0) * 0.15
        elif growth < 10.0:
            return 1.45 + (growth - 5.0) * 0.10
        else:
            return min(2.5, 1.45 + (growth - 5.0) * 0.08)

    def max_position_for_market(self, market_type: str) -> float:
        """
        Maximum USD position size for a given market type.
        Accounts for allocation and per-trade risk.
        """
        alloc = self.get_allocation_usd()
        market_budget = alloc.get(market_type, 0)
        scale = self.get_position_scale_factor()

        # Never more than 25% of market budget in a single position
        max_pos = market_budget * 0.25 * scale

        # Also cap at absolute position limit from risk manager config
        import config
        abs_max = self.portfolio.equity() * config.MAX_POSITION_PCT * scale
        return min(max_pos, abs_max)

    def daily_profit_target(self) -> float:
        """
        Target daily profit in USD.
        Starts conservative (0.3%/day = ~110%/year), scales up.
        At 0.3%/day: $10k → $10k profit in 1 year (compounded).
        At 0.5%/day: $10k → $60k profit in 1 year (compounded).
        """
        equity = self.portfolio.equity()
        initial = self.portfolio.initial_capital
        growth = equity / initial if initial > 0 else 1.0

        base_rate = 0.003  # 0.3%/day base
        if growth > 2:
            base_rate = 0.004
        if growth > 5:
            base_rate = 0.005

        return equity * base_rate

    # ─── Performance Tracking ─────────────────────────────────────────────────

    def _update_market_stats(self):
        """Sync market stats from portfolio's closed trades."""
        # Reset stats
        for k in self.market_stats:
            self.market_stats[k] = {"trades": 0, "wins": 0, "total_pnl": 0.0, "win_rate": 0.5}

        for trade in self.portfolio.closed_trades:
            market = trade.get("market", "crypto_cex")
            # Map market types to allocation keys
            key = self._market_to_key(market)
            stats = self.market_stats[key]
            stats["trades"] += 1
            stats["total_pnl"] += trade.get("pnl_usd", 0)
            if trade.get("pnl_usd", 0) > 0:
                stats["wins"] += 1

        for k, stats in self.market_stats.items():
            n = stats["trades"]
            stats["win_rate"] = stats["wins"] / n if n > 0 else 0.5

    def _market_to_key(self, market: str) -> str:
        mapping = {
            "crypto": "crypto_cex",
            "dex": "crypto_dex",
            "solana": "crypto_dex",
            "polymarket": "polymarket",
            "stocks": "stocks",
            "forex": "forex",
        }
        return mapping.get(market.lower(), "crypto_cex")

    def _rebalance_allocations(self):
        """
        Dynamically rebalance market allocations based on performance.
        Reward markets that are making money, reduce losing markets.
        Uses a softmax-style reweighting.
        """
        import numpy as np

        # Performance scores per market
        scores = {}
        for key, stats in self.market_stats.items():
            n = stats["trades"]
            if n < 3:
                scores[key] = 0.5   # Not enough data, neutral
            else:
                # Combine win rate and total PnL
                wr_score = stats["win_rate"]
                pnl_score = np.clip(stats["total_pnl"] / (self.portfolio.equity() + 1) * 10 + 0.5, 0.1, 1.0)
                scores[key] = 0.6 * wr_score + 0.4 * pnl_score

        # Softmax reweighting
        score_array = np.array(list(scores.values()))
        score_array = np.nan_to_num(score_array, nan=0.5, posinf=0.5, neginf=0.5)
        exp_scores  = np.exp(score_array * 2)  # Temperature = 0.5
        total_exp   = exp_scores.sum()
        if total_exp == 0 or np.isnan(total_exp):
            return  # Bad data — skip rebalance this cycle
        weights     = exp_scores / total_exp

        # Enforce min/max allocation bounds
        min_alloc = 0.05
        max_alloc = 0.45
        weights = np.clip(weights, min_alloc, max_alloc)
        weights = weights / weights.sum()  # Renormalize

        old_alloc = dict(self.allocations)
        for key, w in zip(scores.keys(), weights):
            # Smooth update: blend 30% new, 70% old
            self.allocations[key] = 0.7 * self.allocations[key] + 0.3 * float(w)

        # Renormalize
        total = sum(self.allocations.values())
        self.allocations = {k: v / total for k, v in self.allocations.items()}

        # Log changes
        for k in self.allocations:
            if abs(self.allocations[k] - old_alloc.get(k, 0)) > 0.02:
                logger.info("Rebalance %s: %.1f%% → %.1f%%",
                            k, old_alloc.get(k, 0) * 100, self.allocations[k] * 100)

    def _check_milestones(self, equity: float):
        """Log milestone achievements."""
        for threshold, name in self.milestones:
            if equity >= threshold:
                prev_eq = self._equity_at_last_rebalance
                if prev_eq < threshold <= equity:
                    pct = (equity - self.portfolio.initial_capital) / self.portfolio.initial_capital * 100
                    logger.info("🎯 MILESTONE: %s Portfolio ($%.0f) | +%.1f%% total return",
                                name, equity, pct)

    # ─── Compound Growth Calculator ───────────────────────────────────────────

    def projected_growth(self, daily_rate: float = 0.003, days: int = 365) -> list[dict]:
        """
        Project compound growth over N days.
        daily_rate: 0.003 = 0.3%/day
        """
        equity = self.portfolio.equity()
        projections = []
        for day in range(0, days + 1, 30):
            projected = equity * (1 + daily_rate) ** day
            projections.append({
                "day": day,
                "equity": round(projected, 2),
                "return_pct": round((projected / equity - 1) * 100, 1),
            })
        return projections

    def growth_summary(self) -> dict:
        """Current growth metrics."""
        equity  = self.portfolio.equity()
        initial = self.portfolio.initial_capital
        perf    = self.portfolio.performance_summary()

        return {
            "current_equity": round(equity, 2),
            "initial_capital": initial,
            "total_return_pct": round((equity - initial) / initial * 100, 2),
            "scale_factor": round(self.get_position_scale_factor(), 2),
            "allocations": {k: f"{v*100:.1f}%" for k, v in self.allocations.items()},
            "allocation_usd": self.get_allocation_usd(),
            "daily_profit_target_usd": round(self.daily_profit_target(), 2),
            "market_performance": {
                k: {
                    "trades": v["trades"],
                    "win_rate": f"{v['win_rate']*100:.0f}%",
                    "pnl_usd": round(v["total_pnl"], 2),
                }
                for k, v in self.market_stats.items()
            },
            "projections": {
                "30d_conservative":  round(equity * (1.003 ** 30), 2),
                "90d_conservative":  round(equity * (1.003 ** 90), 2),
                "365d_conservative": round(equity * (1.003 ** 365), 2),
                "30d_target":        round(equity * (1.005 ** 30), 2),
                "365d_target":       round(equity * (1.005 ** 365), 2),
            },
        }

    # ─── Persistence ──────────────────────────────────────────────────────────

    def save_state(self):
        state = {
            "allocations": self.allocations,
            "market_stats": self.market_stats,
            "cycle_count": self._cycle_count,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self.ALLOCATION_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save compounding state: %s", e)

    def load_state(self):
        try:
            with open(self.ALLOCATION_FILE) as f:
                state = json.load(f)
            self.allocations   = state.get("allocations", self.allocations)
            self.market_stats  = state.get("market_stats", self.market_stats)
            self._cycle_count  = state.get("cycle_count", 0)
            logger.info("Compounding state loaded (cycle #%d)", self._cycle_count)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Could not load compounding state: %s", e)
