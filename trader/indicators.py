"""
Technical Indicators — pure NumPy/Pandas implementations.
No external TA library required.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional

import config


def _validate(series: pd.Series, min_len: int) -> bool:
    return series is not None and len(series) >= min_len and not series.isna().all()


# ─── Moving Averages ──────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


# ─── RSI ──────────────────────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = config.RSI_PERIOD) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Pure uptrend (avg_loss = 0, avg_gain > 0) → RSI should be 100, not NaN
    rsi = rsi.where(avg_loss != 0, other=100.0)
    return rsi


def rsi_signal(close: pd.Series) -> float:
    """
    Returns signal in [-1, +1].
    +1 = strong buy (deeply oversold), -1 = strong sell (deeply overbought).
    """
    if not _validate(close, config.RSI_PERIOD + 5):
        return 0.0
    r = rsi(close)
    latest = r.iloc[-1]
    if pd.isna(latest):
        return 0.0
    # Linear mapping: 20→+1, 35→+0.5, 50→0, 65→-0.5, 80→-1
    if latest <= 20:
        return 1.0
    elif latest <= config.RSI_OVERSOLD:
        return 0.5 + 0.5 * (config.RSI_OVERSOLD - latest) / (config.RSI_OVERSOLD - 20)
    elif latest <= 50:
        return 0.5 * (50 - latest) / (50 - config.RSI_OVERSOLD)
    elif latest < config.RSI_OVERBOUGHT:
        return -0.5 * (latest - 50) / (config.RSI_OVERBOUGHT - 50)
    elif latest < 80:
        return -0.5 - 0.5 * (latest - config.RSI_OVERBOUGHT) / (80 - config.RSI_OVERBOUGHT)
    else:
        return -1.0


# ─── MACD ─────────────────────────────────────────────────────────────────────

def macd(close: pd.Series,
         fast: int = config.MACD_FAST,
         slow: int = config.MACD_SLOW,
         signal_p: int = config.MACD_SIGNAL):
    """Returns (macd_line, signal_line, histogram)."""
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal_p)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def macd_signal(close: pd.Series) -> float:
    """
    Returns [-1, +1] based on MACD histogram direction and crossover.
    Combines: histogram sign, recent crossover, histogram acceleration.
    """
    if not _validate(close, config.MACD_SLOW + config.MACD_SIGNAL + 5):
        return 0.0
    _, sig, hist = macd(close)
    if len(hist.dropna()) < 3:
        return 0.0

    h = hist.dropna()
    current = h.iloc[-1]
    prev    = h.iloc[-2]
    prev2   = h.iloc[-3]

    score = 0.0
    # Histogram sign: 0.4
    score += 0.4 * np.sign(current)
    # Acceleration (trend strengthening): 0.3
    if (current > prev > prev2) or (current < prev < prev2):
        score += 0.3 * np.sign(current)
    # Recent crossover (zero-line cross in last 2 bars): 0.3
    if np.sign(current) != np.sign(prev):
        score += 0.3 * np.sign(current)

    return float(np.clip(score, -1, 1))


# ─── Bollinger Bands ──────────────────────────────────────────────────────────

def bollinger(close: pd.Series,
              period: int = config.BOLLINGER_PERIOD,
              std_dev: float = config.BOLLINGER_STD):
    """Returns (upper, middle, lower)."""
    mid   = sma(close, period)
    sigma = close.rolling(period).std()
    return mid + std_dev * sigma, mid, mid - std_dev * sigma


def bollinger_signal(close: pd.Series) -> float:
    """
    Returns [-1, +1].
    Buy near lower band (mean-reversion), sell near upper band.
    Also catches breakouts: price crossing band = continuation signal.
    """
    if not _validate(close, config.BOLLINGER_PERIOD + 5):
        return 0.0
    upper, mid, lower = bollinger(close)
    if upper.isna().all() or lower.isna().all():
        return 0.0

    p  = close.iloc[-1]
    p1 = close.iloc[-2]
    u  = upper.iloc[-1]
    m  = mid.iloc[-1]
    l  = lower.iloc[-1]
    u1 = upper.iloc[-2]
    l1 = lower.iloc[-2]

    band_width = u - l
    if band_width == 0 or pd.isna(band_width):
        return 0.0

    # Position within bands: -1 at lower, 0 at mid, +1 at upper
    position = (p - m) / (band_width / 2)

    # Mean-reversion component
    mr_score = -position * 0.5

    # Breakout component
    breakout = 0.0
    if p1 <= l1 and p > l:   # Price bounced off lower band
        breakout = 0.5
    elif p1 >= u1 and p < u:  # Price bounced off upper band
        breakout = -0.5
    elif p > u and p1 <= u:   # Breakout above upper band (continuation)
        breakout = 0.3
    elif p < l and p1 >= l:   # Breakdown below lower band (continuation)
        breakout = -0.3

    return float(np.clip(mr_score + breakout, -1, 1))


# ─── EMA Crossover ────────────────────────────────────────────────────────────

def ema_cross_signal(close: pd.Series,
                     fast: int = config.EMA_FAST,
                     slow: int = config.EMA_SLOW) -> float:
    """
    Golden cross / death cross signal.
    Returns [-1, +1] based on fast/slow EMA relationship and recent cross.
    """
    if not _validate(close, slow + 5):
        return 0.0
    f = ema(close, fast)
    s = ema(close, slow)

    f_last, f_prev = float(f.iloc[-1]), float(f.iloc[-2])
    s_last, s_prev = float(s.iloc[-1]), float(s.iloc[-2])
    if any(np.isnan(v) for v in (f_last, f_prev, s_last, s_prev)):
        return 0.0

    diff      = f_last - s_last
    diff_prev = f_prev - s_prev
    spread   = abs(diff) / s_last if s_last != 0 else 0

    score = 0.0
    # Direction: 0.5
    score += 0.5 * np.sign(diff)
    # Recent crossover (last 3 bars): 0.3
    if np.sign(diff) != np.sign(diff_prev):
        score += 0.3 * np.sign(diff)
    # Spread magnitude (strength): 0.2
    score += 0.2 * np.sign(diff) * min(spread * 100, 1.0)

    return float(np.clip(score, -1, 1))


# ─── Momentum ─────────────────────────────────────────────────────────────────

def momentum_signal(close: pd.Series, period: int = config.MOMENTUM_PERIOD) -> float:
    """
    Rate-of-change momentum signal.
    Combines ROC, price position vs recent high/low, and trend consistency.
    """
    if not _validate(close, period + 5):
        return 0.0

    roc = close.pct_change(periods=period).iloc[-1]  # rate-of-change over exactly `period` bars
    recent = close.tail(period)
    rng = recent.max() - recent.min()
    pos = (close.iloc[-1] - recent.min()) / rng if rng > 0 else 0.5

    # Trend consistency (fraction of up-days)
    returns = close.pct_change().tail(period)
    up_frac = (returns > 0).sum() / period

    score = (
        0.4 * np.clip(roc * 10, -1, 1) +  # ROC scaled
        0.3 * (pos * 2 - 1) +             # Position in range: -1 to +1
        0.3 * (up_frac * 2 - 1)           # Trend consistency: -1 to +1
    )
    return float(np.clip(score, -1, 1))


# ─── Volume Analysis ──────────────────────────────────────────────────────────

def volume_signal(close: pd.Series, volume: pd.Series, period: int = 20) -> float:
    """
    Volume-price confirmation signal.
    High volume on up-moves = bullish. High volume on down-moves = bearish.
    """
    if not _validate(volume, period + 2) or volume.sum() == 0:
        return 0.0

    avg_vol   = volume.rolling(period).mean()
    latest_vol = volume.iloc[-1]
    avg        = avg_vol.iloc[-1]
    if pd.isna(avg) or avg == 0:
        return 0.0

    vol_ratio   = min(latest_vol / avg, 3.0) / 3.0  # Normalized 0-1
    price_change = close.pct_change().iloc[-1]

    score = vol_ratio * np.sign(price_change) * min(abs(price_change) * 20, 1.0)
    return float(np.clip(score, -1, 1))


# ─── Support / Resistance ────────────────────────────────────────────────────

def find_sr_levels(close: pd.Series, n: int = 5) -> tuple[list, list]:
    """
    Identify key support and resistance levels using local extrema.
    Returns (support_levels, resistance_levels).
    """
    if len(close) < 20:
        return [], []

    support = []
    resistance = []
    data = close.values

    for i in range(2, len(data) - 2):
        if data[i] < data[i-1] and data[i] < data[i-2] and data[i] < data[i+1] and data[i] < data[i+2]:
            support.append(data[i])
        if data[i] > data[i-1] and data[i] > data[i-2] and data[i] > data[i+1] and data[i] > data[i+2]:
            resistance.append(data[i])

    # Return the N nearest levels to current price
    current = data[-1]
    support    = sorted(support, key=lambda x: abs(x - current))[:n]
    resistance = sorted(resistance, key=lambda x: abs(x - current))[:n]
    return support, resistance


# ─── ATR (Average True Range) ─────────────────────────────────────────────────

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range for volatility measurement."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def atr_stop(close: pd.Series, high: pd.Series, low: pd.Series,
             multiplier: float = 2.0) -> tuple[float, float]:
    """
    ATR-based stop loss and take profit levels.
    Returns (stop_price, target_price) for a long position.
    """
    a = atr(high, low, close)
    latest_atr   = a.iloc[-1]
    current_price = close.iloc[-1]
    stop   = current_price - multiplier * latest_atr
    target = current_price + multiplier * 2 * latest_atr  # 2:1 R/R
    return stop, target


# ─── Composite Score ──────────────────────────────────────────────────────────

def compute_composite_score(df: pd.DataFrame, weights: dict = None) -> float:
    """
    Compute the ensemble signal score for a given OHLCV DataFrame.
    Returns a value in [-1, +1].
    Positive = bullish, Negative = bearish, magnitude = conviction.
    """
    if df.empty or len(df) < 30:
        return 0.0

    close  = df["close"]
    volume = df.get("volume", pd.Series(dtype=float))
    high   = df.get("high", close)
    low    = df.get("low", close)

    w = weights or {
        "rsi":       0.20,
        "macd":      0.20,
        "bollinger": 0.15,
        "ema_cross": 0.20,
        "momentum":  0.15,
        "volume":    0.10,
    }

    signals = {
        "rsi":       rsi_signal(close),
        "macd":      macd_signal(close),
        "bollinger": bollinger_signal(close),
        "ema_cross": ema_cross_signal(close),
        "momentum":  momentum_signal(close),
        "volume":    volume_signal(close, volume) if not volume.empty else 0.0,
    }

    total_weight = sum(w.get(k, 0) for k in signals)
    if total_weight == 0:
        return 0.0

    score = sum(signals[k] * w.get(k, 0) for k in signals) / total_weight
    return float(np.clip(score, -1, 1))
