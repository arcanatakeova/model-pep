"""
strategy_auditor.py — Performance auditor and strategy repivot engine.

Runs every N cycles, analyses recent closed trades, then adjusts live config
parameters to maximise edge:
  • Signal threshold (MIN_SIGNAL_STRENGTH)
  • Per-market thresholds (SCALP_MIN_SCORE, FOREX_MIN_SCORE, DEX_MIN_SCORE)
  • Futures conviction gate (MIN_FUTURES_CONVICTION)
  • Stop / TP distances (STOP_LOSS_PCT, TAKE_PROFIT_PCT)
  • DEX position size (DEX_BASE_POSITION_USD)
  • Indicator weights (STRATEGY_WEIGHTS) based on detected regime

All changes are bounded so a single bad run cannot push the bot into an extreme
configuration. Changes are logged so the user can audit what happened.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

logger = logging.getLogger("ai_trader")

# ─── Tuning bounds (hard limits per parameter) ───────────────────────────────
_BOUNDS: dict[str, tuple[float, float]] = {
    "MIN_SIGNAL_STRENGTH":     (0.15, 0.50),
    "SCALP_MIN_SCORE":         (0.18, 0.55),
    "FOREX_MIN_SCORE":         (0.25, 0.60),
    "DEX_MIN_SCORE":           (0.20, 0.55),
    "MIN_FUTURES_CONVICTION":  (0.30, 0.70),
    "STOP_LOSS_PCT":           (0.015, 0.09),
    "TAKE_PROFIT_PCT":         (0.03,  0.18),
    "DEX_BASE_POSITION_USD":   (10.0, 250.0),
}

_INDICATOR_BOUNDS: dict[str, tuple[float, float]] = {
    "rsi":       (0.08, 0.35),
    "macd":      (0.08, 0.35),
    "bollinger": (0.05, 0.30),
    "ema_cross": (0.08, 0.38),
    "momentum":  (0.05, 0.28),
    "volume":    (0.05, 0.22),
}


class StrategyAuditor:
    """
    Audits recent trade performance and repivots live strategy parameters.

    Parameters are modified directly on the imported config module so every
    subsequent cycle picks them up without a restart.
    """

    LOOKBACK        = 50   # Recent closed trades to analyse
    MIN_TRADES      = 10   # Don't repivot until at least this many trades
    MIN_PER_MARKET  = 6    # Min trades per market for per-market adjustments

    def __init__(self, portfolio, cfg_module):
        self.portfolio    = portfolio
        self.cfg          = cfg_module
        self._audit_count = 0

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    def run_audit(self) -> dict:
        """
        Analyse recent trades and apply parameter adjustments.
        Returns a summary dict (logged by caller).
        """
        trades = list(self.portfolio.closed_trades)[-self.LOOKBACK:]
        if len(trades) < self.MIN_TRADES:
            logger.info("Auditor: only %d trades — skipping repivot (need %d)",
                        len(trades), self.MIN_TRADES)
            return {}

        self._audit_count += 1
        adjustments: list[str] = []

        market_stats = self._market_stats(trades)
        score_stats  = self._score_bucket_stats(trades)
        exit_stats   = self._exit_stats(trades)
        regime       = self._detect_regime(trades)

        logger.info("━━━ Audit #%d  (%d trades, regime=%s) ━━━",
                    self._audit_count, len(trades), regime)

        # Log market summary
        for mkt, s in market_stats.items():
            logger.info("  %-8s  n=%-3d  win=%.0f%%  avgPnL=%+.1f%%  total=$%+.0f",
                        mkt, s["n"], s["win_rate"] * 100,
                        s["avg_pnl_pct"], s["total_pnl"])

        # Apply repivots
        adjustments += self._repivot_signal_threshold(score_stats)
        adjustments += self._repivot_per_market(market_stats)
        adjustments += self._repivot_risk_reward(exit_stats)
        adjustments += self._repivot_indicator_weights(regime)

        if adjustments:
            logger.info("Audit adjustments applied:")
            for a in adjustments:
                logger.info("  → %s", a)
        else:
            logger.info("Audit: no adjustments needed")

        return {
            "audit_count": self._audit_count,
            "trades_analysed": len(trades),
            "regime": regime,
            "market_stats": market_stats,
            "adjustments": adjustments,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Statistics helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _market_stats(self, trades: list) -> dict:
        stats: dict[str, dict] = {}
        for market in ("crypto", "forex", "stocks", "dex", "futures"):
            mt = [t for t in trades if t.get("market") == market]
            if not mt:
                continue
            wins = [t for t in mt if t.get("pnl_usd", 0) > 0]
            stats[market] = {
                "n":           len(mt),
                "win_rate":    len(wins) / len(mt),
                "avg_pnl_pct": sum(t.get("pnl_pct", 0) for t in mt) / len(mt),
                "total_pnl":   sum(t.get("pnl_usd", 0) for t in mt),
            }
        return stats

    def _score_bucket_stats(self, trades: list) -> dict:
        """Group by |signal_score| bucket and measure quality."""
        buckets: dict[str, list] = {"low": [], "mid": [], "high": []}
        for t in trades:
            score = abs(t.get("signal_score", 0))
            if score < 0.35:
                buckets["low"].append(t)
            elif score < 0.58:
                buckets["mid"].append(t)
            else:
                buckets["high"].append(t)
        result = {}
        for name, ts in buckets.items():
            if ts:
                wins = [t for t in ts if t.get("pnl_usd", 0) > 0]
                result[name] = {
                    "n":           len(ts),
                    "win_rate":    len(wins) / len(ts),
                    "avg_pnl_pct": sum(t.get("pnl_pct", 0) for t in ts) / len(ts),
                }
        return result

    def _exit_stats(self, trades: list) -> dict:
        """Categorise exits: stop / trailing / take_profit / time / other."""
        total = len(trades)
        if total == 0:
            return {}
        reason_lower = [t.get("close_reason", "").lower() for t in trades]
        stops    = sum(1 for r in reason_lower if "stop" in r)
        tps      = sum(1 for r in reason_lower if "take profit" in r or "take_profit" in r)
        trailing = sum(1 for r in reason_lower if "trailing" in r)
        return {
            "total":        total,
            "stop_rate":    stops / total,
            "tp_rate":      tps / total,
            "trailing_rate": trailing / total,
        }

    def _detect_regime(self, trades: list) -> str:
        """Infer dominant regime from signal metadata stored on trades."""
        regimes = [t.get("regime", "") for t in trades if t.get("regime")]
        if not regimes:
            return "unknown"
        return Counter(regimes).most_common(1)[0][0]

    # ─────────────────────────────────────────────────────────────────────────
    # Repivot rules
    # ─────────────────────────────────────────────────────────────────────────

    def _repivot_signal_threshold(self, score_stats: dict) -> list[str]:
        changes = []
        low  = score_stats.get("low",  {})
        mid  = score_stats.get("mid",  {})
        high = score_stats.get("high", {})

        current = self.cfg.MIN_SIGNAL_STRENGTH

        # Low-score bucket losing → raise threshold to filter weak signals
        if low.get("n", 0) >= 5 and low.get("win_rate", 1.0) < 0.38:
            new = self._clamp("MIN_SIGNAL_STRENGTH", current + 0.03)
            if new != current:
                self.cfg.MIN_SIGNAL_STRENGTH = new
                changes.append(
                    f"MIN_SIGNAL_STRENGTH {current:.2f}→{new:.2f} "
                    f"(low-score win={low['win_rate']:.0%}, raising bar)")

        # Mid/high both strong but no low-score trades → can afford to lower
        elif (low.get("n", 0) == 0
              and mid.get("win_rate", 0) > 0.60
              and high.get("win_rate", 0) > 0.60
              and current > 0.22):
            new = self._clamp("MIN_SIGNAL_STRENGTH", current - 0.02)
            if new != current:
                self.cfg.MIN_SIGNAL_STRENGTH = new
                changes.append(
                    f"MIN_SIGNAL_STRENGTH {current:.2f}→{new:.2f} "
                    f"(all buckets strong, increasing signal flow)")

        return changes

    def _repivot_per_market(self, market_stats: dict) -> list[str]:
        changes = []

        # ── Scalping (scalp results land in 'futures' market) ────────────────
        fut = market_stats.get("futures", {})
        if fut.get("n", 0) >= self.MIN_PER_MARKET:
            old = self.cfg.SCALP_MIN_SCORE
            if fut["win_rate"] < 0.38:
                new = self._clamp("SCALP_MIN_SCORE", old + 0.04)
                if new != old:
                    self.cfg.SCALP_MIN_SCORE = new
                    changes.append(
                        f"SCALP_MIN_SCORE {old:.2f}→{new:.2f} "
                        f"(futures/scalp win={fut['win_rate']:.0%})")
            elif fut["win_rate"] > 0.62 and old > 0.24:
                new = self._clamp("SCALP_MIN_SCORE", old - 0.02)
                if new != old:
                    self.cfg.SCALP_MIN_SCORE = new
                    changes.append(
                        f"SCALP_MIN_SCORE {old:.2f}→{new:.2f} "
                        f"(futures/scalp strong {fut['win_rate']:.0%})")

        # ── Futures conviction gate ───────────────────────────────────────────
        if fut.get("n", 0) >= self.MIN_PER_MARKET:
            old = self.cfg.MIN_FUTURES_CONVICTION
            if fut["win_rate"] < 0.40:
                new = self._clamp("MIN_FUTURES_CONVICTION", old + 0.05)
                if new != old:
                    self.cfg.MIN_FUTURES_CONVICTION = new
                    changes.append(
                        f"MIN_FUTURES_CONVICTION {old:.2f}→{new:.2f} "
                        f"(futures losing, tightening gate)")
            elif fut["win_rate"] > 0.60:
                new = self._clamp("MIN_FUTURES_CONVICTION", old - 0.03)
                if new != old:
                    self.cfg.MIN_FUTURES_CONVICTION = new
                    changes.append(
                        f"MIN_FUTURES_CONVICTION {old:.2f}→{new:.2f} "
                        f"(futures profitable, loosening gate)")

        # ── Forex ─────────────────────────────────────────────────────────────
        forex = market_stats.get("forex", {})
        if forex.get("n", 0) >= self.MIN_PER_MARKET:
            old = self.cfg.FOREX_MIN_SCORE
            if forex["win_rate"] < 0.42:
                new = self._clamp("FOREX_MIN_SCORE", old + 0.03)
                if new != old:
                    self.cfg.FOREX_MIN_SCORE = new
                    changes.append(
                        f"FOREX_MIN_SCORE {old:.2f}→{new:.2f} "
                        f"(forex win={forex['win_rate']:.0%})")
            elif forex["win_rate"] > 0.62 and old > 0.28:
                new = self._clamp("FOREX_MIN_SCORE", old - 0.02)
                if new != old:
                    self.cfg.FOREX_MIN_SCORE = new
                    changes.append(
                        f"FOREX_MIN_SCORE {old:.2f}→{new:.2f} "
                        f"(forex strong {forex['win_rate']:.0%})")

        # ── DEX ───────────────────────────────────────────────────────────────
        dex = market_stats.get("dex", {})
        if dex.get("n", 0) >= self.MIN_PER_MARKET:
            old_size  = self.cfg.DEX_BASE_POSITION_USD
            old_score = self.cfg.DEX_MIN_SCORE
            if dex["win_rate"] < 0.35:
                new_size  = self._clamp("DEX_BASE_POSITION_USD", old_size * 0.75)
                new_score = self._clamp("DEX_MIN_SCORE", old_score + 0.03)
                if new_size != old_size:
                    self.cfg.DEX_BASE_POSITION_USD = new_size
                    changes.append(
                        f"DEX_BASE_POSITION_USD ${old_size:.0f}→${new_size:.0f} "
                        f"(DEX win={dex['win_rate']:.0%})")
                if new_score != old_score:
                    self.cfg.DEX_MIN_SCORE = new_score
                    changes.append(
                        f"DEX_MIN_SCORE {old_score:.2f}→{new_score:.2f} "
                        f"(raising bar on DEX entries)")
            elif dex["win_rate"] > 0.55 and dex["avg_pnl_pct"] > 2.0:
                new_size = self._clamp("DEX_BASE_POSITION_USD", old_size * 1.20)
                if new_size != old_size:
                    self.cfg.DEX_BASE_POSITION_USD = new_size
                    changes.append(
                        f"DEX_BASE_POSITION_USD ${old_size:.0f}→${new_size:.0f} "
                        f"(DEX profitable {dex['win_rate']:.0%})")

        return changes

    def _repivot_risk_reward(self, exit_stats: dict) -> list[str]:
        changes = []
        if exit_stats.get("total", 0) < 15:
            return changes

        stop_rate = exit_stats["stop_rate"] + exit_stats["trailing_rate"]
        tp_rate   = exit_stats["tp_rate"]

        old_stop = self.cfg.STOP_LOSS_PCT
        old_tp   = self.cfg.TAKE_PROFIT_PCT

        if stop_rate > 0.55:
            # Stops too tight — widen by 10 %
            new_stop = self._clamp("STOP_LOSS_PCT", old_stop * 1.10)
            if new_stop != old_stop:
                self.cfg.STOP_LOSS_PCT = new_stop
                changes.append(
                    f"STOP_LOSS_PCT {old_stop:.3f}→{new_stop:.3f} "
                    f"(stops hit {stop_rate:.0%} of trades)")

        elif tp_rate > 0.60:
            # TPs hit often — let winners run further
            new_tp = self._clamp("TAKE_PROFIT_PCT", old_tp * 1.10)
            if new_tp != old_tp:
                self.cfg.TAKE_PROFIT_PCT = new_tp
                changes.append(
                    f"TAKE_PROFIT_PCT {old_tp:.3f}→{new_tp:.3f} "
                    f"(TPs hit {tp_rate:.0%}, extending targets)")

        return changes

    def _repivot_indicator_weights(self, regime: str) -> list[str]:
        if regime not in ("trending", "ranging", "volatile"):
            return []

        weights = dict(self.cfg.STRATEGY_WEIGHTS)
        original = dict(weights)

        if regime == "trending":
            weights["ema_cross"] = weights.get("ema_cross", 0.20) + 0.05
            weights["momentum"]  = weights.get("momentum",  0.15) + 0.03
            weights["rsi"]       = weights.get("rsi",       0.20) - 0.04
            weights["bollinger"] = weights.get("bollinger", 0.15) - 0.04
        elif regime == "ranging":
            weights["rsi"]       = weights.get("rsi",       0.20) + 0.05
            weights["bollinger"] = weights.get("bollinger", 0.15) + 0.05
            weights["ema_cross"] = weights.get("ema_cross", 0.20) - 0.05
            weights["momentum"]  = weights.get("momentum",  0.15) - 0.03
        elif regime == "volatile":
            weights["volume"]    = weights.get("volume",    0.10) + 0.05
            weights["macd"]      = weights.get("macd",      0.20) + 0.03
            weights["momentum"]  = weights.get("momentum",  0.15) - 0.04
            weights["rsi"]       = weights.get("rsi",       0.20) - 0.04

        # Clamp each indicator
        weights = {
            k: max(_INDICATOR_BOUNDS.get(k, (0.05, 0.40))[0],
                   min(_INDICATOR_BOUNDS.get(k, (0.05, 0.40))[1], v))
            for k, v in weights.items()
        }

        # Normalise to sum = 1.0
        total = sum(weights.values())
        weights = {k: round(v / total, 4) for k, v in weights.items()}

        if weights != original:
            self.cfg.STRATEGY_WEIGHTS = weights
            return [f"STRATEGY_WEIGHTS adjusted for '{regime}' regime "
                    f"(ema={weights.get('ema_cross', 0):.2f} "
                    f"rsi={weights.get('rsi', 0):.2f} "
                    f"boll={weights.get('bollinger', 0):.2f} "
                    f"mom={weights.get('momentum', 0):.2f})"]
        return []

    # ─────────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────────

    def _clamp(self, param: str, value: float) -> float:
        lo, hi = _BOUNDS.get(param, (value, value))
        return round(max(lo, min(hi, value)), 4)
