"""
strategy_auditor.py — Continuous trade review and strategy repivot engine.

Two modes run on every DEX close:

1. post_trade_review(trade)  — Instant post-mortem after every single close.
   Compares the just-closed trade against ALL historical trades for context:
   how did exit timing, PnL, and score compare to similar past trades?

2. run_audit()               — Full parameter repivot every 10 DEX closes.
   Uses three time windows: last-10 (recent), last-50 (medium), all-time.
   Adjusts DEX_MIN_SCORE, DEX_BASE_POSITION_USD, stale-exit timer, etc.
   All changes are bounded so no single bad run creates extreme config.

Both methods use the full closed_trades history — not a truncated window —
so pattern recognition improves continuously as more trades accumulate.
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
    "DEX_MIN_SCORE":           (0.25, 0.52),
    "MIN_FUTURES_CONVICTION":  (0.30, 0.70),
    "STOP_LOSS_PCT":           (0.015, 0.09),
    "TAKE_PROFIT_PCT":         (0.03,  0.18),
    "DEX_BASE_POSITION_USD":   (10.0, 200.0),
    "DEX_STALE_EXIT_HOURS":    (1.0,  4.0),
}

# ─── Signal keywords tracked for per-signal win-rate analysis ────────────────
_DEX_SIGNAL_KEYS = {
    "burst":    "BURST MODE",
    "momentum": "5m",
    "volume":   "Vol",
    "fresh":    "FRESH",
    "viral":    "Viral",
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
    Runs after every DEX trade close. post_trade_review() gives instant
    feedback on the just-closed trade against all history. run_audit()
    adjusts live config every 10 closes.
    """

    REPIVOT_EVERY   = 10   # Full parameter repivot every N DEX closes
    MIN_TRADES      = 10   # Skip repivot below this total trade count
    MIN_PER_MARKET  = 6    # Min trades in a market for per-market repivot
    DEX_MIN_DEEP    = 15   # Min DEX trades for signal/hold-time analysis

    def __init__(self, portfolio, cfg_module):
        self.portfolio    = portfolio
        self.cfg          = cfg_module
        self._audit_count = 0
        self._close_count = 0   # counts every DEX close (drives repivot cadence)

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────────────────────────────────

    def on_trade_closed(self, trade: dict) -> None:
        """
        Call after every DEX position close. Runs an instant post-mortem on
        the just-closed trade, then fires a full repivot every REPIVOT_EVERY closes.
        """
        self._close_count += 1
        all_trades = list(self.portfolio.closed_trades)  # full history

        # Always: per-trade post-mortem with historical context
        self._post_trade_review(trade, all_trades)

        # Periodically: full repivot using all available data
        if self._close_count % self.REPIVOT_EVERY == 0:
            self.run_audit(all_trades)

    def run_audit(self, all_trades: Optional[list] = None) -> dict:
        """
        Full parameter repivot. Uses three windows:
          recent  — last 10 trades  (current conditions)
          medium  — last 50 trades  (trend)
          alltime — everything       (base rate)
        """
        if all_trades is None:
            all_trades = list(self.portfolio.closed_trades)

        if len(all_trades) < self.MIN_TRADES:
            logger.info("Auditor: only %d trades total — skipping repivot (need %d)",
                        len(all_trades), self.MIN_TRADES)
            return {}

        self._audit_count += 1
        recent  = all_trades[-10:]
        medium  = all_trades[-50:]
        alltime = all_trades

        adjustments: list[str] = []

        # ── Performance trend snapshot ────────────────────────────────────────
        def _wr(ts): return sum(1 for t in ts if t.get("pnl_usd", 0) > 0) / len(ts) if ts else 0
        def _avg(ts): return sum(t.get("pnl_pct", 0) for t in ts) / len(ts) if ts else 0

        logger.info(
            "━━━ Audit #%d  total=%d  recent-10 win=%.0f%% avg=%+.1f%%  "
            "last-50 win=%.0f%% avg=%+.1f%%  all-time win=%.0f%% avg=%+.1f%% ━━━",
            self._audit_count, len(alltime),
            _wr(recent) * 100, _avg(recent),
            _wr(medium) * 100, _avg(medium),
            _wr(alltime) * 100, _avg(alltime))

        # ── Trend direction (is the bot improving or degrading?) ──────────────
        if len(alltime) >= 20:
            old_half_wr = _wr(alltime[: len(alltime) // 2])
            new_half_wr = _wr(alltime[len(alltime) // 2 :])
            trend = "improving" if new_half_wr > old_half_wr + 0.05 else \
                    "degrading"  if new_half_wr < old_half_wr - 0.05 else "stable"
            logger.info("  Performance trend: %s  (first-half win=%.0f%%  second-half win=%.0f%%)",
                        trend, old_half_wr * 100, new_half_wr * 100)

        # ── Per-market stats (use medium window for repivot decisions) ─────────
        market_stats = self._market_stats(medium)
        for mkt, s in market_stats.items():
            logger.info("  %-8s  n=%-3d  win=%.0f%%  avgPnL=%+.1f%%  total=$%+.0f",
                        mkt, s["n"], s["win_rate"] * 100, s["avg_pnl_pct"], s["total_pnl"])

        # ── Repivots (use medium window for stability) ────────────────────────
        score_stats = self._score_bucket_stats(medium)
        exit_stats  = self._exit_stats(medium)
        regime      = self._detect_regime(medium)

        adjustments += self._repivot_signal_threshold(score_stats)
        adjustments += self._repivot_per_market(market_stats)
        adjustments += self._repivot_risk_reward(exit_stats)
        adjustments += self._repivot_indicator_weights(regime)

        # ── DEX deep analysis (uses ALL history for maximum sample size) ──────
        dex_all    = [t for t in alltime if t.get("market") == "dex"]
        dex_recent = [t for t in recent  if t.get("market") == "dex"]
        if len(dex_all) >= self.DEX_MIN_DEEP:
            adjustments += self._repivot_dex_signals(dex_all, dex_recent)
            adjustments += self._repivot_dex_hold_time(dex_all)

        if adjustments:
            logger.info("Audit adjustments:")
            for a in adjustments:
                logger.info("  → %s", a)
        else:
            logger.info("Audit: no adjustments needed")

        return {
            "audit_count":   self._audit_count,
            "total_trades":  len(alltime),
            "regime":        regime,
            "adjustments":   adjustments,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Per-trade instant post-mortem
    # ─────────────────────────────────────────────────────────────────────────

    def _post_trade_review(self, trade: dict, all_trades: list) -> None:
        """
        Instant analysis of one trade against full history.
        Answers: Was this a typical outcome? Did we exit well? What can we learn?
        """
        symbol      = trade.get("symbol", "?")
        pnl_pct     = trade.get("pnl_pct", 0)
        max_gain    = trade.get("max_gain_pct", 0)
        hold_secs   = trade.get("hold_seconds", 0)
        score       = trade.get("signal_score", 0)
        source      = trade.get("dex_source", "?")
        close_why   = trade.get("close_reason", "?")
        is_win      = trade.get("pnl_usd", 0) > 0
        is_burst    = trade.get("is_burst", False)

        # All DEX trades for peer comparison
        peers = [t for t in all_trades if t.get("market") == "dex"
                 and t.get("asset_id") != trade.get("asset_id")]  # exclude self

        if not peers:
            logger.info("TRADE REVIEW %s | PnL %+.1f%% | first trade — no history yet",
                        symbol, pnl_pct)
            return

        # ── Running win rates ─────────────────────────────────────────────────
        total_n   = len(peers) + 1  # include this trade
        wins_n    = sum(1 for t in peers if t.get("pnl_usd", 0) > 0) + (1 if is_win else 0)
        recent10  = peers[-9:] + [trade]   # last 10 including this
        recent5   = peers[-4:] + [trade]   # last 5 including this
        wr_all    = wins_n / total_n
        wr_10     = sum(1 for t in recent10 if t.get("pnl_usd", 0) > 0) / len(recent10)
        wr_5      = sum(1 for t in recent5  if t.get("pnl_usd", 0) > 0) / len(recent5)

        # ── Peer-group comparison (same source) ───────────────────────────────
        src_peers = [t for t in peers if t.get("dex_source") == source]
        src_avg   = (sum(t.get("pnl_pct", 0) for t in src_peers) / len(src_peers)
                     if src_peers else None)
        src_wr    = (sum(1 for t in src_peers if t.get("pnl_usd", 0) > 0) / len(src_peers)
                     if src_peers else None)

        # ── Score-bracket peers ───────────────────────────────────────────────
        score_lo   = (score // 0.05) * 0.05          # floor to nearest 0.05
        score_hi   = score_lo + 0.05
        score_peers = [t for t in peers
                       if score_lo <= t.get("signal_score", 0) < score_hi]
        score_avg  = (sum(t.get("pnl_pct", 0) for t in score_peers) / len(score_peers)
                      if score_peers else None)

        # ── Exit quality: did we capture the available gain? ──────────────────
        capture_pct = (pnl_pct / max_gain * 100) if max_gain > 1 else None
        left_on_table = max_gain - pnl_pct if max_gain > pnl_pct + 1 else 0

        # ── Build verdict ─────────────────────────────────────────────────────
        flags: list[str] = []

        if is_win:
            if capture_pct is not None and capture_pct < 40:
                flags.append(f"exited too early (captured {capture_pct:.0f}% of {max_gain:.1f}% peak)")
            if capture_pct is not None and capture_pct > 85:
                flags.append(f"near-perfect exit (captured {capture_pct:.0f}% of peak)")
            if left_on_table > 30:
                flags.append(f"${left_on_table:.1f}% left on table vs peak")
        else:
            # Loss — was the stop appropriate?
            stop_set  = trade.get("stop_pct", 0) * 100
            if stop_set > 0 and abs(pnl_pct) > stop_set * 1.5:
                flags.append(f"stop overrun (set {stop_set:.0f}%, lost {abs(pnl_pct):.1f}%)")
            if score_avg is not None and score_avg > 0 and pnl_pct < score_avg - 10:
                flags.append(f"underperformed score-bracket avg ({score_avg:+.1f}%)")

        if hold_secs < 120 and not is_win:
            flags.append("very fast loss (<2min) — likely bad entry timing")
        if wr_5 < 0.30:
            flags.append("HOT STREAK WARNING: only 1/5 recent trades profitable")
        if wr_5 > 0.80:
            flags.append("strong run: 4+/5 recent profitable")
        if is_burst:
            flags.append("BURST MODE entry")

        # ── Log the review ────────────────────────────────────────────────────
        outcome = "WIN " if is_win else "LOSS"
        hold_m  = hold_secs / 60

        logger.info(
            "TRADE REVIEW [%s] %s %+.1f%% | peak=%+.1f%% hold=%.1fm score=%.2f src=%s | "
            "exit: %s",
            outcome, symbol, pnl_pct, max_gain, hold_m, score, source, close_why)

        logger.info(
            "  Win rates → all-time: %.0f%% (%d trades)  last-10: %.0f%%  last-5: %.0f%%",
            wr_all * 100, total_n, wr_10 * 100, wr_5 * 100)

        if src_avg is not None:
            logger.info(
                "  vs %s peers (n=%d): win=%.0f%% avg=%+.1f%%  |  this trade: %+.1f%%  → %s",
                source, len(src_peers), src_wr * 100, src_avg,
                pnl_pct, "above avg" if pnl_pct > src_avg else "below avg")

        if score_avg is not None:
            logger.info(
                "  vs score %.2f–%.2f peers (n=%d): avg=%+.1f%%  → this: %+.1f%%",
                score_lo, score_hi, len(score_peers), score_avg, pnl_pct)

        for f in flags:
            logger.info("  ⚑ %s", f)

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
        buckets: dict[str, list] = {"low": [], "mid": [], "high": []}
        for t in trades:
            score = abs(t.get("signal_score", 0))
            if score < 0.38:
                buckets["low"].append(t)
            elif score < 0.46:
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
        total = len(trades)
        if total == 0:
            return {}
        rl = [t.get("close_reason", "").lower() for t in trades]
        return {
            "total":         total,
            "stop_rate":     sum(1 for r in rl if "stop" in r and "trailing" not in r) / total,
            "tp_rate":       sum(1 for r in rl if "take profit" in r or "take_profit" in r) / total,
            "trailing_rate": sum(1 for r in rl if "trailing" in r) / total,
            "time_rate":     sum(1 for r in rl if "stale" in r or "time" in r) / total,
            "reversal_rate": sum(1 for r in rl if "reversal" in r or "collapse" in r) / total,
        }

    def _detect_regime(self, trades: list) -> str:
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

        if low.get("n", 0) >= 5 and low.get("win_rate", 1.0) < 0.38:
            new = self._clamp("MIN_SIGNAL_STRENGTH", current + 0.03)
            if new != current:
                self.cfg.MIN_SIGNAL_STRENGTH = new
                changes.append(f"MIN_SIGNAL_STRENGTH {current:.2f}→{new:.2f} "
                                f"(low-score win={low['win_rate']:.0%})")
        elif (low.get("n", 0) == 0
              and mid.get("win_rate", 0) > 0.60
              and high.get("win_rate", 0) > 0.60
              and current > 0.22):
            new = self._clamp("MIN_SIGNAL_STRENGTH", current - 0.02)
            if new != current:
                self.cfg.MIN_SIGNAL_STRENGTH = new
                changes.append(f"MIN_SIGNAL_STRENGTH {current:.2f}→{new:.2f} "
                                f"(all buckets strong)")
        return changes

    def _repivot_per_market(self, market_stats: dict) -> list[str]:
        changes = []

        fut = market_stats.get("futures", {})
        if fut.get("n", 0) >= self.MIN_PER_MARKET:
            old = self.cfg.SCALP_MIN_SCORE
            if fut["win_rate"] < 0.38:
                new = self._clamp("SCALP_MIN_SCORE", old + 0.04)
                if new != old:
                    self.cfg.SCALP_MIN_SCORE = new
                    changes.append(f"SCALP_MIN_SCORE {old:.2f}→{new:.2f} "
                                   f"(win={fut['win_rate']:.0%})")
            elif fut["win_rate"] > 0.62 and old > 0.24:
                new = self._clamp("SCALP_MIN_SCORE", old - 0.02)
                if new != old:
                    self.cfg.SCALP_MIN_SCORE = new
                    changes.append(f"SCALP_MIN_SCORE {old:.2f}→{new:.2f} "
                                   f"(strong {fut['win_rate']:.0%})")

            old = self.cfg.MIN_FUTURES_CONVICTION
            if fut["win_rate"] < 0.40:
                new = self._clamp("MIN_FUTURES_CONVICTION", old + 0.05)
                if new != old:
                    self.cfg.MIN_FUTURES_CONVICTION = new
                    changes.append(f"MIN_FUTURES_CONVICTION {old:.2f}→{new:.2f}")
            elif fut["win_rate"] > 0.60:
                new = self._clamp("MIN_FUTURES_CONVICTION", old - 0.03)
                if new != old:
                    self.cfg.MIN_FUTURES_CONVICTION = new
                    changes.append(f"MIN_FUTURES_CONVICTION {old:.2f}→{new:.2f}")

        forex = market_stats.get("forex", {})
        if forex.get("n", 0) >= self.MIN_PER_MARKET:
            old = self.cfg.FOREX_MIN_SCORE
            if forex["win_rate"] < 0.42:
                new = self._clamp("FOREX_MIN_SCORE", old + 0.03)
                if new != old:
                    self.cfg.FOREX_MIN_SCORE = new
                    changes.append(f"FOREX_MIN_SCORE {old:.2f}→{new:.2f}")
            elif forex["win_rate"] > 0.62 and old > 0.28:
                new = self._clamp("FOREX_MIN_SCORE", old - 0.02)
                if new != old:
                    self.cfg.FOREX_MIN_SCORE = new
                    changes.append(f"FOREX_MIN_SCORE {old:.2f}→{new:.2f}")

        dex = market_stats.get("dex", {})
        if dex.get("n", 0) >= self.MIN_PER_MARKET:
            old_size  = self.cfg.DEX_BASE_POSITION_USD
            old_score = self.cfg.DEX_MIN_SCORE
            if dex["win_rate"] < 0.35:
                new_size  = self._clamp("DEX_BASE_POSITION_USD", old_size * 0.80)
                new_score = self._clamp("DEX_MIN_SCORE", old_score + 0.02)
                if new_size != old_size:
                    self.cfg.DEX_BASE_POSITION_USD = new_size
                    changes.append(f"DEX_BASE_POSITION_USD ${old_size:.0f}→${new_size:.0f} "
                                   f"(DEX win={dex['win_rate']:.0%})")
                if new_score != old_score:
                    self.cfg.DEX_MIN_SCORE = new_score
                    changes.append(f"DEX_MIN_SCORE {old_score:.2f}→{new_score:.2f}")
            elif dex["win_rate"] > 0.55 and dex["avg_pnl_pct"] > 2.0:
                new_size = self._clamp("DEX_BASE_POSITION_USD", old_size * 1.15)
                if new_size != old_size:
                    self.cfg.DEX_BASE_POSITION_USD = new_size
                    changes.append(f"DEX_BASE_POSITION_USD ${old_size:.0f}→${new_size:.0f} "
                                   f"(DEX profitable {dex['win_rate']:.0%})")
        return changes

    def _repivot_risk_reward(self, exit_stats: dict) -> list[str]:
        changes = []
        if exit_stats.get("total", 0) < 15:
            return changes
        stop_rate = exit_stats["stop_rate"] + exit_stats["trailing_rate"]
        tp_rate   = exit_stats["tp_rate"]
        old_stop  = self.cfg.STOP_LOSS_PCT
        old_tp    = self.cfg.TAKE_PROFIT_PCT

        if stop_rate > 0.55:
            new = self._clamp("STOP_LOSS_PCT", old_stop * 1.10)
            if new != old_stop:
                self.cfg.STOP_LOSS_PCT = new
                changes.append(f"STOP_LOSS_PCT {old_stop:.3f}→{new:.3f} "
                                f"(stops hit {stop_rate:.0%})")
        if tp_rate > 0.60:
            new = self._clamp("TAKE_PROFIT_PCT", old_tp * 1.10)
            if new != old_tp:
                self.cfg.TAKE_PROFIT_PCT = new
                changes.append(f"TAKE_PROFIT_PCT {old_tp:.3f}→{new:.3f} "
                                f"(TPs hit {tp_rate:.0%})")

        # Time-based exits: if >40% of trades close on stale/time exit with negative PnL,
        # the bot is holding losers too long → tighten stale exit
        time_rate = exit_stats.get("time_rate", 0)
        if time_rate > 0.40:
            old_stale = getattr(self.cfg, "DEX_STALE_EXIT_HOURS", 2.0)
            new_stale = self._clamp("DEX_STALE_EXIT_HOURS", old_stale - 0.25)
            if new_stale != old_stale:
                self.cfg.DEX_STALE_EXIT_HOURS = new_stale
                changes.append(f"DEX_STALE_EXIT_HOURS {old_stale}→{new_stale} "
                                f"(time exits = {time_rate:.0%} of trades)")
        return changes

    def _repivot_indicator_weights(self, regime: str) -> list[str]:
        if regime not in ("trending", "ranging", "volatile"):
            return []
        weights  = dict(self.cfg.STRATEGY_WEIGHTS)
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

        weights = {
            k: max(_INDICATOR_BOUNDS.get(k, (0.05, 0.40))[0],
                   min(_INDICATOR_BOUNDS.get(k, (0.05, 0.40))[1], v))
            for k, v in weights.items()
        }
        total = sum(weights.values())
        weights = {k: round(v / total, 4) for k, v in weights.items()}

        if weights != original:
            self.cfg.STRATEGY_WEIGHTS = weights
            return [f"STRATEGY_WEIGHTS → {regime} regime "
                    f"(ema={weights.get('ema_cross', 0):.2f} "
                    f"rsi={weights.get('rsi', 0):.2f} "
                    f"boll={weights.get('bollinger', 0):.2f})"]
        return []

    # ─────────────────────────────────────────────────────────────────────────
    # DEX deep analysis (uses all-time history for maximum sample size)
    # ─────────────────────────────────────────────────────────────────────────

    def _repivot_dex_signals(self, dex_all: list, dex_recent: list) -> list[str]:
        """
        Per-signal win rate across all-time history, with a recent-10 comparison
        to detect whether specific signals are gaining or losing effectiveness.
        Adjusts DEX_MIN_SCORE based on lowest score bracket quality.
        """
        changes: list[str] = []

        # ── Signal-type scorecard ─────────────────────────────────────────────
        logger.info("  DEX signal scorecard (all-time n=%d):", len(dex_all))
        for label, keyword in sorted(_DEX_SIGNAL_KEYS.items()):
            hits    = [t for t in dex_all if any(keyword in s for s in t.get("signals", []))]
            if not hits:
                continue
            wins    = sum(1 for t in hits if t.get("pnl_usd", 0) > 0)
            avg_pnl = sum(t.get("pnl_pct", 0) for t in hits) / len(hits)
            avg_max = sum(t.get("max_gain_pct", 0) for t in hits) / len(hits)
            # Recent effectiveness
            hits_r  = [t for t in dex_recent if any(keyword in s for s in t.get("signals", []))]
            wr_r    = (sum(1 for t in hits_r if t.get("pnl_usd", 0) > 0) / len(hits_r)
                       if hits_r else None)
            recent_str = f"  recent-{len(dex_recent)}: {wr_r:.0%}" if wr_r is not None else ""
            logger.info("    %-10s  n=%-4d  win=%.0f%%  avgPnL=%+.1f%%  peakGain=+%.1f%%%s",
                        label, len(hits), wins / len(hits) * 100, avg_pnl, avg_max, recent_str)

        # ── Source scorecard ──────────────────────────────────────────────────
        sources: dict[str, dict] = {}
        for t in dex_all:
            src = t.get("dex_source") or "unknown"
            if src not in sources:
                sources[src] = {"n": 0, "wins": 0, "pnl": 0.0, "max_gain": 0.0}
            sources[src]["n"]       += 1
            sources[src]["wins"]    += 1 if t.get("pnl_usd", 0) > 0 else 0
            sources[src]["pnl"]     += t.get("pnl_pct", 0)
            sources[src]["max_gain"] += t.get("max_gain_pct", 0)

        logger.info("  DEX source scorecard:")
        for src, s in sorted(sources.items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr  = s["wins"] / s["n"]
            avg = s["pnl"] / s["n"]
            pk  = s["max_gain"] / s["n"]
            logger.info("    %-22s  n=%-4d  win=%.0f%%  avgPnL=%+.1f%%  peakGain=+%.1f%%",
                        src, s["n"], wr * 100, avg, pk)

        # ── Score-range heatmap ───────────────────────────────────────────────
        logger.info("  DEX score-range heatmap (all-time):")
        brackets = [(0.25, 0.30), (0.30, 0.35), (0.35, 0.40),
                    (0.40, 0.45), (0.45, 0.50), (0.50, 1.00)]
        for lo, hi in brackets:
            bucket = [t for t in dex_all if lo <= t.get("signal_score", 0) < hi]
            if not bucket:
                continue
            wins = sum(1 for t in bucket if t.get("pnl_usd", 0) > 0)
            avg  = sum(t.get("pnl_pct", 0) for t in bucket) / len(bucket)
            logger.info("    score %.2f–%.2f  n=%-4d  win=%.0f%%  avg=%+.1f%%",
                        lo, hi, len(bucket), wins / len(bucket) * 100, avg)

        # ── Adaptive threshold from lowest bracket ────────────────────────────
        low_bracket = [t for t in dex_all if t.get("signal_score", 0) < 0.38]
        if len(low_bracket) >= 8:
            wr  = sum(1 for t in low_bracket if t.get("pnl_usd", 0) > 0) / len(low_bracket)
            avg = sum(t.get("pnl_pct", 0) for t in low_bracket) / len(low_bracket)
            old = self.cfg.DEX_MIN_SCORE
            if wr < 0.28 or avg < -5:
                new = self._clamp("DEX_MIN_SCORE", old + 0.02)
                if new != old:
                    self.cfg.DEX_MIN_SCORE = new
                    changes.append(f"DEX_MIN_SCORE {old:.2f}→{new:.2f} "
                                   f"(sub-0.38 bucket: win={wr:.0%} avg={avg:+.1f}%)")
            elif wr > 0.55 and avg > 0 and old > 0.33:
                new = self._clamp("DEX_MIN_SCORE", old - 0.01)
                if new != old:
                    self.cfg.DEX_MIN_SCORE = new
                    changes.append(f"DEX_MIN_SCORE {old:.2f}→{new:.2f} "
                                   f"(sub-0.38 profitable: win={wr:.0%})")
        return changes

    def _repivot_dex_hold_time(self, dex_all: list) -> list[str]:
        """
        Hold-time vs outcome, partial-profit impact, BURST vs plain,
        exit-reason quality. Adjusts DEX_STALE_EXIT_HOURS if long holds lose.
        """
        changes: list[str] = []

        short  = [t for t in dex_all if t.get("hold_seconds", 9e9) < 1800]
        medium = [t for t in dex_all if 1800 <= t.get("hold_seconds", 0) < 7200]
        long   = [t for t in dex_all if t.get("hold_seconds", 0) >= 7200]

        def _s(g):
            if not g: return 0.0, 0.0
            return (sum(1 for t in g if t.get("pnl_usd", 0) > 0) / len(g),
                    sum(t.get("pnl_pct", 0) for t in g) / len(g))

        swr, savg = _s(short)
        mwr, mavg = _s(medium)
        lwr, lavg = _s(long)

        logger.info("  DEX hold-time: <30m n=%-3d win=%.0f%% avg=%+.1f%%  "
                    "30m–2h n=%-3d win=%.0f%% avg=%+.1f%%  "
                    ">2h n=%-3d win=%.0f%% avg=%+.1f%%",
                    len(short), swr*100, savg,
                    len(medium), mwr*100, mavg,
                    len(long), lwr*100, lavg)

        if len(long) >= 4 and len(short) >= 4 and lavg < savg - 10 and lwr < 0.35:
            old = getattr(self.cfg, "DEX_STALE_EXIT_HOURS", 2.0)
            new = self._clamp("DEX_STALE_EXIT_HOURS", old - 0.25)
            if new != old:
                self.cfg.DEX_STALE_EXIT_HOURS = new
                changes.append(f"DEX_STALE_EXIT_HOURS {old}→{new} "
                                f"(long avg={lavg:+.1f}% vs short avg={savg:+.1f}%)")

        # Partial profit analysis
        with_p    = [t for t in dex_all if t.get("partials_taken")]
        without_p = [t for t in dex_all if not t.get("partials_taken")]
        if len(with_p) >= 4 and len(without_p) >= 4:
            _, wp_avg  = _s(with_p)
            _, wop_avg = _s(without_p)
            logger.info("  DEX partials: with n=%-3d avg=%+.1f%%  |  without n=%-3d avg=%+.1f%%",
                        len(with_p), wp_avg, len(without_p), wop_avg)

        # BURST vs plain
        burst = [t for t in dex_all if t.get("is_burst")]
        plain = [t for t in dex_all if not t.get("is_burst")]
        if burst:
            bwr, bavg = _s(burst)
            pwr, pavg = _s(plain)
            logger.info("  BURST n=%-3d win=%.0f%% avg=%+.1f%%  |  plain n=%-3d win=%.0f%% avg=%+.1f%%",
                        len(burst), bwr*100, bavg, len(plain), pwr*100, pavg)

        # Exit-reason effectiveness
        exits: dict[str, list] = {}
        for t in dex_all:
            cat = "stop" if "stop" in t.get("close_reason", "").lower() \
                  else "take_profit" if "take profit" in t.get("close_reason", "").lower() \
                  else "trailing" if "trailing" in t.get("close_reason", "").lower() \
                  else "time" if "stale" in t.get("close_reason", "").lower() \
                  else "other"
            exits.setdefault(cat, []).append(t)
        logger.info("  DEX exit reasons:")
        for cat, ts in sorted(exits.items(), key=lambda x: len(x[1]), reverse=True):
            wr, avg = _s(ts)
            logger.info("    %-14s  n=%-4d  win=%.0f%%  avg=%+.1f%%",
                        cat, len(ts), wr*100, avg)

        return changes

    # ─────────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────────

    def _clamp(self, param: str, value: float) -> float:
        lo, hi = _BOUNDS.get(param, (value, value))
        return round(max(lo, min(hi, value)), 4)
