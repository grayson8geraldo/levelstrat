"""
Confluence Detector — finds zones where multiple technical factors align.

Checks:
  - Fibonacci retracement levels (0.382, 0.5, 0.618, 0.786)
  - Horizontal support/resistance (historical price clusters)
  - Volume Profile POC (Point of Control)
  - Round numbers ($100, $1000, $10000, etc.)
  - EMA proximity

Each factor adds a bonus to the overall signal score.
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass, field

from src.config import EMA_SLOW, EMA_GLOBAL

logger = logging.getLogger(__name__)

# Fibonacci levels to check
FIB_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.786]
FIB_ZONE_PCT = 0.003  # 0.3% tolerance around Fibonacci level

# Horizontal level detection
HORIZONTAL_TOUCH_ZONE = 0.004  # 0.4% zone for horizontal touches
HORIZONTAL_MIN_TOUCHES = 3
HORIZONTAL_LOOKBACK = 100  # candles to look back for price clusters

# Volume Profile
VPOC_NUM_BINS = 50  # number of price bins for volume profile
VPOC_ZONE_PCT = 0.003  # 0.3% tolerance around VPOC


@dataclass
class ConfluenceResult:
    """Result of confluence analysis."""
    score: float = 0.0            # Total confluence bonus (0-100)
    factors: list[str] = field(default_factory=list)  # Human-readable factors
    fibonacci_hit: bool = False
    fib_level: float = 0.0       # Which Fib level was hit
    horizontal_hit: bool = False
    horizontal_touches: int = 0
    vpoc_hit: bool = False
    vpoc_price: float = 0.0
    round_number_hit: bool = False
    ema_hit: bool = False
    ema_name: str = ""


def detect_fibonacci_levels(
    df: pd.DataFrame, lookback: int = 100
) -> list[tuple[float, float]]:
    """
    Find Fibonacci retracement levels from the most recent swing.

    Returns list of (fib_ratio, price) tuples.
    """
    if df is None or len(df) < 20:
        return []

    window = df.iloc[-lookback:] if len(df) >= lookback else df
    highs = window["high"].values
    lows = window["low"].values

    swing_high = float(np.max(highs))
    swing_low = float(np.min(lows))

    high_idx = int(np.argmax(highs))
    low_idx = int(np.argmin(lows))

    if swing_high <= swing_low:
        return []

    price_range = swing_high - swing_low
    fib_levels = []

    if high_idx > low_idx:
        # Uptrend retracement: measure from low to high
        for ratio in FIB_LEVELS:
            price = swing_high - price_range * ratio
            fib_levels.append((ratio, price))
    else:
        # Downtrend retracement: measure from high to low
        for ratio in FIB_LEVELS:
            price = swing_low + price_range * ratio
            fib_levels.append((ratio, price))

    return fib_levels


def detect_horizontal_levels(
    df: pd.DataFrame, lookback: int = HORIZONTAL_LOOKBACK
) -> list[dict]:
    """
    Find horizontal support/resistance levels by detecting price clusters.

    Uses a simple approach: bin prices and find bins with many touches.

    Returns list of {"price": float, "touches": int, "type": "support"|"resistance"}.
    """
    if df is None or len(df) < 30:
        return []

    window = df.iloc[-lookback:] if len(df) >= lookback else df
    current_price = float(window["close"].iloc[-1])

    # Collect all swing points (local highs and lows)
    highs = window["high"].values
    lows = window["low"].values

    price_min = float(np.min(lows))
    price_max = float(np.max(highs))

    if price_max <= price_min:
        return []

    # Create price bins
    bin_size = (price_max - price_min) / VPOC_NUM_BINS
    if bin_size <= 0:
        return []

    touch_counts = {}  # bin_center -> count of touches

    for i in range(2, len(highs) - 2):
        # Check if it's a local high
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            bin_idx = int((highs[i] - price_min) / bin_size)
            center = price_min + (bin_idx + 0.5) * bin_size
            touch_counts[center] = touch_counts.get(center, 0) + 1

        # Check if it's a local low
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            bin_idx = int((lows[i] - price_min) / bin_size)
            center = price_min + (bin_idx + 0.5) * bin_size
            touch_counts[center] = touch_counts.get(center, 0) + 1

    levels = []
    for price, touches in touch_counts.items():
        if touches >= HORIZONTAL_MIN_TOUCHES:
            level_type = "support" if price < current_price else "resistance"
            levels.append({
                "price": price,
                "touches": touches,
                "type": level_type,
            })

    # Sort by touches descending
    levels.sort(key=lambda x: x["touches"], reverse=True)
    return levels[:10]  # Top 10 horizontal levels


def compute_volume_profile(df: pd.DataFrame, num_bins: int = VPOC_NUM_BINS) -> float:
    """
    Compute Volume Profile POC (Point of Control).

    POC = the price level with the highest traded volume.

    Returns the POC price, or 0 if cannot compute.
    """
    if df is None or len(df) < 20:
        return 0.0

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())

    if price_max <= price_min:
        return 0.0

    bin_size = (price_max - price_min) / num_bins
    if bin_size <= 0:
        return 0.0

    volume_at_price = np.zeros(num_bins)

    for _, row in df.iterrows():
        low_bin = max(0, int((row["low"] - price_min) / bin_size))
        high_bin = min(num_bins - 1, int((row["high"] - price_min) / bin_size))

        # Distribute volume across price range of the candle
        if high_bin >= low_bin:
            bins_count = high_bin - low_bin + 1
            vol_per_bin = row["volume"] / bins_count
            for b in range(low_bin, high_bin + 1):
                if 0 <= b < num_bins:
                    volume_at_price[b] += vol_per_bin

    poc_bin = int(np.argmax(volume_at_price))
    poc_price = price_min + (poc_bin + 0.5) * bin_size

    return poc_price


def check_round_number(price: float) -> bool:
    """Check if price is near a psychologically significant round number."""
    if price <= 0:
        return False

    # Determine magnitude-appropriate round number
    if price >= 10000:
        magnitude = 1000
    elif price >= 1000:
        magnitude = 100
    elif price >= 100:
        magnitude = 50
    elif price >= 10:
        magnitude = 5
    elif price >= 1:
        magnitude = 0.5
    else:
        magnitude = 0.05

    remainder = price % magnitude
    distance_to_round = min(remainder, magnitude - remainder)
    threshold = price * 0.002  # Within 0.2% of round number

    return distance_to_round <= threshold


def analyze_confluence(
    level_price: float,
    df: pd.DataFrame,
    df_indicator: pd.DataFrame | None = None,
) -> ConfluenceResult:
    """
    Comprehensive confluence analysis for a given price level.

    Checks all confluence factors and returns a scored result.

    Scoring:
      - Fibonacci level:    +25
      - Horizontal S/R:     +20
      - Volume POC:         +20
      - Round number:       +15
      - EMA alignment:      +20
      Total possible:       100
    """
    result = ConfluenceResult()

    if level_price <= 0 or df is None or len(df) < 20:
        return result

    # ── 1. Fibonacci Check ──────────────────────────────
    fib_levels = detect_fibonacci_levels(df)
    for ratio, fib_price in fib_levels:
        if fib_price > 0:
            dist = abs(level_price - fib_price) / fib_price
            if dist <= FIB_ZONE_PCT:
                result.fibonacci_hit = True
                result.fib_level = ratio
                result.score += 25
                result.factors.append(f"Fib {ratio:.3f} ({fib_price:.2f})")
                break

    # ── 2. Horizontal Level Check ───────────────────────
    h_levels = detect_horizontal_levels(df)
    for hlevel in h_levels:
        hp = hlevel["price"]
        if hp > 0:
            dist = abs(level_price - hp) / hp
            if dist <= HORIZONTAL_TOUCH_ZONE:
                result.horizontal_hit = True
                result.horizontal_touches = hlevel["touches"]
                result.score += 20
                result.factors.append(
                    f"Гориз. уровень ({hlevel['touches']} касаний, {hp:.2f})"
                )
                break

    # ── 3. Volume POC Check ─────────────────────────────
    vpoc = compute_volume_profile(df)
    if vpoc > 0:
        dist = abs(level_price - vpoc) / vpoc
        if dist <= VPOC_ZONE_PCT:
            result.vpoc_hit = True
            result.vpoc_price = vpoc
            result.score += 20
            result.factors.append(f"VPOC ({vpoc:.2f})")

    # ── 4. Round Number Check ───────────────────────────
    if check_round_number(level_price):
        result.round_number_hit = True
        result.score += 15
        result.factors.append("Круглый уровень")

    # ── 5. EMA Check ────────────────────────────────────
    if df_indicator is not None and len(df_indicator) > 0:
        last = df_indicator.iloc[-1]
        for ema_col in [f"EMA_{EMA_SLOW}", f"EMA_{EMA_GLOBAL}"]:
            ema_val = last.get(ema_col)
            if ema_val is not None and not pd.isna(ema_val):
                dist = abs(level_price - float(ema_val)) / level_price
                if dist < 0.003:  # Within 0.3%
                    result.ema_hit = True
                    result.ema_name = ema_col
                    result.score += 20
                    result.factors.append(f"{ema_col} ({float(ema_val):.2f})")
                    break

    return result
