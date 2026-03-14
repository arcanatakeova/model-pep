"""
Scalping Signal Engine — Fast 5-Minute Timeframe Signals
=========================================================
Generates high-frequency trade signals using 5m candles.
Runs every 30 seconds to catch short-term momentum bursts
on BTC, ETH, and SOL for leveraged futures scalp trades.

Indicators used (5m timeframe):
  - RSI(14) with aggressive thresholds (25/75)
  - EMA(9) vs EMA(21) crossover momentum
  - Volume spike detection (current vs rolling avg)

Each signal also includes a 1h trend filter so scalp trades
only fire WITH the prevailing hourly trend.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import config
import data_fetcher as df_mod

logger = logging.getLogger(__name__)

# CryptoCompare 5-minute endpoint
_CC_BASE = "https://min-api.cryptocompare.com/data/v2/histominute"


@dataclass
class ScalpSignal:
    """A short-term scalp trade signal generated from 5m candles."""
    asset_id: str
    symbol: str
    signal: str            # "BUY", "SELL", or "HOLD"
    score: float           # [-1, +1]
    conviction: float      # [0, 1]
    current_price: float
    stop_loss: float
    take_profit: float
    market: str = "crypto"
    timeframe: str = "5m"
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "symbol": self.symbol,
            "signal": self.signal,
            "score": self.score,
            "conviction": self.conviction,
            "current_price": self.current_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "market": self.market,
            "timeframe": self.timeframe,
            "reasons": self.reasons,
        }


class ScalpingScanner:
    """
    Generates 5-minute scalp signals for fast leveraged futures trades.
    Designed to run every SCALP_INTERVAL_SEC (30s by default).
    """

    def __init__(self):
        self._cache: dict[str, tuple[float, list]] = {}  # symbol → (timestamp, ohlcv)
        self._cache_ttl = 25  # seconds — match DATA_CACHE_TTL

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def scan(self, symbols: Optional[list[str]] = None) -> list[ScalpSignal]:
        """
        Scan configured symbols on 5m timeframe.
        Returns actionable signals (BUY or SELL) sorted by score magnitude.
        """
        symbols = symbols or config.SCALP_SYMBOLS
        signals = []
        for sym in symbols:
            try:
                sig = self._analyze_symbol(sym)
                if sig and sig.signal != "HOLD":
                    signals.append(sig)
            except Exception as e:
                logger.debug("Scalp scan error for %s: %s", sym, e)

        signals.sort(key=lambda s: abs(s.score), reverse=True)
        return signals

    # ─────────────────────────────────────────────────────────────────────────
    # Core Analysis
    # ─────────────────────────────────────────────────────────────────────────

    def _analyze_symbol(self, symbol: str) -> Optional[ScalpSignal]:
        """Compute 5m indicators and build a ScalpSignal."""
        candles = self._get_5m_ohlcv(symbol)
        if candles is None or len(candles) < 30:
            return None

        closes  = [c.get("close", 0) for c in candles if c.get("close", 0) > 0]
        if len(closes) < 30:
            return None
        volumes = [c.get("volumeto", c.get("volumefrom", 0)) for c in candles if c.get("close", 0) > 0]
        price   = closes[-1]

        # ── Indicators ───────────────────────────────────────────────────────
        rsi      = self._rsi(closes, 14)
        ema9     = self._ema(closes, 9)
        ema21    = self._ema(closes, 21)
        vol_spike = self._volume_spike(volumes)
        momentum  = self._momentum(closes, 5)  # 5-bar momentum
        trend_1h  = self._get_1h_trend(symbol)  # +1 = up, -1 = down, 0 = neutral

        scores = []
        reasons = []

        # ── RSI ──────────────────────────────────────────────────────────────
        if rsi is not None:
            if rsi < config.SCALP_RSI_OVERSOLD:
                scores.append(1.0)
                reasons.append(f"RSI oversold ({rsi:.1f})")
            elif rsi > config.SCALP_RSI_OVERBOUGHT:
                scores.append(-1.0)
                reasons.append(f"RSI overbought ({rsi:.1f})")
            else:
                # Directional lean within neutral zone
                scores.append((50 - rsi) / 50 * 0.3)

        # ── EMA Cross ────────────────────────────────────────────────────────
        if ema9 and ema21:
            cross_score = (ema9 - ema21) / ema21
            scores.append(max(-1.0, min(1.0, cross_score * 100)))
            if ema9 > ema21:
                reasons.append(f"EMA9 > EMA21 (bullish cross)")
            else:
                reasons.append(f"EMA9 < EMA21 (bearish cross)")

        # ── Volume Spike ─────────────────────────────────────────────────────
        if vol_spike > 1.8:
            # Volume spike amplifies the directional bias
            direction = 1.0 if (ema9 or 0) >= (ema21 or 0) else -1.0
            scores.append(direction * min(vol_spike / 5, 1.0))
            reasons.append(f"Volume spike {vol_spike:.1f}x avg")

        # ── Momentum ─────────────────────────────────────────────────────────
        if momentum is not None:
            mom_score = max(-1.0, min(1.0, momentum * 20))
            scores.append(mom_score)
            if abs(momentum) > 0.005:
                direction_word = "up" if momentum > 0 else "down"
                reasons.append(f"Momentum {direction_word} ({momentum*100:+.2f}%/bar)")

        if not scores:
            return None

        raw_score = sum(scores) / len(scores)

        # ── 1h trend filter: cut score if counter-trend ───────────────────────
        if trend_1h != 0 and (raw_score * trend_1h) < 0:
            raw_score *= 0.40  # Heavy penalty for counter-trend scalps

        conviction = min(1.0, len([s for s in scores if abs(s) > 0.3]) / len(scores) + 0.1)

        if abs(raw_score) < config.SCALP_MIN_SCORE:
            return ScalpSignal(
                asset_id=symbol.lower(), symbol=symbol,
                signal="HOLD", score=raw_score, conviction=conviction,
                current_price=price, stop_loss=0.0, take_profit=0.0, reasons=reasons,
            )

        signal_dir = "BUY" if raw_score > 0 else "SELL"
        stop_pct   = config.STOP_LOSS_PCT * 0.8   # Tighter stop for scalps (2.4%)
        tp_pct     = config.TAKE_PROFIT_PCT * 0.7  # Tighter TP for scalps (4.2%)

        if signal_dir == "BUY":
            stop_loss   = price * (1 - stop_pct)
            take_profit = price * (1 + tp_pct)
        else:
            stop_loss   = price * (1 + stop_pct)
            take_profit = price * (1 - tp_pct)

        return ScalpSignal(
            asset_id=symbol.lower(),
            symbol=symbol,
            signal=signal_dir,
            score=round(raw_score, 4),
            conviction=round(conviction, 4),
            current_price=price,
            stop_loss=round(stop_loss, 8),
            take_profit=round(take_profit, 8),
            market="crypto",
            timeframe="5m",
            reasons=reasons,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Data Fetching
    # ─────────────────────────────────────────────────────────────────────────

    def _get_5m_ohlcv(self, symbol: str) -> Optional[list[dict]]:
        """
        Fetch 5-minute OHLCV from CryptoCompare histominute (aggregate=5).
        Free endpoint — no API key required.
        Caches results for 25 seconds.
        """
        now = time.time()
        cached_ts, cached_data = self._cache.get(symbol, (0, None))
        if cached_data and (now - cached_ts) < self._cache_ttl:
            return cached_data

        import requests
        try:
            url = f"{_CC_BASE}?fsym={symbol}&tsym=USD&limit={config.SCALP_CANDLES}&aggregate=5"
            r = requests.get(url, timeout=6)
            r.raise_for_status()
            data = r.json()
            candles = data.get("Data", {}).get("Data", [])
            if candles:
                self._cache[symbol] = (now, candles)
            return candles or None
        except Exception as e:
            logger.debug("5m OHLCV fetch failed for %s: %s", symbol, e)
            return None

    def _get_1h_trend(self, symbol: str) -> int:
        """
        Quick 1h trend check via CryptoCompare.
        Returns +1 (bullish), -1 (bearish), 0 (neutral).
        """
        try:
            df = df_mod.get_crypto_ohlcv_cc(symbol, limit=20)
            if df is None or df.empty:
                return 0
            closes = df["close"].tolist()
            ema_fast = self._ema(closes, 9)
            ema_slow  = self._ema(closes, 21)
            if ema_fast and ema_slow:
                if ema_fast > ema_slow * 1.002:
                    return 1
                elif ema_fast < ema_slow * 0.998:
                    return -1
        except Exception:
            pass
        return 0

    # ─────────────────────────────────────────────────────────────────────────
    # Indicator Math
    # ─────────────────────────────────────────────────────────────────────────

    def _rsi(self, closes: list[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, period + 1):
            d = closes[-period + i] - closes[-period + i - 1]
            (gains if d >= 0 else losses).append(abs(d))
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 1e-9
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _ema(self, closes: list[float], period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _volume_spike(self, volumes: list[float], lookback: int = 20) -> float:
        """Return ratio of latest volume to rolling average."""
        if len(volumes) < lookback + 1:
            return 1.0
        avg = sum(volumes[-lookback - 1:-1]) / lookback
        if avg <= 0:
            return 1.0
        return volumes[-1] / avg

    def _momentum(self, closes: list[float], bars: int = 5) -> Optional[float]:
        """Average per-bar price change over last N bars."""
        if len(closes) < bars + 1:
            return None
        changes = [(closes[-i] - closes[-i - 1]) / closes[-i - 1]
                   for i in range(1, bars + 1)]
        return sum(changes) / len(changes)
