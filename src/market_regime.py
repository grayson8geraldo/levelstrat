"""
Market Regime Filter — determines market conditions and trading session suitability.

Checks:
  - Volatility regime (low/normal/high/extreme)
  - Trading session (Asian/European/American)
  - Macro event windows (pre/post major events)
  - Overall market sentiment (BTC dominance proxy)
"""

import logging
from datetime import datetime, timezone
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.config import ATR_PERIOD

logger = logging.getLogger(__name__)

# Volatility regime thresholds (ATR as % of price)
VOL_LOW = 0.005          # < 0.5% ATR = low volatility
VOL_NORMAL_HIGH = 0.02   # < 2% ATR = normal
VOL_HIGH = 0.04          # < 4% ATR = high
# > 4% = extreme

# Trading sessions (UTC hours)
SESSIONS = {
    "asian":     (0, 8),    # 00:00-08:00 UTC (Tokyo/HK)
    "european":  (7, 16),   # 07:00-16:00 UTC (London)
    "american":  (13, 22),  # 13:00-22:00 UTC (New York)
}

# Session overlaps are the most liquid
SESSION_OVERLAPS = {
    "asian_european":    (7, 8),    # London open
    "european_american": (13, 16),  # NY overlap with London
}

# Known macro event patterns (hour UTC, day of week)
# CPI: 2nd Tuesday of month, 13:30 UTC
# FOMC: 8 times/year, Wednesday 19:00 UTC
# NFP: 1st Friday of month, 13:30 UTC
MACRO_BLACKOUT_MINUTES = 30  # Don't trade 30 min before/after

# BTC crash detection
BTC_CRASH_THRESHOLD = -0.05  # -5% in context TF = avoid alts


@dataclass
class MarketRegime:
    """Current market regime assessment."""
    volatility: str = "normal"     # "low", "normal", "high", "extreme"
    volatility_pct: float = 0.0    # ATR as % of price
    session: str = "unknown"       # "asian", "european", "american"
    is_overlap: bool = False       # In session overlap (high liquidity)
    overlap_name: str = ""
    is_macro_window: bool = False  # Near macro event
    macro_event: str = ""
    btc_regime: str = "normal"     # "normal", "crash", "pump"
    can_trade: bool = True
    score_adjustment: float = 0.0  # -20 to +20
    reasons: list = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


def detect_volatility_regime(df: pd.DataFrame) -> tuple[str, float]:
    """
    Determine volatility regime from ATR relative to price.

    Returns (regime_name, atr_pct).
    """
    if df is None or len(df) < ATR_PERIOD + 5:
        return "normal", 0.0

    atr_col = f"ATR_{ATR_PERIOD}"
    if atr_col not in df.columns:
        # Calculate manually
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(ATR_PERIOD).mean().iloc[-1])
    else:
        atr = float(df[atr_col].iloc[-1])

    current_price = float(df["close"].iloc[-1])
    if current_price <= 0:
        return "normal", 0.0

    atr_pct = atr / current_price

    if atr_pct < VOL_LOW:
        return "low", atr_pct
    elif atr_pct < VOL_NORMAL_HIGH:
        return "normal", atr_pct
    elif atr_pct < VOL_HIGH:
        return "high", atr_pct
    else:
        return "extreme", atr_pct


def get_current_session() -> tuple[str, bool, str]:
    """
    Determine the current trading session and if we're in an overlap.

    Returns (session_name, is_overlap, overlap_name).
    """
    now = datetime.now(timezone.utc)
    hour = now.hour

    session = "off_hours"
    for name, (start, end) in SESSIONS.items():
        if start <= hour < end:
            session = name
            break

    is_overlap = False
    overlap_name = ""
    for name, (start, end) in SESSION_OVERLAPS.items():
        if start <= hour < end:
            is_overlap = True
            overlap_name = name
            break

    return session, is_overlap, overlap_name


def check_macro_window() -> tuple[bool, str]:
    """
    Check if we're in a macro event blackout window.

    Uses simple heuristics for common US macro events:
    - NFP: 1st Friday of month, ~13:30 UTC
    - CPI: ~2nd Tuesday of month, ~13:30 UTC
    - FOMC: ~every 6 weeks, Wednesday ~19:00 UTC

    Returns (is_in_window, event_name).
    """
    now = datetime.now(timezone.utc)
    day = now.day
    weekday = now.weekday()  # 0=Mon, 4=Fri
    hour = now.hour
    minute = now.minute
    current_minutes = hour * 60 + minute

    # NFP: 1st Friday of month, 13:30 UTC
    if weekday == 4 and day <= 7:
        event_time = 13 * 60 + 30  # 13:30 UTC
        if abs(current_minutes - event_time) <= MACRO_BLACKOUT_MINUTES:
            return True, "NFP (Non-Farm Payrolls)"

    # CPI: usually 2nd Tuesday, 13:30 UTC
    if weekday == 1 and 8 <= day <= 14:
        event_time = 13 * 60 + 30
        if abs(current_minutes - event_time) <= MACRO_BLACKOUT_MINUTES:
            return True, "CPI (Consumer Price Index)"

    # FOMC: selected Wednesdays, 19:00 UTC
    if weekday == 2:
        event_time = 19 * 60
        if abs(current_minutes - event_time) <= MACRO_BLACKOUT_MINUTES:
            # Not all Wednesdays have FOMC, but we're conservative
            return True, "Potential FOMC window"

    return False, ""


def analyze_market_regime(
    df_working: pd.DataFrame,
    df_btc: pd.DataFrame | None = None,
) -> MarketRegime:
    """
    Comprehensive market regime analysis.

    Args:
        df_working: Working timeframe OHLCV with indicators
        df_btc: BTC/USDT OHLCV for market-wide sentiment (optional)

    Returns:
        MarketRegime with all assessments and score adjustment.
    """
    regime = MarketRegime()

    # 1. Volatility
    vol_regime, vol_pct = detect_volatility_regime(df_working)
    regime.volatility = vol_regime
    regime.volatility_pct = vol_pct

    if vol_regime == "low":
        regime.score_adjustment -= 10
        regime.reasons.append(f"Низкая волатильность ({vol_pct:.2%}) — слабые движения")
    elif vol_regime == "high":
        regime.score_adjustment += 5
        regime.reasons.append(f"Высокая волатильность ({vol_pct:.2%}) — хорошие движения")
    elif vol_regime == "extreme":
        regime.score_adjustment -= 15
        regime.can_trade = False
        regime.reasons.append(f"Экстремальная волатильность ({vol_pct:.2%}) — опасно торговать")

    # 2. Trading session
    session, is_overlap, overlap_name = get_current_session()
    regime.session = session
    regime.is_overlap = is_overlap
    regime.overlap_name = overlap_name

    if is_overlap:
        regime.score_adjustment += 10
        regime.reasons.append(f"Сессия: перекрытие {overlap_name} (макс. ликвидность)")
    elif session == "asian":
        regime.score_adjustment -= 5
        regime.reasons.append("Азиатская сессия (низкая ликвидность для крипто)")
    elif session in ("european", "american"):
        regime.score_adjustment += 5
        regime.reasons.append(f"Активная сессия: {session}")

    # 3. Macro events
    is_macro, event_name = check_macro_window()
    regime.is_macro_window = is_macro
    regime.macro_event = event_name

    if is_macro:
        regime.can_trade = False
        regime.score_adjustment -= 30
        regime.reasons.append(f"Макро-событие: {event_name} — НЕ ТОРГОВАТЬ")

    # 4. BTC regime (market sentiment)
    if df_btc is not None and len(df_btc) >= 20:
        btc_closes = df_btc["close"].values
        if len(btc_closes) >= 10:
            btc_change = (btc_closes[-1] - btc_closes[-10]) / btc_closes[-10]

            if btc_change <= BTC_CRASH_THRESHOLD:
                regime.btc_regime = "crash"
                regime.score_adjustment -= 20
                regime.reasons.append(f"BTC в обвале ({btc_change:.1%}) — опасно для альтов")
            elif btc_change >= abs(BTC_CRASH_THRESHOLD):
                regime.btc_regime = "pump"
                regime.score_adjustment += 5
                regime.reasons.append(f"BTC в росте ({btc_change:+.1%})")

    return regime
