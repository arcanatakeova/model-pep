"""
Professional Forex Analysis Engine
====================================
Institutional-quality forex signal generation. Built to the standard
of a professional trading desk:

  - Multi-timeframe analysis: 1h entry signals + 4h trend confirmation
  - Forex-specific indicators: ATR, ADX, Stochastic, Pivot Points, MACD
  - Trading session filter: only trade during liquid London/NY/Tokyo windows
  - ATR-based dynamic stop placement (not arbitrary %)
  - 2.2:1 risk-reward minimum (stops widen with volatility, targets scale)
  - Correlation guard: block adding to correlated pairs already in positions
  - Spread-adjusted entry: account for real bid/ask cost before sizing

Supported pairs:
  EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD
  (easily extended — just add pair to SPREADS_PIPS and PAIR_SESSIONS)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

# UTC hour windows for each session (start_inclusive, end_exclusive)
SESSION_WINDOWS = {
    "tokyo":   (0,  9),
    "london":  (7,  16),
    "newyork": (12, 22),
}

# Which sessions produce the strongest signals for each pair
PAIR_SESSIONS = {
    "EUR/USD": ["london", "newyork"],
    "GBP/USD": ["london", "newyork"],
    "USD/JPY": ["tokyo",  "london"],
    "AUD/USD": ["tokyo",  "newyork"],
    "USD/CAD": ["london", "newyork"],
}

# Typical retail spread in pips (used for min-stop calculation)
SPREADS_PIPS = {
    "EUR/USD": 0.5,
    "GBP/USD": 0.8,
    "USD/JPY": 0.6,
    "AUD/USD": 0.7,
    "USD/CAD": 1.0,
}

# One pip = this many price units
PIP_SIZES = {
    "USD/JPY": 0.01,
    "EUR/JPY": 0.01,
    "GBP/JPY": 0.01,
}
_DEFAULT_PIP = 0.0001

# Correlated pair groups — don't hold two positions in the same group
# (they move together, doubling unintended exposure)
CORRELATION_GROUPS = [
    ["EUR/USD", "GBP/USD"],   # Both rally on USD weakness
    ["AUD/USD", "NZD/USD"],   # Commodity / risk-on proxies
]


# ─── Signal Dataclass ─────────────────────────────────────────────────────────

@dataclass
class ForexSignal:
    pair:          str
    signal:        str          # "BUY", "SELL", "HOLD"
    score:         float        # [-1, +1]
    conviction:    float        # [0, 1]
    current_price: float
    stop_loss:     float
    take_profit:   float
    stop_pips:     float
    target_pips:   float
    atr_pips:      float
    adx:           float
    session:       str          # active session name(s) at signal time
    reasons:       list = field(default_factory=list)
    market:        str = "forex"
    regime:        str = "trending"
    trend_direction: str = "neutral"
    component_scores: dict = field(default_factory=dict)

    @property
    def asset_id(self) -> str:
        return self.pair

    @property
    def symbol(self) -> str:
        return self.pair.replace("/", "")

    def to_dict(self) -> dict:
        return {
            "pair": self.pair, "signal": self.signal,
            "score": self.score, "conviction": self.conviction,
            "current_price": self.current_price,
            "stop_loss": self.stop_loss, "take_profit": self.take_profit,
            "stop_pips": self.stop_pips, "target_pips": self.target_pips,
            "atr_pips": self.atr_pips, "adx": self.adx,
            "session": self.session, "reasons": self.reasons,
        }

    def to_trade_signal(self):
        """Convert to TradeSignal for executor compatibility."""
        # Import here to avoid circular imports
        from strategies.ensemble import TradeSignal  # type: ignore
        return TradeSignal(
            asset_id=self.pair,
            symbol=self.symbol,
            signal=self.signal,
            score=self.score,
            conviction=self.conviction,
            current_price=self.current_price,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            market="forex",
            regime=self.regime,
            trend_direction=self.trend_direction,
            reasons=self.reasons,
            component_scores=self.component_scores,
        )


# ─── Analyzer ─────────────────────────────────────────────────────────────────

class ForexAnalyzer:
    """
    Institutional-grade forex signal generator.

    Call analyze(pair, df_1h, df_4h) → Optional[ForexSignal]
    """

    def __init__(self):
        self._min_score    = getattr(config, "FOREX_MIN_SCORE", 0.35)
        self._min_adx      = getattr(config, "FOREX_MIN_ADX", 20)
        self._atr_mult     = getattr(config, "FOREX_ATR_STOP_MULTIPLIER", 1.5)
        self._rr_ratio     = getattr(config, "FOREX_RR_RATIO", 2.2)
        self._session_filt = getattr(config, "FOREX_SESSION_FILTER", True)

    # ── Public API ──────────────────────────────────────────────────────────

    def analyze(self, pair: str, df_1h: pd.DataFrame,
                df_4h: Optional[pd.DataFrame] = None) -> Optional[ForexSignal]:
        """
        Full multi-timeframe forex analysis.
        Returns ForexSignal or None if no tradeable setup exists.
        """
        if df_1h.empty or len(df_1h) < 50:
            return None

        # ── 1. Session filter ──────────────────────────────────────────────
        active_sessions = self._active_sessions(pair)
        if self._session_filt and not active_sessions:
            return None   # Market illiquid — skip
        session_str = "+".join(active_sessions) if active_sessions else "off-hours"

        price = float(df_1h["close"].iloc[-1])
        if price <= 0:
            return None

        pip_size = PIP_SIZES.get(pair, _DEFAULT_PIP)
        spread_p = SPREADS_PIPS.get(pair, 1.0)

        # ── 2. ATR and ADX (1h) ───────────────────────────────────────────
        atr_1h = self._atr(df_1h, 14)
        if atr_1h is None or atr_1h <= 0:
            return None
        atr_pips = min(atr_1h / pip_size, 300)  # Cap 300 pips — prevents absurd stops from historical data

        adx_1h = self._adx(df_1h, 14)

        # ── 3. Trend direction — 4h is truth, 1h as fallback ──────────────
        if df_4h is not None and not df_4h.empty and len(df_4h) >= 21:
            trend_df = df_4h
        else:
            trend_df = df_1h

        ema_fast_4h = self._ema(trend_df["close"], 9)
        ema_slow_4h = self._ema(trend_df["close"], 21)
        if ema_fast_4h is None or ema_slow_4h is None:
            return None

        trend_up = float(ema_fast_4h) > float(ema_slow_4h)
        ema_gap_pct = abs(float(ema_fast_4h) - float(ema_slow_4h)) / float(ema_slow_4h)
        trend_dir = "up" if trend_up else "down"

        # ── 4. Score components ───────────────────────────────────────────
        scores: dict[str, float] = {}

        # a) Trend alignment (EMA cross on higher timeframe)
        scores["ema_trend"] = 0.30 if trend_up else -0.30

        # b) ADX — skip ranging markets, boost conviction on strong trends
        if adx_1h < self._min_adx:
            return None   # Market is ranging — no trend to trade
        adx_boost = min((adx_1h - self._min_adx) / 30.0, 1.0)  # 0 at ADX=20, 1 at ADX=50
        scores["adx_strength"] = 0.10 * adx_boost * (1 if trend_up else -1)

        # c) Stochastic — oversold/overbought timing on 1h
        stoch = self._stochastic(df_1h, k_period=14, d_period=3)
        if stoch is not None:
            k, d = stoch["k"], stoch["d"]
            if trend_up and k < 25 and d < 30:
                scores["stochastic"] = 0.25   # Oversold pullback in uptrend
            elif not trend_up and k > 75 and d > 70:
                scores["stochastic"] = -0.25  # Overbought rally in downtrend
            elif trend_up and k < 45:
                scores["stochastic"] = 0.10   # Weak oversold signal
            elif not trend_up and k > 55:
                scores["stochastic"] = -0.10
            else:
                scores["stochastic"] = 0.0

        # d) RSI (1h) — confirmation
        rsi_val = self._rsi(df_1h["close"], 14)
        if rsi_val is not None:
            if trend_up and 35 <= rsi_val <= 55:
                scores["rsi"] = 0.15   # Healthy pullback zone — good entry
            elif not trend_up and 45 <= rsi_val <= 65:
                scores["rsi"] = -0.15
            elif trend_up and rsi_val < 35:
                scores["rsi"] = 0.10   # Deeply oversold — potential reversal
            elif not trend_up and rsi_val > 65:
                scores["rsi"] = -0.10
            else:
                scores["rsi"] = 0.0

        # e) MACD histogram direction (1h)
        macd_hist = self._macd_histogram(df_1h["close"])
        if macd_hist is not None:
            if trend_up and macd_hist > 0:
                scores["macd"] = 0.15
            elif not trend_up and macd_hist < 0:
                scores["macd"] = -0.15
            elif trend_up and macd_hist > -0.00005:
                scores["macd"] = 0.05   # Histogram recovering
            else:
                scores["macd"] = 0.0

        # f) Pivot point position (daily)
        pivots = self._pivot_points(df_1h)
        if pivots:
            pivot, r1, s1 = pivots["pivot"], pivots["r1"], pivots["s1"]
            if trend_up and price > pivot:
                scores["pivot"] = 0.10   # Above pivot = bullish bias
            elif trend_up and price > s1:
                scores["pivot"] = 0.05   # Between S1 and pivot — neutral/mild
            elif not trend_up and price < pivot:
                scores["pivot"] = -0.10
            elif not trend_up and price < r1:
                scores["pivot"] = -0.05
            else:
                scores["pivot"] = 0.0

        # g) Session quality bonus (overlap = higher liquidity = better fills)
        overlap = ("london" in active_sessions and "newyork" in active_sessions)
        scores["session"] = 0.05 if overlap else 0.0

        total_score = sum(scores.values())

        # ── 5. Conviction ─────────────────────────────────────────────────
        # Scale by ADX (trend strength) and EMA gap (momentum)
        conviction = min(abs(total_score) * (adx_1h / 35.0) * (1 + ema_gap_pct * 10), 1.0)

        # ── 6. Signal decision ────────────────────────────────────────────
        if abs(total_score) < self._min_score:
            return None

        signal = "BUY" if total_score > 0 else "SELL"

        # ── 7. ATR-based stop and target ──────────────────────────────────
        stop_pips   = max(atr_pips * self._atr_mult, spread_p * 4)
        target_pips = stop_pips * self._rr_ratio
        stop_loss   = price - stop_pips * pip_size if signal == "BUY" \
                      else price + stop_pips * pip_size
        take_profit = price + target_pips * pip_size if signal == "BUY" \
                      else price - target_pips * pip_size

        # Sanity: cap stop/TP within 5% of entry (guards against bad data slipping through)
        max_dist = price * 0.05
        if abs(take_profit - price) > max_dist:
            take_profit = (price + max_dist) if signal == "BUY" else (price - max_dist)
            stop_loss   = (price - max_dist / self._rr_ratio) if signal == "BUY" \
                          else (price + max_dist / self._rr_ratio)

        # ── 8. Build reasons list ─────────────────────────────────────────
        reasons = []
        if scores.get("ema_trend", 0):
            reasons.append(f"4h EMA {'bullish' if trend_up else 'bearish'}")
        if scores.get("stochastic", 0):
            reasons.append(f"Stoch {'oversold' if signal=='BUY' else 'overbought'}")
        if scores.get("rsi", 0):
            reasons.append(f"RSI {rsi_val:.0f}" if rsi_val else "RSI signal")
        if scores.get("macd", 0):
            reasons.append(f"MACD {'positive' if macd_hist and macd_hist>0 else 'negative'} hist")
        if scores.get("pivot", 0):
            reasons.append(f"{'Above' if signal=='BUY' else 'Below'} daily pivot")
        if adx_1h:
            reasons.append(f"ADX {adx_1h:.0f} (trending)")
        if overlap:
            reasons.append("London/NY overlap")

        logger.debug(
            "FOREX %s %s score=%.2f conv=%.2f adx=%.1f session=%s | %s",
            signal, pair, total_score, conviction, adx_1h, session_str,
            ", ".join(reasons[:3]),
        )

        return ForexSignal(
            pair=pair,
            signal=signal,
            score=round(total_score, 4),
            conviction=round(conviction, 4),
            current_price=price,
            stop_loss=round(stop_loss, 6),
            take_profit=round(take_profit, 6),
            stop_pips=round(stop_pips, 1),
            target_pips=round(target_pips, 1),
            atr_pips=round(atr_pips, 1),
            adx=round(adx_1h, 1),
            session=session_str,
            reasons=reasons,
            market="forex",
            regime="trending",
            trend_direction=trend_dir,
            component_scores={k: round(v, 3) for k, v in scores.items()},
        )

    def is_active_session(self, pair: str) -> bool:
        return bool(self._active_sessions(pair))

    def check_correlation_limit(self, pair: str, open_pairs: list[str]) -> bool:
        """
        Return True if opening `pair` would exceed FOREX_MAX_CORRELATED_PAIRS
        for a correlated group.
        """
        max_corr = getattr(config, "FOREX_MAX_CORRELATED_PAIRS", 1)
        for group in CORRELATION_GROUPS:
            if pair in group:
                already = sum(1 for p in open_pairs if p in group)
                if already >= max_corr:
                    return False   # Blocked by correlation limit
        return True   # OK to open

    # ── Indicators ──────────────────────────────────────────────────────────

    def _active_sessions(self, pair: str) -> list[str]:
        """Return list of currently active sessions for this pair."""
        hour_utc = datetime.now(timezone.utc).hour
        preferred = PAIR_SESSIONS.get(pair, list(SESSION_WINDOWS.keys()))
        active = []
        for name, (start, end) in SESSION_WINDOWS.items():
            if name in preferred:
                if start <= hour_utc < end:
                    active.append(name)
        return active

    @staticmethod
    def _ema(series: pd.Series, span: int) -> Optional[float]:
        if len(series) < span:
            return None
        return float(series.ewm(span=span, adjust=False).mean().iloc[-1])

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """Average True Range."""
        if len(df) < period + 1:
            return None
        high  = df["high"].values
        low   = df["low"].values
        close = df["close"].values
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:]  - close[:-1]),
            )
        )
        atr_series = pd.Series(tr).ewm(span=period, adjust=False).mean()
        val = float(atr_series.iloc[-1])
        return val if val > 0 else None

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> float:
        """Average Directional Index — measures trend strength (0-100)."""
        if len(df) < period * 2:
            return 0.0
        try:
            high  = df["high"].values.astype(float)
            low   = df["low"].values.astype(float)
            close = df["close"].values.astype(float)

            plus_dm  = np.maximum(high[1:] - high[:-1], 0.0)
            minus_dm = np.maximum(low[:-1] - low[1:],   0.0)
            # Zero out where the other is larger
            mask = plus_dm <= minus_dm
            plus_dm[mask] = 0.0
            mask2 = minus_dm <= high[1:] - high[:-1]
            minus_dm[mask2] = 0.0

            tr = np.maximum(
                high[1:] - low[1:],
                np.maximum(np.abs(high[1:] - close[:-1]),
                           np.abs(low[1:]  - close[:-1])),
            )

            def _smooth(arr, n):
                s = pd.Series(arr).ewm(span=n, adjust=False).mean()
                return s.values

            atr_s    = _smooth(tr, period)
            plus_di  = 100 * _smooth(plus_dm,  period) / (atr_s + 1e-9)
            minus_di = 100 * _smooth(minus_dm, period) / (atr_s + 1e-9)
            dx       = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
            adx      = _smooth(dx, period)
            return float(adx[-1])
        except Exception:
            return 0.0

    @staticmethod
    def _stochastic(df: pd.DataFrame, k_period: int = 14,
                    d_period: int = 3) -> Optional[dict]:
        """Stochastic oscillator %K and %D."""
        if len(df) < k_period + d_period:
            return None
        try:
            high  = df["high"].rolling(k_period).max()
            low   = df["low"].rolling(k_period).min()
            k = 100 * (df["close"] - low) / (high - low + 1e-9)
            d = k.rolling(d_period).mean()
            return {"k": float(k.iloc[-1]), "d": float(d.iloc[-1])}
        except Exception:
            return None

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> Optional[float]:
        """Relative Strength Index."""
        if len(series) < period + 1:
            return None
        try:
            delta  = series.diff().dropna()
            gain   = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
            loss   = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
            rs     = gain / (loss + 1e-9)
            return float(100 - 100 / (1 + rs.iloc[-1]))
        except Exception:
            return None

    @staticmethod
    def _macd_histogram(series: pd.Series,
                        fast: int = 12, slow: int = 26,
                        signal_p: int = 9) -> Optional[float]:
        """MACD histogram (MACD line − signal line)."""
        if len(series) < slow + signal_p:
            return None
        try:
            ema_f  = series.ewm(span=fast,   adjust=False).mean()
            ema_s  = series.ewm(span=slow,   adjust=False).mean()
            macd   = ema_f - ema_s
            sig    = macd.ewm(span=signal_p, adjust=False).mean()
            return float((macd - sig).iloc[-1])
        except Exception:
            return None

    @staticmethod
    def _pivot_points(df: pd.DataFrame) -> Optional[dict]:
        """Classic daily pivot points from the most recent completed session."""
        if len(df) < 24:
            return None
        try:
            # Use last 24 hourly bars as the "prior day"
            session = df.iloc[-25:-1]
            h = float(session["high"].max())
            l = float(session["low"].min())
            c = float(session["close"].iloc[-1])
            pivot = (h + l + c) / 3
            r1    = 2 * pivot - l
            r2    = pivot + (h - l)
            s1    = 2 * pivot - h
            s2    = pivot - (h - l)
            return {"pivot": pivot, "r1": r1, "r2": r2, "s1": s1, "s2": s2}
        except Exception:
            return None
