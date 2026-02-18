"""
Diagonal Level Detector — the core of DLS strategy.

Finds valid trendlines (naklonnye urovni) with:
  - 3+ touches
  - Built from swing extremums
  - Traded (high volume at touches)
  - Proper angle (15°-70°)

Uses linear regression on swing points + touch validation.
"""

import numpy as np
import pandas as pd
import logging
import math
from dataclasses import dataclass, field
from scipy.stats import linregress

from src.config import (
    MIN_TOUCHES, TOUCH_ZONE_PCT, MIN_CANDLES_BETWEEN_TOUCHES,
    MIN_BOUNCE_PCT, MIN_ANGLE_DEG, MAX_ANGLE_DEG,
    SWING_LOOKBACK, VOLUME_MA_PERIOD,
)

logger = logging.getLogger(__name__)


@dataclass
class DiagonalLevel:
    """Represents a validated diagonal (sloping) level."""
    level_type: str              # "support" or "resistance"
    slope: float                 # Price change per candle
    intercept: float             # Price at index 0
    touches: list[int] = field(default_factory=list)  # Candle indices of touches
    touch_prices: list[float] = field(default_factory=list)
    angle_deg: float = 0.0
    is_traded: bool = False      # Has volume confirmation
    from_extremum: bool = False  # Built from significant swing point
    strength: float = 0.0       # Composite strength score (0-100)
    current_price_at_level: float = 0.0  # Projected price at current candle

    @property
    def num_touches(self) -> int:
        return len(self.touches)

    def price_at(self, candle_index: int) -> float:
        """Get the trendline price at a given candle index."""
        return self.slope * candle_index + self.intercept

    def distance_pct(self, price: float, candle_index: int) -> float:
        """Distance from price to trendline as percentage."""
        level_price = self.price_at(candle_index)
        if level_price == 0:
            return float("inf")
        return (price - level_price) / level_price


def find_swing_highs(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> list[int]:
    """Find swing high indices (local maxima)."""
    highs = df["high"].values
    swings = []
    for i in range(lookback, len(highs) - lookback):
        is_swing = True
        for j in range(1, lookback + 1):
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_swing = False
                break
        if is_swing:
            swings.append(i)
    return swings


def find_swing_lows(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> list[int]:
    """Find swing low indices (local minima)."""
    lows = df["low"].values
    swings = []
    for i in range(lookback, len(lows) - lookback):
        is_swing = True
        for j in range(1, lookback + 1):
            if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                is_swing = False
                break
        if is_swing:
            swings.append(i)
    return swings


def _compute_angle(slope: float, avg_price: float) -> float:
    """
    Compute the trendline angle in degrees.
    Normalizes slope by average price for comparability.
    """
    if avg_price == 0:
        return 0
    normalized_slope = slope / avg_price
    return abs(math.degrees(math.atan(normalized_slope * 100)))


def _check_touches(
    df: pd.DataFrame,
    slope: float,
    intercept: float,
    level_type: str,
    timeframe: str,
) -> tuple[list[int], list[float]]:
    """
    Find all candles that touch the trendline within the touch zone.

    Returns (touch_indices, touch_prices).
    """
    zone_pct = TOUCH_ZONE_PCT.get(timeframe, 0.0015)
    prices = df["high"].values if level_type == "resistance" else df["low"].values
    touches = []
    touch_prices = []
    last_touch_idx = -MIN_CANDLES_BETWEEN_TOUCHES - 1

    for i in range(len(prices)):
        level_price = slope * i + intercept
        if level_price <= 0:
            continue

        distance = abs(prices[i] - level_price) / level_price

        if distance <= zone_pct:
            if i - last_touch_idx >= MIN_CANDLES_BETWEEN_TOUCHES:
                # Check bounce: price must move away after touch
                if i + 3 < len(df):
                    if level_type == "support":
                        future_high = df["high"].values[i + 1: i + 4].max()
                        bounce = (future_high - prices[i]) / prices[i]
                    else:
                        future_low = df["low"].values[i + 1: i + 4].min()
                        bounce = (prices[i] - future_low) / prices[i]

                    if bounce >= MIN_BOUNCE_PCT:
                        touches.append(i)
                        touch_prices.append(prices[i])
                        last_touch_idx = i

    return touches, touch_prices


def _check_traded(df: pd.DataFrame, touches: list[int]) -> bool:
    """Check if the level is well-traded (volume above average at touch points)."""
    if "volume_ma" not in df.columns:
        vol_ma = df["volume"].rolling(VOLUME_MA_PERIOD).mean()
    else:
        vol_ma = df["volume_ma"]

    above_avg_count = 0
    for idx in touches:
        if idx >= len(df):
            continue
        vol = df["volume"].iloc[idx]
        avg = vol_ma.iloc[idx] if pd.notna(vol_ma.iloc[idx]) else 0
        if avg > 0 and vol >= avg * 1.2:  # 20%+ above average
            above_avg_count += 1

    # At least half of touches should have above-average volume
    return above_avg_count >= len(touches) / 2


def _compute_strength(level: DiagonalLevel) -> float:
    """
    Compute composite strength score (0-100) for a diagonal level.

    Factors:
      - Number of touches (more = stronger, but degrades after 5)
      - Volume confirmation (is_traded)
      - Built from extremum
      - Angle (optimal 30-50)
    """
    score = 0.0

    # Touches: 3=30, 4=40, 5=45, 6+=40 (degradation)
    t = level.num_touches
    if t == 3:
        score += 30
    elif t == 4:
        score += 40
    elif t == 5:
        score += 45
    else:
        score += 40  # degradation

    # Volume: +20
    if level.is_traded:
        score += 20

    # Extremum: +20
    if level.from_extremum:
        score += 20

    # Angle: +20 if optimal, +10 otherwise
    if 30 <= level.angle_deg <= 50:
        score += 20
    elif MIN_ANGLE_DEG <= level.angle_deg <= MAX_ANGLE_DEG:
        score += 10

    return min(score, 100)


def detect_diagonal_levels(
    df: pd.DataFrame,
    timeframe: str = "15m",
) -> list[DiagonalLevel]:
    """
    Main function: detect all valid diagonal levels in the OHLCV data.

    Returns a list of DiagonalLevel objects sorted by strength (descending).
    """
    if df is None or len(df) < 30:
        return []

    swing_highs = find_swing_highs(df)
    swing_lows = find_swing_lows(df)
    levels = []

    # ── Descending resistance (from swing highs) ──────────
    if len(swing_highs) >= 2:
        for i in range(len(swing_highs)):
            for j in range(i + 1, len(swing_highs)):
                idx1, idx2 = swing_highs[i], swing_highs[j]
                p1, p2 = df["high"].iloc[idx1], df["high"].iloc[idx2]

                if idx2 - idx1 < MIN_CANDLES_BETWEEN_TOUCHES:
                    continue

                slope = (p2 - p1) / (idx2 - idx1)
                intercept = p1 - slope * idx1

                # Only descending resistance (negative slope) or ascending
                touches, touch_prices = _check_touches(
                    df, slope, intercept, "resistance", timeframe
                )
                if len(touches) < MIN_TOUCHES:
                    continue

                avg_price = np.mean(touch_prices) if touch_prices else p1
                angle = _compute_angle(slope, avg_price)
                if angle < MIN_ANGLE_DEG or angle > MAX_ANGLE_DEG:
                    continue

                is_traded = _check_traded(df, touches)
                from_extremum = idx1 == swing_highs[0]  # First swing = extremum

                level = DiagonalLevel(
                    level_type="resistance",
                    slope=slope,
                    intercept=intercept,
                    touches=touches,
                    touch_prices=touch_prices,
                    angle_deg=angle,
                    is_traded=is_traded,
                    from_extremum=from_extremum,
                    current_price_at_level=slope * (len(df) - 1) + intercept,
                )
                level.strength = _compute_strength(level)
                levels.append(level)

    # ── Ascending support (from swing lows) ───────────────
    if len(swing_lows) >= 2:
        for i in range(len(swing_lows)):
            for j in range(i + 1, len(swing_lows)):
                idx1, idx2 = swing_lows[i], swing_lows[j]
                p1, p2 = df["low"].iloc[idx1], df["low"].iloc[idx2]

                if idx2 - idx1 < MIN_CANDLES_BETWEEN_TOUCHES:
                    continue

                slope = (p2 - p1) / (idx2 - idx1)
                intercept = p1 - slope * idx1

                touches, touch_prices = _check_touches(
                    df, slope, intercept, "support", timeframe
                )
                if len(touches) < MIN_TOUCHES:
                    continue

                avg_price = np.mean(touch_prices) if touch_prices else p1
                angle = _compute_angle(slope, avg_price)
                if angle < MIN_ANGLE_DEG or angle > MAX_ANGLE_DEG:
                    continue

                is_traded = _check_traded(df, touches)
                from_extremum = idx1 == swing_lows[0]

                level = DiagonalLevel(
                    level_type="support",
                    slope=slope,
                    intercept=intercept,
                    touches=touches,
                    touch_prices=touch_prices,
                    angle_deg=angle,
                    is_traded=is_traded,
                    from_extremum=from_extremum,
                    current_price_at_level=slope * (len(df) - 1) + intercept,
                )
                level.strength = _compute_strength(level)
                levels.append(level)

    # Deduplicate similar levels (within 0.5% of each other)
    levels = _deduplicate(levels, df)

    # Sort by strength descending
    levels.sort(key=lambda l: l.strength, reverse=True)

    return levels


def _deduplicate(levels: list[DiagonalLevel], df: pd.DataFrame) -> list[DiagonalLevel]:
    """Remove duplicate levels that are too close to each other."""
    if not levels:
        return levels

    unique = []
    last_idx = len(df) - 1

    for lvl in levels:
        is_dup = False
        lvl_price = lvl.price_at(last_idx)
        for existing in unique:
            ex_price = existing.price_at(last_idx)
            if ex_price == 0:
                continue
            if abs(lvl_price - ex_price) / ex_price < 0.005:  # Within 0.5%
                if lvl.strength > existing.strength:
                    unique.remove(existing)
                    unique.append(lvl)
                is_dup = True
                break
        if not is_dup:
            unique.append(lvl)

    return unique


def is_price_near_level(
    price: float,
    level: DiagonalLevel,
    candle_index: int,
    timeframe: str = "15m",
) -> bool:
    """Check if current price is within the touch zone of a diagonal level."""
    zone_pct = TOUCH_ZONE_PCT.get(timeframe, 0.0015)
    level_price = level.price_at(candle_index)
    if level_price <= 0:
        return False
    distance = abs(price - level_price) / level_price
    return distance <= zone_pct
