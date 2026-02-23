"""
Coin Screener — filters coins by volume, spread, trend.
Selects candidates for DLS signal scanning.

Enhanced with VolumeTracker: detects coins with accelerating volume
to catch them BEFORE peak activity, not after.
"""

import time
import logging
import pandas as pd
from typing import Optional

from src.config import (
    MIN_VOLUME_24H, MAX_SPREAD_PCT, TOP_COINS_TO_SCAN,
    TF_DIRECTION, EMA_FAST, EMA_MEDIUM, EMA_SLOW,
    RSI_PERIOD, RSI_EXTREME_OB, RSI_EXTREME_OS,
    PUMP_MIN_MOVE_PCT, PUMP_MIN_VOLUME_MULT,
    VOLUME_ACCEL_MIN_READINGS, VOLUME_ACCEL_LOOKBACK,
    VOLUME_ACCEL_MAX_HISTORY, VOLUME_ACCEL_WEIGHT,
    VOLUME_ACCEL_MIN_24H,
)
from src.data_fetcher import DataFetcher
from src.indicators import compute_indicators

logger = logging.getLogger(__name__)


class VolumeTracker:
    """Track volume acceleration across scan cycles.

    Caches 24h volume readings per coin and calculates how fast
    volume is growing. Coins with accelerating volume are likely
    approaching peak activity — not past it.
    """

    def __init__(self):
        # symbol -> [(timestamp, volume_24h)]
        self._history: dict[str, list[tuple[float, float]]] = {}

    def update(self, symbol: str, volume: float):
        """Record current volume reading."""
        if symbol not in self._history:
            self._history[symbol] = []
        self._history[symbol].append((time.time(), volume))
        if len(self._history[symbol]) > VOLUME_ACCEL_MAX_HISTORY:
            self._history[symbol] = self._history[symbol][-VOLUME_ACCEL_MAX_HISTORY:]

    def get_acceleration(self, symbol: str) -> float:
        """Get volume acceleration (ratio of current vs older reading).

        Returns:
          > 1.0: volume is increasing (coin heating up)
          < 1.0: volume is decreasing (coin cooling down)
            1.0: stable or insufficient data
        """
        if symbol not in self._history:
            return 1.0

        readings = self._history[symbol]
        if len(readings) < VOLUME_ACCEL_MIN_READINGS:
            return 1.0

        # Compare latest reading vs LOOKBACK cycles ago
        lookback_idx = max(0, len(readings) - VOLUME_ACCEL_LOOKBACK - 1)
        older_vol = readings[lookback_idx][1]
        current_vol = readings[-1][1]

        if older_vol <= 0:
            return 1.0

        return current_vol / older_vol

    def cleanup(self, active_symbols: set[str]):
        """Remove tracking for coins no longer in candidate pool."""
        stale = [s for s in self._history if s not in active_symbols]
        for s in stale:
            del self._history[s]


class CoinScreener:
    """Screens coins and classifies them into 4 scenario types."""

    def __init__(self, fetcher: DataFetcher):
        self.fetcher = fetcher
        self.vol_tracker = VolumeTracker()

    def get_candidates(self) -> list[dict]:
        """
        Screen all USDT perps → return sorted list of tradeable candidates.

        Uses a two-stage filter:
          1. Lower volume bar ($10M) to catch coins heating up early
          2. Volume acceleration weighting to prioritize rising coins
          3. Top N by heat score (volume × acceleration)

        Each candidate dict:
        {
            "symbol": "BTC/USDT:USDT",
            "volume_24h": 1234567890,
            "spread_pct": 0.01,
            "price": 65000.0,
            "scenario": "uptrend" | "downtrend" | "pump" | "new_listing" | None,
            "trend_strength": float,
            "volume_accel": float,   # > 1.0 = heating up
            "heat_score": float,     # combined ranking score
        }
        """
        perps = self.fetcher.get_usdt_perpetuals()
        symbols = [p["symbol"] for p in perps]

        tickers = self.fetcher.fetch_tickers_batch(symbols)
        if not tickers:
            logger.warning("No tickers received")
            return []

        candidates = []
        for symbol, ticker in tickers.items():
            vol = ticker.get("quoteVolume") or 0
            # Lower bar to catch coins before they peak
            if vol < VOLUME_ACCEL_MIN_24H:
                continue

            bid = ticker.get("bid") or 0
            ask = ticker.get("ask") or 0
            if bid <= 0 or ask <= 0:
                continue
            spread_pct = (ask - bid) / bid * 100
            if spread_pct > MAX_SPREAD_PCT:
                continue

            # Update volume tracker
            self.vol_tracker.update(symbol, vol)
            accel = self.vol_tracker.get_acceleration(symbol)

            # Heat score: blend absolute volume with acceleration
            # Acceleration weight makes rising-volume coins rank higher
            heat_score = vol * (1.0 + VOLUME_ACCEL_WEIGHT * (accel - 1.0))

            candidates.append({
                "symbol": symbol,
                "volume_24h": vol,
                "spread_pct": spread_pct,
                "price": ticker.get("last") or ticker.get("close") or 0,
                "change_pct": ticker.get("percentage") or 0,
                "scenario": None,
                "trend_strength": 0,
                "volume_accel": round(accel, 3),
                "heat_score": heat_score,
            })

        # Sort by heat score (volume × acceleration), take top N
        candidates.sort(key=lambda c: c["heat_score"], reverse=True)
        candidates = candidates[:TOP_COINS_TO_SCAN]

        # Cleanup stale tracker entries
        active = {c["symbol"] for c in candidates}
        self.vol_tracker.cleanup(active)

        # Log top accelerating coins
        hot_coins = [c for c in candidates if c["volume_accel"] > 1.05]
        if hot_coins:
            hot_coins.sort(key=lambda c: c["volume_accel"], reverse=True)
            top3 = hot_coins[:3]
            hot_str = ", ".join(
                f"{c['symbol'].split('/')[0]}(+{(c['volume_accel']-1)*100:.0f}%)"
                for c in top3
            )
            logger.info(f"Screener: hot coins (volume rising): {hot_str}")

        logger.info(f"Screener: {len(candidates)} coins passed volume/spread filter")
        return candidates

    def classify_scenario(
        self, candidate: dict, df_1h: Optional[pd.DataFrame]
    ) -> dict:
        """
        Classify a candidate into one of 4 scenarios based on 1H data.

        Returns updated candidate with 'scenario' and 'trend_strength'.
        """
        if df_1h is None or len(df_1h) < EMA_SLOW + 10:
            candidate["scenario"] = None
            return candidate

        ind = compute_indicators(df_1h)
        if ind is None:
            candidate["scenario"] = None
            return candidate

        last = ind.iloc[-1]
        ema_f = last.get(f"EMA_{EMA_FAST}")
        ema_m = last.get(f"EMA_{EMA_MEDIUM}")
        ema_s = last.get(f"EMA_{EMA_SLOW}")
        rsi = last.get(f"RSI_{RSI_PERIOD}")
        close = last["close"]
        change = candidate.get("change_pct", 0)

        if any(v is None or pd.isna(v) for v in [ema_f, ema_m, ema_s, rsi]):
            candidate["scenario"] = None
            return candidate

        # ── Pump detection ──
        abs_change = abs(change) / 100 if change else 0
        if abs_change >= PUMP_MIN_MOVE_PCT:
            vol_col = "volume_ratio"
            vol_ratio = last.get(vol_col, 1.0)
            if pd.notna(vol_ratio) and vol_ratio >= PUMP_MIN_VOLUME_MULT:
                candidate["scenario"] = "pump"
                candidate["trend_strength"] = abs_change
                return candidate

        # ── Strong downtrend ──
        if ema_f < ema_m < ema_s and rsi < 45:
            strength = (ema_s - ema_f) / ema_s  # EMA spread as strength
            candidate["scenario"] = "downtrend"
            candidate["trend_strength"] = strength
            return candidate

        # ── Strong uptrend ──
        if ema_f > ema_m > ema_s and rsi > 55:
            strength = (ema_f - ema_s) / ema_s
            candidate["scenario"] = "uptrend"
            candidate["trend_strength"] = strength
            return candidate

        # ── Ranging / weak trend — still tradeable on levels ──
        # Coins without strong EMA alignment can still have valid diagonal levels
        ema_spread = abs(ema_f - ema_s) / ema_s
        if ema_spread < 0.02:  # EMAs within 2% = ranging
            candidate["scenario"] = "ranging"
            candidate["trend_strength"] = ema_spread
            return candidate

        # ── Weak downtrend (EMAs not fully aligned but bearish bias) ──
        if ema_f < ema_s:
            candidate["scenario"] = "downtrend"
            candidate["trend_strength"] = (ema_s - ema_f) / ema_s
            return candidate

        # ── Weak uptrend ──
        if ema_f > ema_s:
            candidate["scenario"] = "uptrend"
            candidate["trend_strength"] = (ema_f - ema_s) / ema_s
            return candidate

        candidate["scenario"] = None
        return candidate
