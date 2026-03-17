"""
Technical Analysis Layer — bridges Birdeye OHLCV data with indicators.py.
Handles candle fetching, adaptive indicator selection, and score computation.
"""
from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

import config
import indicators as ind

if TYPE_CHECKING:
    from birdeye import BirdeyeClient

logger = logging.getLogger(__name__)


# ─── Data conversion helpers ──────────────────────────────────────────────────

def candles_to_df(candles: list) -> pd.DataFrame:
    """Convert list of BirdeyeOHLCV objects to a OHLCV DataFrame."""
    if not candles or len(candles) < config.TA_MIN_CANDLES:
        return pd.DataFrame()
    rows = [{
        "timestamp": c.timestamp,
        "open":   c.open,
        "high":   c.high,
        "low":    c.low,
        "close":  c.close,
        "volume": c.volume,
    } for c in candles]
    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp").drop_duplicates("timestamp")
    df.index = pd.to_datetime(df["timestamp"], unit="s")
    df = df.drop(columns=["timestamp"])
    return df


def ticks_to_df(ticks: list[tuple], resample: str = "15s") -> pd.DataFrame:
    """
    Convert (timestamp, price, volume) tuples from the fast monitor into
    an OHLCV DataFrame by resampling into fixed-length candles.
    """
    if len(ticks) < 5:
        return pd.DataFrame()
    df = pd.DataFrame(ticks, columns=["timestamp", "close", "volume"])
    df.index = pd.to_datetime(df["timestamp"], unit="s")
    df = df.drop(columns=["timestamp"])
    # Resample into candles
    ohlcv = df["close"].resample(resample).ohlc()
    ohlcv["volume"] = df["volume"].resample(resample).sum()
    ohlcv = ohlcv.dropna()
    return ohlcv


# ─── Adaptive signal computation ─────────────────────────────────────────────

def compute_ta_signals(df: pd.DataFrame) -> dict[str, float]:
    """
    Compute all applicable indicators based on available candle history.
    Returns a dict of {indicator_name: signal} where each value is in [-1, +1].
    Also includes a 'composite' key with the memecoin-tuned weighted sum,
    and a 'confidence' key (0-1) based on data availability.
    """
    if df.empty:
        return {"composite": 0.0, "confidence": 0.0}

    n = len(df)
    close  = df["close"]
    volume = df.get("volume", pd.Series(dtype=float, index=df.index))
    high   = df.get("high",   close)
    low    = df.get("low",    close)

    signals: dict[str, float] = {}
    confidence = min(n / 50.0, 1.0)

    # Always available (≥10 candles)
    if n >= 10:
        signals["momentum"]  = ind.momentum_signal(close)
        signals["roc_accel"] = ind.roc_acceleration_signal(close)
        if not volume.empty and volume.sum() > 0:
            signals["vwap"] = ind.vwap_signal(high, low, close, volume)

    # Available with ≥20 candles
    if n >= 20:
        signals["rsi"]      = ind.rsi_signal(close)
        signals["stoch_rsi"] = ind.stoch_rsi_signal(close)
        if not volume.empty and volume.sum() > 0:
            signals["obv"]    = ind.obv_signal(close, volume)
            signals["volume"] = ind.volume_signal(close, volume)
        signals["supertrend"] = ind.supertrend_signal(high, low, close)

    # Available with ≥30 candles
    if n >= 30:
        signals["adx"] = ind.adx_signal(high, low, close)

    # Compute weighted composite using memecoin weights
    weights = config.MEMECOIN_TA_WEIGHTS
    total_w = sum(weights.get(k, 0.0) for k in signals)
    if total_w > 0:
        composite = sum(signals[k] * weights.get(k, 0.0) for k in signals) / total_w
    else:
        composite = 0.0

    signals["composite"] = float(np.clip(composite, -1, 1))
    signals["confidence"] = confidence
    return signals


def compute_ta_score(df: pd.DataFrame) -> tuple[float, float, list[str]]:
    """
    Returns (ta_score, confidence, signal_labels).
    ta_score: weighted composite in [-1, +1].
    confidence: 0.0-1.0.
    signal_labels: human-readable signal descriptions.
    """
    sigs = compute_ta_signals(df)
    ta_score   = sigs.get("composite", 0.0)
    confidence = sigs.get("confidence", 0.0)

    labels = []
    if sigs.get("rsi", 0.0) > 0.5:
        close = df["close"]
        r = ind.rsi(close)
        rsi_val = r.dropna().iloc[-1] if not r.dropna().empty else 50
        labels.append(f"RSI oversold ({rsi_val:.0f})")
    elif sigs.get("rsi", 0.0) < -0.5:
        close = df["close"]
        r = ind.rsi(close)
        rsi_val = r.dropna().iloc[-1] if not r.dropna().empty else 50
        labels.append(f"RSI overbought ({rsi_val:.0f})")

    if sigs.get("supertrend", 0.0) > 0:
        labels.append("Supertrend bullish")
    elif sigs.get("supertrend", 0.0) < 0:
        labels.append("Supertrend bearish")

    if sigs.get("vwap", 0.0) > 0.3:
        labels.append("Price above VWAP")
    elif sigs.get("vwap", 0.0) < -0.3:
        labels.append("Price below VWAP")

    if sigs.get("obv", 0.0) > 0.4:
        labels.append("OBV accumulation")
    elif sigs.get("obv", 0.0) < -0.4:
        labels.append("OBV distribution")

    if sigs.get("adx", 0.0) > 0.4:
        labels.append("ADX strong uptrend")
    elif sigs.get("adx", 0.0) < -0.4:
        labels.append("ADX strong downtrend")

    return ta_score, confidence, labels


# ─── Cached OHLCV fetch ───────────────────────────────────────────────────────

def fetch_ta_signals(mint_address: str, birdeye: "BirdeyeClient") -> tuple[float, float, list[str]]:
    """
    Fetch OHLCV candles for mint_address and compute TA signals.
    Returns (ta_score, confidence, signal_labels).
    Falls through to (0.0, 0.0, []) on any failure.
    """
    try:
        candles = birdeye.get_ohlcv(
            mint_address,
            interval=config.TA_OHLCV_INTERVAL,
            limit=config.TA_OHLCV_LIMIT,
        )
        if not candles:
            return 0.0, 0.0, []

        # Check staleness: if last candle is >10 minutes old, halve confidence
        age_seconds = time.time() - candles[-1].timestamp
        stale_penalty = 0.5 if age_seconds > 600 else 1.0

        df = candles_to_df(candles)
        if df.empty:
            return 0.0, 0.0, []

        ta_score, confidence, labels = compute_ta_score(df)
        return ta_score, confidence * stale_penalty, labels
    except Exception as e:
        logger.debug("TA fetch failed for %s: %s", mint_address, e)
        return 0.0, 0.0, []
