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
    "DEX_MIN_SCORE":           (0.25, 0.52),   # tighter: never go below 0.25 or above 0.52
    "MIN_FUTURES_CONVICTION":  (0.30, 0.70),
    "STOP_LOSS_PCT":           (0.015, 0.09),
    "TAKE_PROFIT_PCT":         (0.03,  0.18),
    "DEX_BASE_POSITION_USD":   (10.0, 200.0),
}

# ─── DEX-specific signal labels that the auditor tracks ──────────────────────
_DEX_SIGNAL_KEYS = {
    "burst":    "BURST MODE",
    "momentum": "5m",          # any "5m" mention = 5m momentum signal
    "volume":   "Vol",         # volume surge/explosion signals
    "new":      "FRESH",       # very fresh pairs (<15min)
    "viral":    "Viral",       # viral growth signal
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

    LOOKBACK        = 75   # Recent closed trades to analyse (raised: more DEX data = better signal)
    MIN_TRADES      = 10   # Don't repivot until at least this many trades
    MIN_PER_MARKET  = 6    # Min trades per market for per-market adjustments
    DEX_MIN_FOR_DEEP = 15  # Minimum DEX trades for signal-level analysis

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

        # DEX-specific deep analysis
        dex_trades = [t for t in trades if t.get("market") == "dex"]
        if len(dex_trades) >= self.DEX_MIN_FOR_DEEP:
            adjustments += self._repivot_dex_signals(dex_trades)
            adjustments += self._repivot_dex_hold_time(dex_trades)

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
    # DEX-specific deep analysis
    # ─────────────────────────────────────────────────────────────────────────

    def _repivot_dex_signals(self, dex_trades: list) -> list[str]:
        """
        Analyse win rate per entry signal type (BURST, 5m momentum, volume, fresh, viral).
        Adjusts DEX_MIN_SCORE up/down based on which signals correlate with wins.
        Also logs a signal scorecard so the operator can see what's working.
        """
        changes: list[str] = []

        # Build per-signal win stats
        signal_stats: dict[str, dict] = {}
        for label, keyword in _DEX_SIGNAL_KEYS.items():
            hits = [t for t in dex_trades
                    if any(keyword in s for s in t.get("signals", []))]
            if not hits:
                continue
            wins = [t for t in hits if t.get("pnl_usd", 0) > 0]
            avg_gain = sum(t.get("max_gain_pct", 0) for t in hits) / len(hits)
            signal_stats[label] = {
                "n": len(hits),
                "win_rate": len(wins) / len(hits),
                "avg_max_gain_pct": avg_gain,
                "avg_pnl_pct": sum(t.get("pnl_pct", 0) for t in hits) / len(hits),
            }

        if signal_stats:
            logger.info("  DEX signal scorecard (last %d trades):", len(dex_trades))
            for label, s in sorted(signal_stats.items(),
                                   key=lambda x: x[1]["avg_pnl_pct"], reverse=True):
                logger.info("    %-10s  n=%-3d  win=%.0f%%  avgPnL=%+.1f%%  peakGain=+%.1f%%",
                            label, s["n"], s["win_rate"] * 100,
                            s["avg_pnl_pct"], s["avg_max_gain_pct"])

        # Analyse by DEX source (pumpfun_new, birdeye_gainer, etc.)
        source_stats: dict[str, dict] = {}
        for t in dex_trades:
            src = t.get("dex_source", "unknown") or "unknown"
            if src not in source_stats:
                source_stats[src] = {"n": 0, "wins": 0, "pnl": 0.0}
            source_stats[src]["n"] += 1
            source_stats[src]["wins"] += 1 if t.get("pnl_usd", 0) > 0 else 0
            source_stats[src]["pnl"] += t.get("pnl_usd", 0)

        if source_stats:
            logger.info("  DEX source breakdown:")
            for src, s in sorted(source_stats.items(),
                                  key=lambda x: x[1]["pnl"], reverse=True):
                wr = s["wins"] / s["n"] if s["n"] > 0 else 0
                logger.info("    %-20s  n=%-3d  win=%.0f%%  pnl=$%+.2f",
                            src, s["n"], wr * 100, s["pnl"])

        # Score-range breakdown for DEX — gives finer granularity than global buckets
        score_ranges = [(0.33, 0.38), (0.38, 0.43), (0.43, 0.50), (0.50, 1.0)]
        logger.info("  DEX score-range breakdown:")
        for lo, hi in score_ranges:
            bucket = [t for t in dex_trades
                      if lo <= t.get("signal_score", 0) < hi]
            if not bucket:
                continue
            wins = [t for t in bucket if t.get("pnl_usd", 0) > 0]
            avg_pnl = sum(t.get("pnl_pct", 0) for t in bucket) / len(bucket)
            logger.info("    score %.2f–%.2f  n=%-3d  win=%.0f%%  avgPnL=%+.1f%%",
                        lo, hi, len(bucket),
                        len(wins) / len(bucket) * 100, avg_pnl)

        # Adaptive threshold: if the lowest score bracket is underwater, raise the bar
        lowest_bracket = [t for t in dex_trades
                          if t.get("signal_score", 0) < 0.38]
        if len(lowest_bracket) >= 5:
            wr = sum(1 for t in lowest_bracket if t.get("pnl_usd", 0) > 0) / len(lowest_bracket)
            old = self.cfg.DEX_MIN_SCORE
            if wr < 0.30:
                new = self._clamp("DEX_MIN_SCORE", old + 0.02)
                if new != old:
                    self.cfg.DEX_MIN_SCORE = new
                    changes.append(
                        f"DEX_MIN_SCORE {old:.2f}→{new:.2f} "
                        f"(low-score bracket win={wr:.0%} — raising entry bar)")
            elif wr > 0.55 and old > 0.33:
                new = self._clamp("DEX_MIN_SCORE", old - 0.01)
                if new != old:
                    self.cfg.DEX_MIN_SCORE = new
                    changes.append(
                        f"DEX_MIN_SCORE {old:.2f}→{new:.2f} "
                        f"(low-score bracket profitable {wr:.0%} — slight relaxation)")

        return changes

    def _repivot_dex_hold_time(self, dex_trades: list) -> list[str]:
        """
        Analyse hold time vs. outcome. If trades held >2h tend to lose more than
        trades exited <30min, tighten the stale-exit timer.
        Also check whether early partial-profit tiers are being taken and paying off.
        """
        changes: list[str] = []

        # Split by hold time
        short_holds  = [t for t in dex_trades if t.get("hold_seconds", 999999) < 1800]   # <30min
        medium_holds = [t for t in dex_trades if 1800 <= t.get("hold_seconds", 0) < 7200] # 30min–2h
        long_holds   = [t for t in dex_trades if t.get("hold_seconds", 0) >= 7200]        # >2h

        def _stats(group: list) -> tuple[float, float]:
            if not group:
                return 0.0, 0.0
            wr = sum(1 for t in group if t.get("pnl_usd", 0) > 0) / len(group)
            avg_pnl = sum(t.get("pnl_pct", 0) for t in group) / len(group)
            return wr, avg_pnl

        short_wr,  short_avg  = _stats(short_holds)
        medium_wr, medium_avg = _stats(medium_holds)
        long_wr,   long_avg   = _stats(long_holds)

        logger.info("  DEX hold-time analysis  (short<30m n=%d win=%.0f%% avg=%+.1f%%) "
                    "(mid n=%d win=%.0f%% avg=%+.1f%%) "
                    "(long>2h n=%d win=%.0f%% avg=%+.1f%%)",
                    len(short_holds), short_wr * 100, short_avg,
                    len(medium_holds), medium_wr * 100, medium_avg,
                    len(long_holds), long_wr * 100, long_avg)

        # If long holds are significantly worse than short holds → tighten stale exit
        if (len(long_holds) >= 4 and len(short_holds) >= 4
                and long_avg < short_avg - 10 and long_wr < 0.35):
            old = self.cfg.DEX_STALE_EXIT_HOURS
            new = round(max(1.0, old - 0.5), 1)
            if new != old:
                self.cfg.DEX_STALE_EXIT_HOURS = new
                changes.append(
                    f"DEX_STALE_EXIT_HOURS {old}→{new} "
                    f"(long holds losing avg={long_avg:+.1f}%, short avg={short_avg:+.1f}%)")

        # Partial profit analysis — were the tiers helping?
        with_partials    = [t for t in dex_trades if t.get("partials_taken")]
        without_partials = [t for t in dex_trades if not t.get("partials_taken")]
        if len(with_partials) >= 4 and len(without_partials) >= 4:
            wp_avg  = sum(t.get("pnl_pct", 0) for t in with_partials) / len(with_partials)
            wop_avg = sum(t.get("pnl_pct", 0) for t in without_partials) / len(without_partials)
            logger.info("  DEX partial-profit impact: with_partials n=%d avg=%+.1f%% | "
                        "no_partials n=%d avg=%+.1f%%",
                        len(with_partials), wp_avg, len(without_partials), wop_avg)

        # Burst vs non-burst comparison
        burst_trades = [t for t in dex_trades if t.get("is_burst")]
        plain_trades = [t for t in dex_trades if not t.get("is_burst")]
        if burst_trades:
            b_wr  = sum(1 for t in burst_trades if t.get("pnl_usd", 0) > 0) / len(burst_trades)
            b_avg = sum(t.get("pnl_pct", 0) for t in burst_trades) / len(burst_trades)
            p_wr  = sum(1 for t in plain_trades if t.get("pnl_usd", 0) > 0) / len(plain_trades) if plain_trades else 0
            p_avg = sum(t.get("pnl_pct", 0) for t in plain_trades) / len(plain_trades) if plain_trades else 0
            logger.info("  DEX BURST trades: n=%d win=%.0f%% avg=%+.1f%%  |  "
                        "plain: n=%d win=%.0f%% avg=%+.1f%%",
                        len(burst_trades), b_wr * 100, b_avg,
                        len(plain_trades), p_wr * 100, p_avg)

        return changes

    # ─────────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────────

    def _clamp(self, param: str, value: float) -> float:
        lo, hi = _BOUNDS.get(param, (value, value))
        return round(max(lo, min(hi, value)), 4)
