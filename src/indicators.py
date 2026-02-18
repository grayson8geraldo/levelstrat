"""
Technical Indicators — computes all indicators needed by DLS strategy.
Uses pandas_ta for calculations.
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
import logging

from src.config import (
    EMA_FAST, EMA_MEDIUM, EMA_SLOW, EMA_GLOBAL,
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    ATR_PERIOD, BB_PERIOD, BB_STD, VOLUME_MA_PERIOD,
)

logger = logging.getLogger(__name__)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame | None:
    """
    Compute all DLS indicators on an OHLCV DataFrame.

    Input columns: open, high, low, close, volume
    Returns DataFrame with all original + indicator columns.
    """
    if df is None or len(df) < EMA_GLOBAL:
        return None

    try:
        out = df.copy()

        # ── EMAs ───────────────────────────────────────────
        out[f"EMA_{EMA_FAST}"] = ta.ema(out["close"], length=EMA_FAST)
        out[f"EMA_{EMA_MEDIUM}"] = ta.ema(out["close"], length=EMA_MEDIUM)
        out[f"EMA_{EMA_SLOW}"] = ta.ema(out["close"], length=EMA_SLOW)
        out[f"EMA_{EMA_GLOBAL}"] = ta.ema(out["close"], length=EMA_GLOBAL)

        # ── RSI ────────────────────────────────────────────
        out[f"RSI_{RSI_PERIOD}"] = ta.rsi(out["close"], length=RSI_PERIOD)

        # ── MACD ───────────────────────────────────────────
        macd = ta.macd(
            out["close"], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL
        )
        if macd is not None:
            out = pd.concat([out, macd], axis=1)

        # ── ATR ────────────────────────────────────────────
        out[f"ATR_{ATR_PERIOD}"] = ta.atr(
            out["high"], out["low"], out["close"], length=ATR_PERIOD
        )

        # ── Bollinger Bands ────────────────────────────────
        bb = ta.bbands(out["close"], length=BB_PERIOD, std=BB_STD)
        if bb is not None:
            out = pd.concat([out, bb], axis=1)

        # ── OBV ────────────────────────────────────────────
        out["OBV"] = ta.obv(out["close"], out["volume"])

        # ── Volume MA & ratio ──────────────────────────────
        out["volume_ma"] = ta.sma(out["volume"], length=VOLUME_MA_PERIOD)
        out["volume_ratio"] = out["volume"] / out["volume_ma"]

        return out

    except Exception as e:
        logger.error(f"Error computing indicators: {e}")
        return None


def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 30) -> str | None:
    """
    Detect RSI divergence on the last `lookback` candles.

    Returns:
      "bullish"  — price lower low, RSI higher low
      "bearish"  — price higher high, RSI lower high
      None       — no divergence
    """
    rsi_col = f"RSI_{RSI_PERIOD}"
    if rsi_col not in df.columns or len(df) < lookback:
        return None

    window = df.iloc[-lookback:]
    prices = window["close"].values
    rsis = window[rsi_col].values

    if np.any(np.isnan(rsis)):
        return None

    # Find last two swing lows (for bullish)
    price_lows = []
    rsi_lows = []
    for i in range(2, len(prices) - 2):
        if prices[i] < prices[i - 1] and prices[i] < prices[i - 2] and \
           prices[i] < prices[i + 1] and prices[i] < prices[i + 2]:
            price_lows.append((i, prices[i], rsis[i]))

    if len(price_lows) >= 2:
        prev = price_lows[-2]
        curr = price_lows[-1]
        if curr[1] < prev[1] and curr[2] > prev[2]:
            return "bullish"

    # Find last two swing highs (for bearish)
    price_highs = []
    for i in range(2, len(prices) - 2):
        if prices[i] > prices[i - 1] and prices[i] > prices[i - 2] and \
           prices[i] > prices[i + 1] and prices[i] > prices[i + 2]:
            price_highs.append((i, prices[i], rsis[i]))

    if len(price_highs) >= 2:
        prev = price_highs[-2]
        curr = price_highs[-1]
        if curr[1] > prev[1] and curr[2] < prev[2]:
            return "bearish"

    return None


def get_trend_direction(df: pd.DataFrame) -> str:
    """
    Determine trend direction from EMA alignment.

    Returns: "bullish", "bearish", or "neutral"
    """
    if df is None or len(df) < 2:
        return "neutral"

    last = df.iloc[-1]
    ema_f = last.get(f"EMA_{EMA_FAST}")
    ema_m = last.get(f"EMA_{EMA_MEDIUM}")
    ema_s = last.get(f"EMA_{EMA_SLOW}")

    if any(v is None or pd.isna(v) for v in [ema_f, ema_m, ema_s]):
        return "neutral"

    if ema_f > ema_m > ema_s:
        return "bullish"
    elif ema_f < ema_m < ema_s:
        return "bearish"
    return "neutral"
