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

    diff     = f.iloc[-1] - s.iloc[-1]
    diff_prev = f.iloc[-2] - s.iloc[-2]
    spread   = abs(diff) / s.iloc[-1] if (s.iloc[-1] != 0 and not pd.isna(s.iloc[-1])) else 0

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

# ─── Supertrend ───────────────────────────────────────────────────────────────

def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               period: int = config.SUPERTREND_PERIOD,
               multiplier: float = config.SUPERTREND_MULTIPLIER) -> pd.Series:
    """
    Returns a Series of +1 (uptrend) or -1 (downtrend) values.
    Uses ATR-based upper/lower bands; direction flips on price crossover.
    """
    a = atr(high, low, close, period)
    hl_mid = (high + low) / 2
    upper = hl_mid + multiplier * a
    lower = hl_mid - multiplier * a

    trend = pd.Series(index=close.index, dtype=float)
    final_upper = upper.copy()
    final_lower = lower.copy()

    for i in range(1, len(close)):
        # Upper band: only tighten when in downtrend
        final_upper.iloc[i] = (
            upper.iloc[i] if upper.iloc[i] < final_upper.iloc[i - 1]
            or close.iloc[i - 1] > final_upper.iloc[i - 1]
            else final_upper.iloc[i - 1]
        )
        # Lower band: only widen when in uptrend
        final_lower.iloc[i] = (
            lower.iloc[i] if lower.iloc[i] > final_lower.iloc[i - 1]
            or close.iloc[i - 1] < final_lower.iloc[i - 1]
            else final_lower.iloc[i - 1]
        )

        prev_trend = trend.iloc[i - 1] if i > 1 and not pd.isna(trend.iloc[i - 1]) else 1.0
        if prev_trend == -1.0 and close.iloc[i] > final_upper.iloc[i]:
            trend.iloc[i] = 1.0   # Crossed above upper → flip to uptrend
        elif prev_trend == 1.0 and close.iloc[i] < final_lower.iloc[i]:
            trend.iloc[i] = -1.0  # Crossed below lower → flip to downtrend
        else:
            trend.iloc[i] = prev_trend

    return trend


def supertrend_signal(high: pd.Series, low: pd.Series, close: pd.Series) -> float:
    """
    Returns [-1, +1]. Adds a recency bonus when the trend just flipped.
    """
    min_len = config.SUPERTREND_PERIOD + 5
    if not _validate(close, min_len):
        return 0.0
    st = supertrend(high, low, close)
    valid = st.dropna()
    if len(valid) < 2:
        return 0.0
    current = valid.iloc[-1]
    prev = valid.iloc[-2]
    base = float(current)
    # Recency bonus: +0.3 magnitude boost on a fresh crossover
    if current != prev:
        base = float(np.clip(current * 1.3, -1, 1))
    return float(np.clip(base, -1, 1))


# ─── Stochastic RSI ───────────────────────────────────────────────────────────

def stoch_rsi(close: pd.Series,
              rsi_period: int = config.STOCH_RSI_PERIOD,
              stoch_period: int = config.STOCH_RSI_PERIOD,
              k_smooth: int = config.STOCH_K_SMOOTH,
              d_smooth: int = config.STOCH_D_SMOOTH) -> tuple[pd.Series, pd.Series]:
    """
    Returns (%K, %D) Stochastic RSI series (0-100 range).
    Applies the stochastic formula to RSI values.
    """
    r = rsi(close, rsi_period)
    rsi_low  = r.rolling(stoch_period).min()
    rsi_high = r.rolling(stoch_period).max()
    rsi_range = rsi_high - rsi_low
    k_raw = (r - rsi_low) / rsi_range.replace(0, np.nan) * 100
    k = k_raw.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def stoch_rsi_signal(close: pd.Series) -> float:
    """
    Returns [-1, +1] based on %K/%D crossover and overbought/oversold zones (80/20).
    """
    min_len = config.STOCH_RSI_PERIOD * 2 + config.STOCH_K_SMOOTH + 5
    if not _validate(close, min_len):
        return 0.0
    k, d = stoch_rsi(close)
    k_valid = k.dropna()
    d_valid = d.dropna()
    if len(k_valid) < 2 or len(d_valid) < 2:
        return 0.0
    kv  = k_valid.iloc[-1]
    kp  = k_valid.iloc[-2]
    dv  = d_valid.iloc[-1]

    score = 0.0
    # Oversold / overbought zone
    if kv < 20:
        score += 0.5
    elif kv > 80:
        score -= 0.5

    # %K/%D crossover
    if kv > dv and kp <= d_valid.iloc[-2] if len(d_valid) > 1 else kp <= dv:
        score += 0.5   # bullish crossover
    elif kv < dv and kp >= d_valid.iloc[-2] if len(d_valid) > 1 else kp >= dv:
        score -= 0.5   # bearish crossover

    return float(np.clip(score, -1, 1))


# ─── VWAP ─────────────────────────────────────────────────────────────────────

def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series) -> pd.Series:
    """Volume-Weighted Average Price (cumulative, intraday-style from start of window)."""
    typical = (high + low + close) / 3
    cum_vol = volume.cumsum()
    cum_tp_vol = (typical * volume).cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def vwap_signal(high: pd.Series, low: pd.Series, close: pd.Series,
                volume: pd.Series) -> float:
    """
    Returns [-1, +1] based on price position relative to VWAP and distance magnitude.
    """
    if not _validate(close, 5) or not _validate(volume, 5) or volume.sum() == 0:
        return 0.0
    vw = vwap(high, low, close, volume)
    latest_vwap = vw.dropna().iloc[-1] if not vw.dropna().empty else None
    if latest_vwap is None or latest_vwap == 0 or pd.isna(latest_vwap):
        return 0.0
    price = close.iloc[-1]
    deviation = (price - latest_vwap) / latest_vwap
    # Positive deviation = price above VWAP = bullish; scale and clip
    return float(np.clip(deviation * 10, -1, 1))


# ─── ADX ──────────────────────────────────────────────────────────────────────

def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (ADX, +DI, -DI).
    ADX > 25 indicates a trending market.
    """
    up_move   = high.diff()
    down_move = -(low.diff())
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm_s  = pd.Series(plus_dm,  index=high.index).ewm(com=period - 1, adjust=False).mean()
    minus_dm_s = pd.Series(minus_dm, index=high.index).ewm(com=period - 1, adjust=False).mean()
    atr_s = atr(high, low, close, period)
    plus_di  = 100 * plus_dm_s  / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm_s / atr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_s = dx.ewm(com=period - 1, adjust=False).mean()
    return adx_s, plus_di, minus_di


def adx_signal(high: pd.Series, low: pd.Series, close: pd.Series) -> float:
    """
    Returns [-1, +1].  Magnitude scales with ADX value (>25 = trending).
    Below ADX 20, returns 0.0 (no clear trend — indicator abstains).
    """
    if not _validate(close, 30):
        return 0.0
    adx_s, plus_di, minus_di = adx(high, low, close)
    adx_val  = adx_s.dropna().iloc[-1]  if not adx_s.dropna().empty  else 0.0
    pdi_val  = plus_di.dropna().iloc[-1]  if not plus_di.dropna().empty  else 0.0
    mdi_val  = minus_di.dropna().iloc[-1] if not minus_di.dropna().empty else 0.0
    if pd.isna(adx_val) or adx_val < 20:
        return 0.0
    direction = np.sign(pdi_val - mdi_val)
    strength  = min(adx_val / 50, 1.0)   # 50+ ADX = full conviction
    return float(np.clip(direction * strength, -1, 1))


# ─── OBV ──────────────────────────────────────────────────────────────────────

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: cumulative volume with sign from price direction."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def obv_signal(close: pd.Series, volume: pd.Series, period: int = 20) -> float:
    """
    Returns [-1, +1] based on OBV vs price divergence.
    OBV rising while price flat = accumulation (+).
    OBV falling while price flat = distribution (-).
    """
    if not _validate(close, period + 5) or not _validate(volume, period + 5):
        return 0.0
    obv_s = obv(close, volume)
    if len(obv_s) < period:
        return 0.0
    obv_roc  = (obv_s.iloc[-1] - obv_s.iloc[-period]) / (abs(obv_s.iloc[-period]) + 1e-10)
    price_roc = (close.iloc[-1] - close.iloc[-period]) / (close.iloc[-period] + 1e-10)
    divergence = obv_roc - price_roc
    return float(np.clip(divergence * 5, -1, 1))


# ─── ROC Acceleration ─────────────────────────────────────────────────────────

def roc_acceleration_signal(close: pd.Series, period: int = config.MOMENTUM_PERIOD) -> float:
    """
    Returns [-1, +1] based on whether momentum (ROC) is accelerating or decelerating.
    Positive = momentum picking up speed (good for entry); Negative = momentum fading.
    """
    if not _validate(close, period * 2 + 5):
        return 0.0
    roc_series = close.pct_change(periods=period)
    if len(roc_series.dropna()) < 3:
        return 0.0
    r = roc_series.dropna()
    current = r.iloc[-1]
    prev    = r.iloc[-2]
    prev2   = r.iloc[-3]
    acceleration = current - prev
    prev_accel   = prev - prev2
    score = 0.0
    score += 0.5 * np.sign(current)           # Momentum direction
    score += 0.3 * np.sign(acceleration)      # Acceleration direction
    if np.sign(acceleration) == np.sign(prev_accel):
        score += 0.2 * np.sign(acceleration)  # Consistent acceleration
    return float(np.clip(score, -1, 1))


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
