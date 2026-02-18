"""
Data Fetcher — Bybit OHLCV + market data via ccxt.

Features:
  - Exponential backoff retry on failures
  - Rate limit awareness
  - Funding rate and open interest fetching
"""

import time
import ccxt
import pandas as pd
import logging
from typing import Optional
from functools import wraps

from src.config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET, CANDLE_LIMIT,
    API_MAX_RETRIES, API_RETRY_BASE_DELAY,
)

logger = logging.getLogger(__name__)


def retry_on_failure(max_retries: int = API_MAX_RETRIES, base_delay: float = API_RETRY_BASE_DELAY):
    """Decorator for exponential backoff retry on API failures."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (ccxt.NetworkError, ccxt.ExchangeNotAvailable,
                        ccxt.RequestTimeout, ccxt.DDoSProtection) as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                            f"Retrying in {delay}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_retries + 1} attempts: {e}"
                        )
                except ccxt.RateLimitExceeded as e:
                    last_exception = e
                    delay = base_delay * (2 ** (attempt + 1))
                    logger.warning(f"Rate limited on {func.__name__}, waiting {delay}s")
                    time.sleep(delay)
                except Exception as e:
                    logger.error(f"{func.__name__} error: {e}")
                    return None
            return None
        return wrapper
    return decorator


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

    @retry_on_failure()
    def load_markets(self):
        """Load and cache markets."""
        if self._markets is None:
            self._markets = self.exchange.load_markets()
        return self._markets

    def get_usdt_perpetuals(self) -> list[dict]:
        """Return all active USDT-margined perpetual futures."""
        markets = self.load_markets()
        if not markets:
            return []
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

    @retry_on_failure()
    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = CANDLE_LIMIT
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles and return as DataFrame.

        Columns: timestamp, open, high, low, close, volume
        """
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

    @retry_on_failure()
    def fetch_ticker(self, symbol: str) -> Optional[dict]:
        """Fetch current ticker (price, volume, spread)."""
        return self.exchange.fetch_ticker(symbol)

    @retry_on_failure()
    def fetch_tickers_batch(self, symbols: list[str]) -> dict:
        """Fetch tickers for multiple symbols at once."""
        result = self.exchange.fetch_tickers(symbols)
        return result if result else {}

    @retry_on_failure()
    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """Fetch current funding rate for a symbol."""
        funding = self.exchange.fetch_funding_rate(symbol)
        return funding.get("fundingRate")

    @retry_on_failure()
    def fetch_open_interest(self, symbol: str) -> Optional[float]:
        """Fetch current open interest for a symbol."""
        oi = self.exchange.fetch_open_interest(symbol)
        return oi.get("openInterestValue") or oi.get("openInterestAmount")

    @retry_on_failure()
    def fetch_ohlcv_extended(
        self, symbol: str, timeframe: str, limit: int = 500
    ) -> Optional[pd.DataFrame]:
        """Fetch extended OHLCV data for backtesting (multiple API calls if needed)."""
        if limit <= 200:
            return self.fetch_ohlcv(symbol, timeframe, limit)

        all_data = []
        remaining = limit
        since = None

        while remaining > 0:
            batch_limit = min(remaining, 200)
            raw = self.exchange.fetch_ohlcv(
                symbol, timeframe, since=since, limit=batch_limit
            )
            if not raw:
                break

            all_data.extend(raw)
            remaining -= len(raw)

            if len(raw) < batch_limit:
                break

            since = raw[-1][0] + 1

        if not all_data:
            return None

        df = pd.DataFrame(
            all_data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        df = df[~df.index.duplicated(keep="first")]
        return df
