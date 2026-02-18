"""
Data Fetcher — Bybit OHLCV + market data via ccxt.
"""

import ccxt
import pandas as pd
import logging
from typing import Optional

from src.config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET, CANDLE_LIMIT
)

logger = logging.getLogger(__name__)


class DataFetcher:
    """Fetches OHLCV and market data from Bybit via ccxt."""

    def __init__(self):
        opts = {
            "apiKey": BYBIT_API_KEY,
            "secret": BYBIT_API_SECRET,
            "options": {"defaultType": "swap"},  # USDT-M perpetual
        }
        if BYBIT_TESTNET:
            opts["sandbox"] = True

        self.exchange = ccxt.bybit(opts)
        self._markets = None

    def load_markets(self):
        """Load and cache markets."""
        if self._markets is None:
            self._markets = self.exchange.load_markets()
        return self._markets

    def get_usdt_perpetuals(self) -> list[dict]:
        """Return all active USDT-margined perpetual futures."""
        markets = self.load_markets()
        result = []
        for symbol, info in markets.items():
            if (
                info.get("swap")
                and info.get("active")
                and info.get("settle") == "USDT"
                and info.get("linear")
            ):
                result.append(info)
        return result

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = CANDLE_LIMIT
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles and return as DataFrame.

        Columns: timestamp, open, high, low, close, volume
        """
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not raw:
                return None

            df = pd.DataFrame(
                raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            df = df.astype(float)
            return df

        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} {timeframe}: {e}")
            return None

    def fetch_ticker(self, symbol: str) -> Optional[dict]:
        """Fetch current ticker (price, volume, spread)."""
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            return None

    def fetch_tickers_batch(self, symbols: list[str]) -> dict:
        """Fetch tickers for multiple symbols at once."""
        try:
            return self.exchange.fetch_tickers(symbols)
        except Exception as e:
            logger.error(f"Error fetching batch tickers: {e}")
            return {}

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """Fetch current funding rate for a symbol."""
        try:
            funding = self.exchange.fetch_funding_rate(symbol)
            return funding.get("fundingRate")
        except Exception as e:
            logger.error(f"Error fetching funding rate for {symbol}: {e}")
            return None

    def fetch_open_interest(self, symbol: str) -> Optional[float]:
        """Fetch current open interest for a symbol."""
        try:
            oi = self.exchange.fetch_open_interest(symbol)
            return oi.get("openInterestValue") or oi.get("openInterestAmount")
        except Exception as e:
            logger.error(f"Error fetching OI for {symbol}: {e}")
            return None
