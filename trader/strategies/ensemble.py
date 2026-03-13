"""
Ensemble Signal Engine
Aggregates multiple technical signals into a single actionable trade decision.
Applies regime filtering, trend confirmation, and conviction scoring.
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

import config
import indicators as ind

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """Structured output from the ensemble engine."""
    asset_id: str           # e.g. "bitcoin", "BTC/USD", "AAPL"
    market: str             # "crypto", "forex", "stocks"
    symbol: str             # Short ticker, e.g. "BTC"
    current_price: float
    signal: str             # "BUY", "SELL", "HOLD"
    score: float            # [-1, +1] ensemble score
    conviction: float       # [0, 1] confidence level
    stop_loss: float
    take_profit: float
    component_scores: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    regime: str = "unknown"    # "trending", "ranging", "volatile"
    trend_direction: str = "neutral"  # "up", "down", "neutral"

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "market": self.market,
            "symbol": self.symbol,
            "current_price": self.current_price,
            "signal": self.signal,
            "score": round(self.score, 4),
            "conviction": round(self.conviction, 4),
            "stop_loss": round(self.stop_loss, 6),
            "take_profit": round(self.take_profit, 6),
            "component_scores": {k: round(v, 4) for k, v in self.component_scores.items()},
            "reasons": self.reasons,
            "regime": self.regime,
            "trend_direction": self.trend_direction,
        }


class EnsembleSignal:
    """
    Generates trade signals by combining:
    1. Technical indicators (RSI, MACD, BB, EMA, Momentum, Volume)
    2. Market regime detection (trending vs ranging)
    3. Multi-timeframe trend confirmation
    4. Dynamic stop/target calculation (ATR-based)
    """

    def __init__(self, weights: dict = None):
        self.weights = weights or config.STRATEGY_WEIGHTS

    def analyze(self, df: pd.DataFrame, asset_id: str, market: str = "crypto",
                symbol: str = "") -> Optional[TradeSignal]:
        """
        Full analysis pipeline. Returns TradeSignal or None if insufficient data.
        """
        if df.empty or len(df) < 30:
            logger.debug("Insufficient data for %s (%d rows)", asset_id, len(df))
            return None

        close  = df["close"]
        volume = df.get("volume", pd.Series(dtype=float))
        high   = df.get("high", close)
        low    = df.get("low", close)
        current_price = float(close.iloc[-1])

        if current_price <= 0:
            return None

        # ── 1. Compute individual signals ──────────────────────────────────
        components = {
            "rsi":       ind.rsi_signal(close),
            "macd":      ind.macd_signal(close),
            "bollinger": ind.bollinger_signal(close),
            "ema_cross": ind.ema_cross_signal(close),
            "momentum":  ind.momentum_signal(close),
            "volume":    ind.volume_signal(close, volume) if not volume.empty and volume.sum() > 0 else 0.0,
        }

        # ── 2. Regime detection ────────────────────────────────────────────
        regime, trend_dir = self._detect_regime(close, high, low)

        # ── 3. Regime-adjusted weights ────────────────────────────────────
        adj_weights = self._regime_weights(regime, self.weights)

        # ── 4. Weighted ensemble score ─────────────────────────────────────
        total_w = sum(adj_weights.get(k, 0) for k in components)
        raw_score = sum(components[k] * adj_weights.get(k, 0) for k in components) / total_w if total_w > 0 else 0.0
        score = float(np.clip(raw_score, -1, 1))

        # ── 5. Trend filter: penalize counter-trend signals ────────────────
        if trend_dir == "up" and score < 0:
            score *= 0.6    # Reduce short signal strength in uptrend
        elif trend_dir == "down" and score > 0:
            score *= 0.6    # Reduce long signal strength in downtrend

        # ── 6. Conviction (signal agreement) ──────────────────────────────
        conviction = self._conviction(components, score)

        # ── 7. Signal classification ───────────────────────────────────────
        min_strength = config.MIN_SIGNAL_STRENGTH
        if score >= min_strength:
            signal = "BUY"
        elif score <= -min_strength:
            signal = "SELL"
        else:
            signal = "HOLD"

        # ── 8. Dynamic stop / target ───────────────────────────────────────
        stop_loss, take_profit = self._compute_levels(close, high, low, signal, current_price)

        # ── 9. Build reasons ──────────────────────────────────────────────
        reasons = self._build_reasons(components, regime, trend_dir, score)

        return TradeSignal(
            asset_id=asset_id,
            market=market,
            symbol=symbol or asset_id.upper(),
            current_price=current_price,
            signal=signal,
            score=score,
            conviction=conviction,
            stop_loss=stop_loss,
            take_profit=take_profit,
            component_scores=components,
            reasons=reasons,
            regime=regime,
            trend_direction=trend_dir,
        )

    def _detect_regime(self, close: pd.Series, high: pd.Series, low: pd.Series) -> tuple[str, str]:
        """
        Detect market regime: trending or ranging.
        Uses ADX proxy and EMA alignment.
        """
        period = 20
        if len(close) < period + 5:
            return "unknown", "neutral"

        # EMA alignment for trend direction
        e9  = ind.ema(close, 9)
        e21 = ind.ema(close, 21)
        e50 = ind.ema(close, 50) if len(close) >= 55 else e21

        is_up_trend   = e9.iloc[-1] > e21.iloc[-1] > e50.iloc[-1]
        is_down_trend = e9.iloc[-1] < e21.iloc[-1] < e50.iloc[-1]
        trend_dir = "up" if is_up_trend else "down" if is_down_trend else "neutral"

        # Volatility / ADX proxy: compare current ATR to avg ATR
        try:
            a = ind.atr(high, low, close, 14)
            atr_now = a.iloc[-1]
            atr_avg = a.tail(period).mean()
            atr_ratio = atr_now / atr_avg if atr_avg > 0 else 1.0
        except Exception:
            atr_ratio = 1.0

        # BB width as ranging proxy
        upper, mid, lower = ind.bollinger(close)
        bb_width = (upper.iloc[-1] - lower.iloc[-1]) / mid.iloc[-1] if mid.iloc[-1] > 0 else 0
        bb_avg   = ((upper - lower) / mid).tail(period).mean()
        bb_expanding = bb_width > bb_avg * 1.1

        if atr_ratio > 1.3 and bb_expanding:
            regime = "volatile"
        elif (is_up_trend or is_down_trend) and bb_expanding:
            regime = "trending"
        else:
            regime = "ranging"

        return regime, trend_dir

    def _regime_weights(self, regime: str, base: dict) -> dict:
        """
        Adjust indicator weights based on market regime.
        Trending: favour EMA crossover & momentum.
        Ranging:  favour RSI & Bollinger.
        Volatile: reduce all, favour volume.
        """
        w = dict(base)
        if regime == "trending":
            w["ema_cross"] = w.get("ema_cross", 0) * 1.5
            w["momentum"]  = w.get("momentum", 0) * 1.4
            w["bollinger"] = w.get("bollinger", 0) * 0.6
            w["rsi"]       = w.get("rsi", 0) * 0.7
        elif regime == "ranging":
            w["rsi"]       = w.get("rsi", 0) * 1.5
            w["bollinger"] = w.get("bollinger", 0) * 1.5
            w["ema_cross"] = w.get("ema_cross", 0) * 0.6
            w["momentum"]  = w.get("momentum", 0) * 0.6
        elif regime == "volatile":
            w["volume"]    = w.get("volume", 0) * 2.0
            w["macd"]      = w.get("macd", 0) * 1.3
            for k in ["rsi", "bollinger", "ema_cross", "momentum"]:
                w[k] = w.get(k, 0) * 0.7
        return w

    def _conviction(self, components: dict, score: float) -> float:
        """
        Conviction = fraction of signals agreeing with the final score direction.
        Weighted by absolute magnitude.
        """
        if score == 0:
            return 0.0
        direction = np.sign(score)
        agreed = sum(1 for v in components.values() if np.sign(v) == direction and abs(v) > 0.1)
        total  = sum(1 for v in components.values() if abs(v) > 0.1)
        if total == 0:
            return 0.0
        agreement_ratio = agreed / total
        # Boost by absolute score magnitude
        return float(np.clip(agreement_ratio * (0.5 + abs(score) * 0.5), 0, 1))

    def _compute_levels(self, close: pd.Series, high: pd.Series, low: pd.Series,
                        signal: str, price: float) -> tuple[float, float]:
        """ATR-based stop loss and take profit levels."""
        try:
            a = ind.atr(high, low, close, 14)
            atr_val = a.iloc[-1]
            if pd.isna(atr_val) or atr_val <= 0:
                raise ValueError("bad atr")
        except Exception:
            atr_val = price * 0.02  # Fallback: 2% of price

        if signal == "BUY":
            stop   = price - 2.0 * atr_val
            target = price + 4.0 * atr_val   # 2:1 R/R
        elif signal == "SELL":
            stop   = price + 2.0 * atr_val
            target = price - 4.0 * atr_val
        else:
            stop   = price * (1 - config.STOP_LOSS_PCT)
            target = price * (1 + config.TAKE_PROFIT_PCT)

        return round(stop, 8), round(target, 8)

    def _build_reasons(self, components: dict, regime: str,
                       trend_dir: str, score: float) -> list[str]:
        reasons = [f"Regime: {regime}, Trend: {trend_dir}, Score: {score:.2f}"]
        for name, val in components.items():
            if abs(val) >= 0.3:
                direction = "bullish" if val > 0 else "bearish"
                reasons.append(f"{name.upper()} {direction} ({val:.2f})")
        return reasons
