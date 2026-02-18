"""
Coin Screener — filters coins by volume, spread, trend.
Selects candidates for DLS signal scanning.
"""

import logging
import pandas as pd
from typing import Optional

from src.config import (
    MIN_VOLUME_24H, MAX_SPREAD_PCT, TOP_COINS_TO_SCAN,
    TF_DIRECTION, EMA_FAST, EMA_MEDIUM, EMA_SLOW,
    RSI_PERIOD, RSI_EXTREME_OB, RSI_EXTREME_OS,
    PUMP_MIN_MOVE_PCT, PUMP_MIN_VOLUME_MULT,
)
from src.data_fetcher import DataFetcher
from src.indicators import compute_indicators

logger = logging.getLogger(__name__)


class CoinScreener:
    """Screens coins and classifies them into 4 scenario types."""

    def __init__(self, fetcher: DataFetcher):
        self.fetcher = fetcher

    def get_candidates(self) -> list[dict]:
        """
        Screen all USDT perps → return sorted list of tradeable candidates.

        Each candidate dict:
        {
            "symbol": "BTC/USDT:USDT",
            "volume_24h": 1234567890,
            "spread_pct": 0.01,
            "price": 65000.0,
            "scenario": "uptrend" | "downtrend" | "pump" | "new_listing" | None,
            "trend_strength": float,
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
            if vol < MIN_VOLUME_24H:
                continue

            bid = ticker.get("bid") or 0
            ask = ticker.get("ask") or 0
            if bid <= 0 or ask <= 0:
                continue
            spread_pct = (ask - bid) / bid * 100
            if spread_pct > MAX_SPREAD_PCT:
                continue

            candidates.append({
                "symbol": symbol,
                "volume_24h": vol,
                "spread_pct": spread_pct,
                "price": ticker.get("last") or ticker.get("close") or 0,
                "change_pct": ticker.get("percentage") or 0,
                "scenario": None,
                "trend_strength": 0,
            })

        # Sort by volume descending, take top N
        candidates.sort(key=lambda c: c["volume_24h"], reverse=True)
        candidates = candidates[:TOP_COINS_TO_SCAN]

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

        candidate["scenario"] = None
        return candidate
