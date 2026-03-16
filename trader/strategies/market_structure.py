"""
Market Structure with Inducements & Sweeps
==========================================
Python port of the LuxAlgo "Market Structure with Inducements & Sweeps"
TradingView indicator (© LuxAlgo, CC BY-NC-SA 4.0).

Concepts
--------
CHoCH  – Change of Character: price breaks a major swing level, signalling
         a potential trend reversal.
BOS    – Break of Structure: after an Inducement is triggered, price breaks
         the trailing high/low, confirming trend continuation.
IDM    – Inducement: a short-term swing that gets swept before the real move
         (liquidity grab that precedes BOS).
Sweep  – A wick beyond a structural level that closes back inside (failed
         breakout / liquidity grab at extremes).

Signal output  [-1 .. +1]
  +1.0   BOS bullish  (strongest: IDM confirmed → break above trailing max)
  +0.6   CHoCH bullish (trend reversal signal)
  +0.3   Bullish IDM pending (BOS not yet confirmed)
  -0.3   Bearish IDM pending
  -0.6   CHoCH bearish
  -1.0   BOS bearish
  ±0.2   Sweep adjustment (caution overlay)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Defaults mirroring LuxAlgo Pine Script ────────────────────────────────────
# Pine defaults: len=50, shortLen=3. We use 30 to stay within the 100-candle
# window the bot fetches; increase if you pull more history.
_CHOCH_LEN = 30
_IDM_LEN   = 3


# ── Result dataclass ──────────────────────────────────────────────────────────
@dataclass
class MarketStructureResult:
    signal:      str   = "HOLD"
    score:       float = 0.0
    conviction:  float = 0.0
    reasons:     list  = field(default_factory=list)
    os:          int   = 0       # current structure bias: 1=bullish, 0=bearish
    choch_bull:  bool  = False
    choch_bear:  bool  = False
    bos_bull:    bool  = False
    bos_bear:    bool  = False
    idm_bull:    bool  = False   # inducement swept in bullish structure
    idm_bear:    bool  = False   # inducement swept in bearish structure
    sweep_bull:  bool  = False   # wick above trailing max, closed below
    sweep_bear:  bool  = False   # wick below trailing min, closed above
    trend_level: float = float("nan")


# ── Swing detection ───────────────────────────────────────────────────────────
def _detect_swings(high: np.ndarray, low: np.ndarray, length: int):
    """
    Equivalent to the Pine Script swings(len) function.

    A confirmed swing high at bar `pivot = i - length` is detected when:
        high[pivot] > max(high[pivot+1 .. i])
    i.e., the pivot bar is higher than all `length` bars that follow it.

    Returns
    -------
    os_arr   : int[n]   — running structure state (0 = after swing high,
                                                    1 = after swing low)
    top_arr  : float[n] — swing high price at confirmation bar (else NaN)
    topx_arr : int[n]   — bar index of the swing high pivot (else -1)
    btm_arr  : float[n] — swing low price at confirmation bar (else NaN)
    btmx_arr : int[n]   — bar index of the swing low pivot (else -1)
    """
    n        = len(high)
    os_arr   = np.zeros(n, dtype=int)
    top_arr  = np.full(n, np.nan)
    topx_arr = np.full(n, -1, dtype=int)
    btm_arr  = np.full(n, np.nan)
    btmx_arr = np.full(n, -1, dtype=int)

    os_cur = 0

    for i in range(length, n):
        pivot    = i - length
        hi_p     = high[pivot]
        lo_p     = low[pivot]
        fut_hi   = np.max(high[pivot + 1: i + 1])
        fut_lo   = np.min(low[pivot + 1:  i + 1])

        if hi_p > fut_hi:
            os_cur = 0    # pivot is a swing HIGH
        elif lo_p < fut_lo:
            os_cur = 1    # pivot is a swing LOW

        prev_os = os_arr[i - 1]

        if os_cur == 0 and prev_os != 0:
            top_arr[i]  = hi_p
            topx_arr[i] = pivot

        if os_cur == 1 and prev_os != 1:
            btm_arr[i]  = lo_p
            btmx_arr[i] = pivot

        os_arr[i] = os_cur

    return os_arr, top_arr, topx_arr, btm_arr, btmx_arr


def _ffill_nan(arr: np.ndarray) -> np.ndarray:
    """Forward-fill NaN values (Pine Script fixnan)."""
    out  = arr.copy()
    last = np.nan
    for i in range(len(out)):
        if not np.isnan(out[i]):
            last = out[i]
        else:
            out[i] = last
    return out


def _ffill_idx(arr: np.ndarray) -> np.ndarray:
    """Forward-fill integer index arrays (fill -1 sentinels)."""
    out  = arr.copy().astype(float)
    last = 0.0
    for i in range(len(out)):
        if out[i] >= 0:
            last = out[i]
        else:
            out[i] = last
    return out.astype(int)


# ── Main analysis function ────────────────────────────────────────────────────
def analyze(
    df:        pd.DataFrame,
    choch_len: int = _CHOCH_LEN,
    idm_len:   int = _IDM_LEN,
    lookback:  int = 5,
) -> MarketStructureResult:
    """
    Run Market Structure analysis on an OHLCV DataFrame.

    Parameters
    ----------
    df        : DataFrame with columns high, low, close
    choch_len : swing lookback for CHoCH detection (default 30)
    idm_len   : swing lookback for IDM/Inducement detection (default 3)
    lookback  : number of trailing bars to check for recent events

    Returns
    -------
    MarketStructureResult with signal, score, conviction, and event flags
    """
    if df is None or len(df) < choch_len + idm_len + 10:
        return MarketStructureResult()

    high  = df["high"].to_numpy(dtype=float)
    low   = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n     = len(close)

    # ── Detect swings at both timeframes ─────────────────────────────────────
    _, top_arr,  topx_arr,  btm_arr,  btmx_arr  = _detect_swings(high, low, choch_len)
    _, stop_arr, stopx_arr, sbtm_arr, sbtmx_arr = _detect_swings(high, low, idm_len)

    # Forward-fill price levels (fixnan) so every bar has a reference level
    stopy_arr = _ffill_nan(stop_arr)   # short-term swing high (trailing)
    sbtmy_arr = _ffill_nan(sbtm_arr)   # short-term swing low  (trailing)

    # ── State machine — mirrors Pine Script bar-by-bar logic ─────────────────
    os           = 0
    top_crossed  = False
    btm_crossed  = False
    topy         = np.nan
    btmy         = np.nan
    max_         = np.nan
    min_         = np.nan
    max_x1       = 0
    min_x1       = 0
    stop_crossed = False
    sbtm_crossed = False

    choch_events: list[tuple] = []
    bos_events:   list[tuple] = []
    idm_events:   list[tuple] = []
    sweep_events: list[tuple] = []

    for i in range(n):
        # ── Refresh structural swing references ───────────────────────────────
        if not np.isnan(top_arr[i]):
            topy        = top_arr[i]
            top_crossed = False
        if not np.isnan(btm_arr[i]):
            btmy        = btm_arr[i]
            btm_crossed = False

        prev_os = os

        # ── CHoCH: price crosses above/below major swing ─────────────────────
        if not np.isnan(topy) and close[i] > topy and not top_crossed:
            os          = 1           # bullish CHoCH
            top_crossed = True
        if not np.isnan(btmy) and close[i] < btmy and not btm_crossed:
            os          = 0           # bearish CHoCH
            btm_crossed = True

        # ── On structure flip: reset trailing max/min & IDM states ───────────
        if os != prev_os:
            max_         = high[i]
            min_         = low[i]
            max_x1       = i
            min_x1       = i
            stop_crossed = False
            sbtm_crossed = False
            if os == 1:
                choch_events.append((i, "bull", topy))
            else:
                choch_events.append((i, "bear", btmy))

        stopy = stopy_arr[i]
        sbtmy = sbtmy_arr[i]

        # ── Bullish IDM & BOS ─────────────────────────────────────────────────
        # IDM: in bullish structure, a short-term low is swept downward
        if (
            not np.isnan(sbtmy) and low[i] < sbtmy
            and not sbtm_crossed and os == 1 and sbtmy != btmy
        ):
            idm_events.append((i, "bull", sbtmy))
            sbtm_crossed = True

        # BOS: after IDM, price closes above the trailing high
        if (
            not np.isnan(max_) and close[i] > max_
            and sbtm_crossed and os == 1
        ):
            bos_events.append((i, "bull", max_))
            sbtm_crossed = False

        # ── Bearish IDM & BOS ─────────────────────────────────────────────────
        # IDM: in bearish structure, a short-term high is swept upward
        if (
            not np.isnan(stopy) and high[i] > stopy
            and not stop_crossed and os == 0 and stopy != topy
        ):
            idm_events.append((i, "bear", stopy))
            stop_crossed = True

        # BOS: after IDM, price closes below the trailing low
        if (
            not np.isnan(min_) and close[i] < min_
            and stop_crossed and os == 0
        ):
            bos_events.append((i, "bear", min_))
            stop_crossed = False

        # ── Sweeps (failed breakout / liquidity grab) ─────────────────────────
        # Sweep of resistance: wick above trailing max, close back below
        if (
            not np.isnan(max_) and high[i] > max_
            and close[i] < max_ and os == 1 and i - max_x1 > 1
        ):
            sweep_events.append((i, "bull_sweep", max_))

        # Sweep of support: wick below trailing min, close back above
        if (
            not np.isnan(min_) and low[i] < min_
            and close[i] > min_ and os == 0 and i - min_x1 > 1
        ):
            sweep_events.append((i, "bear_sweep", min_))

        # ── Trailing max / min ────────────────────────────────────────────────
        if np.isnan(max_):
            max_   = high[i]
            max_x1 = i
        elif high[i] > max_:
            max_   = high[i]
            max_x1 = i

        if np.isnan(min_):
            min_   = low[i]
            min_x1 = i
        elif low[i] < min_:
            min_   = low[i]
            min_x1 = i

    # ── Collect events in the trailing `lookback` bars ────────────────────────
    cutoff = n - lookback
    rc = [e for e in choch_events if e[0] >= cutoff]
    rb = [e for e in bos_events   if e[0] >= cutoff]
    ri = [e for e in idm_events   if e[0] >= cutoff]
    rs = [e for e in sweep_events if e[0] >= cutoff]

    choch_bull = any(e[1] == "bull"       for e in rc)
    choch_bear = any(e[1] == "bear"       for e in rc)
    bos_bull   = any(e[1] == "bull"       for e in rb)
    bos_bear   = any(e[1] == "bear"       for e in rb)
    idm_bull   = any(e[1] == "bull"       for e in ri)
    idm_bear   = any(e[1] == "bear"       for e in ri)
    sweep_bull = any(e[1] == "bull_sweep" for e in rs)
    sweep_bear = any(e[1] == "bear_sweep" for e in rs)

    # ── Score calculation ─────────────────────────────────────────────────────
    score   = 0.0
    reasons: list[str] = []

    # BOS is the strongest signal (structure + IDM both confirmed)
    if bos_bull:
        score += 1.0
        reasons.append("Bullish BOS — structure break confirmed after IDM")
    elif choch_bull:
        score += 0.6
        reasons.append("Bullish CHoCH — market structure shift to bullish")

    if bos_bear:
        score -= 1.0
        reasons.append("Bearish BOS — structure break confirmed after IDM")
    elif choch_bear:
        score -= 0.6
        reasons.append("Bearish CHoCH — market structure shift to bearish")

    # IDM alone (BOS not yet confirmed): mild directional bias
    if idm_bull and not bos_bull and os == 1:
        score += 0.3
        reasons.append("Bullish IDM — inducement swept, awaiting BOS")
    if idm_bear and not bos_bear and os == 0:
        score -= 0.3
        reasons.append("Bearish IDM — inducement swept, awaiting BOS")

    # Sweeps are contrarian overlays (exhaustion / liquidity grab)
    if sweep_bull and os == 1:
        score -= 0.2
        reasons.append("High sweep — liquidity grab at resistance (caution on longs)")
    if sweep_bear and os == 0:
        score += 0.2
        reasons.append("Low sweep — liquidity grab at support (caution on shorts)")

    score = float(np.clip(score, -1.0, 1.0))

    bull_ev    = int(bos_bull) + int(choch_bull) + int(idm_bull)
    bear_ev    = int(bos_bear) + int(choch_bear) + int(idm_bear)
    conviction = float(np.clip(max(bull_ev, bear_ev) / 3.0, 0.0, 1.0))

    signal = "HOLD" if abs(score) < 0.25 else ("BUY" if score > 0 else "SELL")

    return MarketStructureResult(
        signal      = signal,
        score       = score,
        conviction  = conviction,
        reasons     = reasons,
        os          = os,
        choch_bull  = choch_bull,
        choch_bear  = choch_bear,
        bos_bull    = bos_bull,
        bos_bear    = bos_bear,
        idm_bull    = idm_bull,
        idm_bear    = idm_bear,
        sweep_bull  = sweep_bull,
        sweep_bear  = sweep_bear,
        trend_level = max_ if os == 1 else min_,
    )


def market_structure_signal(
    df:        pd.DataFrame,
    choch_len: int = _CHOCH_LEN,
    idm_len:   int = _IDM_LEN,
) -> float:
    """
    Convenience wrapper that returns a single [-1, +1] score for use in
    the ensemble engine.
    """
    return analyze(df, choch_len=choch_len, idm_len=idm_len).score
