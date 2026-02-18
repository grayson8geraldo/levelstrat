"""
Pump Trading Module — specialized logic for pump/dump scenarios.

Implements:
  - Fair Value Gap (FVG) detection for imbalance zones
  - Pump deflation confirmation
  - Pump age validation
  - Modified entry/exit rules for pump trades
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass, field

from src.config import (
    PUMP_MIN_MOVE_PCT, PUMP_MIN_VOLUME_MULT,
    PUMP_MIN_CONSECUTIVE_CANDLES, PUMP_DEFLATION_MIN_RETRACE,
    PUMP_MAX_AGE_HOURS,
)

logger = logging.getLogger(__name__)

# Timeframe to minutes mapping
TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15,
    "30m": 30, "1h": 60, "2h": 120, "4h": 240,
    "6h": 360, "8h": 480, "12h": 720, "1d": 1440,
}


@dataclass
class FairValueGap:
    """Represents a Fair Value Gap (imbalance zone)."""
    gap_type: str          # "bullish" (gap up) or "bearish" (gap down)
    top: float             # Upper bound of the gap
    bottom: float          # Lower bound of the gap
    candle_index: int      # Index where gap was created
    size_pct: float = 0.0  # Gap size as % of price
    filled: bool = False   # Whether price has returned to fill the gap


@dataclass
class PumpAnalysis:
    """Complete pump analysis result."""
    is_pump: bool = False
    pump_direction: str = ""       # "up" or "down"
    pump_start_idx: int = 0
    pump_end_idx: int = 0
    pump_move_pct: float = 0.0
    pump_age_minutes: int = 0
    is_deflating: bool = False
    retrace_pct: float = 0.0
    fvgs: list[FairValueGap] = field(default_factory=list)
    unfilled_fvgs: list[FairValueGap] = field(default_factory=list)
    consecutive_candles: int = 0
    is_tradeable: bool = False     # Passes all pump trading criteria
    reason: str = ""               # Why it's tradeable or not


def detect_fair_value_gaps(df: pd.DataFrame, min_gap_pct: float = 0.001) -> list[FairValueGap]:
    """
    Detect Fair Value Gaps (FVGs) in OHLCV data.

    An FVG is an imbalance zone where:
    - Bullish FVG: candle[i-1].high < candle[i+1].low (gap up)
    - Bearish FVG: candle[i-1].low > candle[i+1].high (gap down)

    These zones often act as magnets for price retracement.
    """
    if df is None or len(df) < 3:
        return []

    fvgs = []
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    for i in range(1, len(df) - 1):
        mid_price = closes[i]
        if mid_price <= 0:
            continue

        # Bullish FVG: gap up
        if lows[i + 1] > highs[i - 1]:
            gap_size = lows[i + 1] - highs[i - 1]
            gap_pct = gap_size / mid_price
            if gap_pct >= min_gap_pct:
                fvg = FairValueGap(
                    gap_type="bullish",
                    top=float(lows[i + 1]),
                    bottom=float(highs[i - 1]),
                    candle_index=i,
                    size_pct=gap_pct,
                )
                # Check if gap has been filled by subsequent price action
                if i + 2 < len(df):
                    subsequent_lows = lows[i + 2:]
                    if len(subsequent_lows) > 0 and np.min(subsequent_lows) <= highs[i - 1]:
                        fvg.filled = True
                fvgs.append(fvg)

        # Bearish FVG: gap down
        if highs[i + 1] < lows[i - 1]:
            gap_size = lows[i - 1] - highs[i + 1]
            gap_pct = gap_size / mid_price
            if gap_pct >= min_gap_pct:
                fvg = FairValueGap(
                    gap_type="bearish",
                    top=float(lows[i - 1]),
                    bottom=float(highs[i + 1]),
                    candle_index=i,
                    size_pct=gap_pct,
                )
                if i + 2 < len(df):
                    subsequent_highs = highs[i + 2:]
                    if len(subsequent_highs) > 0 and np.max(subsequent_highs) >= lows[i - 1]:
                        fvg.filled = True
                fvgs.append(fvg)

    return fvgs


def detect_pump(
    df: pd.DataFrame,
    timeframe: str = "15m",
) -> PumpAnalysis:
    """
    Detect and analyze a pump/dump move.

    Identifies:
    1. The pump start/end points
    2. Consecutive same-direction candles
    3. Total move size
    4. Whether deflation has begun
    5. Fair Value Gaps created during the pump
    """
    result = PumpAnalysis()

    if df is None or len(df) < 10:
        return result

    closes = df["close"].values
    opens = df["open"].values
    volumes = df["volume"].values

    # Find the most recent consecutive run of same-direction candles
    last_idx = len(closes) - 1
    direction = 1 if closes[last_idx] > opens[last_idx] else -1

    # Scan backwards to find the pump
    consecutive = 0
    pump_end_idx = last_idx
    pump_start_idx = last_idx

    for i in range(last_idx, 0, -1):
        candle_dir = 1 if closes[i] > opens[i] else -1
        if candle_dir == direction:
            consecutive += 1
            pump_start_idx = i
        else:
            # Check if the non-matching candle is small (pullback within pump)
            body = abs(closes[i] - opens[i])
            avg_body = np.mean(np.abs(closes[max(0, i - 10):i] - opens[max(0, i - 10):i]))
            if avg_body > 0 and body < avg_body * 0.3:
                # Small pullback, continue looking
                pump_start_idx = i
                continue
            break

    result.consecutive_candles = consecutive

    if consecutive < PUMP_MIN_CONSECUTIVE_CANDLES:
        # Not enough consecutive candles — look for a pump in recent history
        # Check last 50 candles for a rapid move
        lookback = min(50, len(df))
        window = df.iloc[-lookback:]

        high_val = float(window["high"].max())
        low_val = float(window["low"].min())
        high_idx = int(window["high"].values.argmax())
        low_idx = int(window["low"].values.argmin())

        if low_val > 0:
            up_move = (high_val - low_val) / low_val
        else:
            up_move = 0

        if up_move >= PUMP_MIN_MOVE_PCT:
            if high_idx > low_idx:
                # Pump up
                result.pump_direction = "up"
                result.pump_start_idx = len(df) - lookback + low_idx
                result.pump_end_idx = len(df) - lookback + high_idx
            else:
                # Pump down
                result.pump_direction = "down"
                result.pump_start_idx = len(df) - lookback + high_idx
                result.pump_end_idx = len(df) - lookback + low_idx

            result.pump_move_pct = up_move
            result.is_pump = True
        else:
            return result
    else:
        result.is_pump = True
        result.pump_start_idx = pump_start_idx
        result.pump_end_idx = pump_end_idx
        result.pump_direction = "up" if direction > 0 else "down"

        start_price = closes[pump_start_idx]
        end_price = closes[pump_end_idx]
        if start_price > 0:
            result.pump_move_pct = abs(end_price - start_price) / start_price

    if not result.is_pump:
        return result

    # Calculate pump age
    tf_minutes = TF_MINUTES.get(timeframe, 15)
    candles_since_pump_end = last_idx - result.pump_end_idx
    result.pump_age_minutes = candles_since_pump_end * tf_minutes

    # Check deflation
    if result.pump_end_idx < last_idx:
        if result.pump_direction == "up":
            pump_high = float(df["high"].iloc[result.pump_end_idx])
            current_low = float(df["low"].iloc[-1])
            if pump_high > 0:
                result.retrace_pct = (pump_high - current_low) / pump_high
        else:
            pump_low = float(df["low"].iloc[result.pump_end_idx])
            current_high = float(df["high"].iloc[-1])
            if pump_low > 0:
                result.retrace_pct = (current_high - pump_low) / pump_low

        result.is_deflating = result.retrace_pct >= PUMP_DEFLATION_MIN_RETRACE

    # Detect FVGs in the pump zone
    pump_slice = df.iloc[max(0, result.pump_start_idx - 1):result.pump_end_idx + 2]
    result.fvgs = detect_fair_value_gaps(pump_slice)
    result.unfilled_fvgs = [f for f in result.fvgs if not f.filled]

    # Determine if tradeable
    max_age = PUMP_MAX_AGE_HOURS * 60
    if result.pump_age_minutes > max_age:
        result.reason = f"Памп слишком старый ({result.pump_age_minutes // 60}ч > {PUMP_MAX_AGE_HOURS}ч)"
    elif not result.is_deflating:
        result.reason = f"Нет подтверждения сдутия (откат {result.retrace_pct:.1%} < {PUMP_DEFLATION_MIN_RETRACE:.0%})"
    elif result.pump_move_pct < PUMP_MIN_MOVE_PCT:
        result.reason = f"Движение слишком маленькое ({result.pump_move_pct:.1%} < {PUMP_MIN_MOVE_PCT:.0%})"
    else:
        result.is_tradeable = True
        result.reason = (
            f"Памп {'вверх' if result.pump_direction == 'up' else 'вниз'} "
            f"{result.pump_move_pct:.1%}, сдутие {result.retrace_pct:.1%}, "
            f"{len(result.unfilled_fvgs)} незаполненных FVG"
        )

    return result


def get_pump_entry_zone(
    pump: PumpAnalysis, df: pd.DataFrame
) -> tuple[float, float] | None:
    """
    Get the optimal entry zone for a pump trade.

    For a deflating pump UP → SHORT entry near:
      - Unfilled bearish FVG zones
      - Or 38.2-61.8% retracement of the deflation move

    For a deflating pump DOWN → LONG entry near:
      - Unfilled bullish FVG zones
      - Or 38.2-61.8% retracement of the deflation move

    Returns (entry_low, entry_high) zone or None.
    """
    if not pump.is_tradeable or df is None:
        return None

    # Prefer unfilled FVG zones
    if pump.unfilled_fvgs:
        # Pick the closest unfilled FVG to current price
        current_price = float(df["close"].iloc[-1])
        closest_fvg = min(
            pump.unfilled_fvgs,
            key=lambda f: abs((f.top + f.bottom) / 2 - current_price)
        )
        return (closest_fvg.bottom, closest_fvg.top)

    # Fallback: use Fibonacci of the deflation leg
    if pump.pump_direction == "up":
        pump_high = float(df["high"].iloc[pump.pump_end_idx])
        current_low = float(df["low"].iloc[-1])
        retrace_range = pump_high - current_low
        entry_high = current_low + retrace_range * 0.618
        entry_low = current_low + retrace_range * 0.382
    else:
        pump_low = float(df["low"].iloc[pump.pump_end_idx])
        current_high = float(df["high"].iloc[-1])
        retrace_range = current_high - pump_low
        entry_low = current_high - retrace_range * 0.618
        entry_high = current_high - retrace_range * 0.382

    return (entry_low, entry_high)
