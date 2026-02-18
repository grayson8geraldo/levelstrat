"""
Candle Pattern Detector — identifies reversal patterns at diagonal levels.
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)


def detect_patterns(df: pd.DataFrame, lookback: int = 3) -> list[dict]:
    """
    Detect candle patterns in the last `lookback` candles.

    Returns list of dicts: {"pattern": str, "direction": "bullish"|"bearish", "strength": 1-5}
    """
    if df is None or len(df) < lookback + 1:
        return []

    patterns = []
    tail = df.iloc[-(lookback + 1):]

    curr = tail.iloc[-1]
    prev = tail.iloc[-2]

    c_open, c_close = curr["open"], curr["close"]
    c_high, c_low = curr["high"], curr["low"]
    c_body = abs(c_close - c_open)
    c_range = c_high - c_low

    p_open, p_close = prev["open"], prev["close"]
    p_body = abs(p_close - p_open)

    if c_range == 0:
        return patterns

    # ── Bullish Engulfing ──────────────────────────────────
    if (
        p_close < p_open  # prev is bearish
        and c_close > c_open  # curr is bullish
        and c_close > p_open  # curr close > prev open
        and c_open < p_close  # curr open < prev close
    ):
        patterns.append({
            "pattern": "bullish_engulfing",
            "direction": "bullish",
            "strength": 5,
        })

    # ── Bearish Engulfing ──────────────────────────────────
    if (
        p_close > p_open  # prev is bullish
        and c_close < c_open  # curr is bearish
        and c_close < p_open  # curr close < prev open
        and c_open > p_close  # curr open > prev close
    ):
        patterns.append({
            "pattern": "bearish_engulfing",
            "direction": "bearish",
            "strength": 5,
        })

    # ── Hammer (Pin Bar) ──────────────────────────────────
    lower_shadow = min(c_open, c_close) - c_low
    upper_shadow = c_high - max(c_open, c_close)

    if c_body > 0 and lower_shadow >= 2 * c_body and upper_shadow < c_body * 0.5:
        patterns.append({
            "pattern": "hammer",
            "direction": "bullish",
            "strength": 5,
        })

    # ── Shooting Star ──────────────────────────────────────
    if c_body > 0 and upper_shadow >= 2 * c_body and lower_shadow < c_body * 0.5:
        patterns.append({
            "pattern": "shooting_star",
            "direction": "bearish",
            "strength": 5,
        })

    # ── Bullish Harami ─────────────────────────────────────
    if (
        p_close < p_open  # prev bearish
        and c_close > c_open  # curr bullish
        and c_close < p_open  # curr fits inside prev
        and c_open > p_close
        and c_body < p_body * 0.6
    ):
        patterns.append({
            "pattern": "bullish_harami",
            "direction": "bullish",
            "strength": 3,
        })

    # ── Bearish Harami ─────────────────────────────────────
    if (
        p_close > p_open  # prev bullish
        and c_close < c_open  # curr bearish
        and c_close > p_open  # curr fits inside prev
        and c_open < p_close
        and c_body < p_body * 0.6
    ):
        patterns.append({
            "pattern": "bearish_harami",
            "direction": "bearish",
            "strength": 3,
        })

    # ── Doji ───────────────────────────────────────────────
    if c_range > 0 and c_body / c_range < 0.1:
        patterns.append({
            "pattern": "doji",
            "direction": "neutral",
            "strength": 2,
        })

    # ── Morning Star (3-candle) ────────────────────────────
    if len(tail) >= 3:
        c3 = tail.iloc[-3]
        c3_body = abs(c3["close"] - c3["open"])

        if (
            c3["close"] < c3["open"]  # first: bearish
            and p_body < c3_body * 0.3  # second: small body (star)
            and c_close > c_open  # third: bullish
            and c_close > (c3["open"] + c3["close"]) / 2  # closes above mid of first
        ):
            patterns.append({
                "pattern": "morning_star",
                "direction": "bullish",
                "strength": 4,
            })

    # ── Evening Star (3-candle) ────────────────────────────
    if len(tail) >= 3:
        c3 = tail.iloc[-3]
        c3_body = abs(c3["close"] - c3["open"])

        if (
            c3["close"] > c3["open"]  # first: bullish
            and p_body < c3_body * 0.3  # second: small body
            and c_close < c_open  # third: bearish
            and c_close < (c3["open"] + c3["close"]) / 2  # closes below mid of first
        ):
            patterns.append({
                "pattern": "evening_star",
                "direction": "bearish",
                "strength": 4,
            })

    return patterns


def get_best_pattern(patterns: list[dict], direction: str) -> dict | None:
    """Get the strongest pattern matching the desired direction."""
    matching = [p for p in patterns if p["direction"] == direction]
    if not matching:
        return None
    return max(matching, key=lambda p: p["strength"])
