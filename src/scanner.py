"""
Main Scanner — orchestrates the full DLS signal pipeline.

Flow:
  1. Screen coins (volume, spread, trend)
  2. Classify scenario (uptrend, downtrend, pump, new_listing)
  3. Fetch OHLCV on multiple timeframes
  4. Detect diagonal levels on working TF
  5. Compute indicators
  6. Evaluate Triple Confirmation for each level
  7. Send signals via Telegram
"""

import time
import logging

from src.config import (
    SCAN_INTERVAL_SECONDS, TF_CONTEXT, TF_DIRECTION,
    TF_WORKING, TF_ENTRY, CANDLE_LIMIT,
)
from src.data_fetcher import DataFetcher
from src.screener import CoinScreener
from src.indicators import compute_indicators, get_trend_direction
from src.diagonal_levels import detect_diagonal_levels
from src.signal_engine import evaluate_signal
from src.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class DLSScanner:
    """Main scanner that runs the DLS strategy pipeline."""

    def __init__(self):
        self.fetcher = DataFetcher()
        self.screener = CoinScreener(self.fetcher)
        self.notifier = TelegramNotifier()
        self._sent_signals: set[str] = set()  # Dedup: "symbol_direction_level_ts"

    def _signal_key(self, symbol: str, direction: str, level_price: float) -> str:
        """Create a dedup key for a signal (valid for ~15 min)."""
        ts_bucket = int(time.time() / 900)  # 15-min buckets
        price_bucket = round(level_price, 2)
        return f"{symbol}_{direction}_{price_bucket}_{ts_bucket}"

    def scan_once(self):
        """Run one full scan cycle."""
        logger.info("=" * 60)
        logger.info("Starting scan cycle...")

        # Step 1: Screen coins
        candidates = self.screener.get_candidates()
        if not candidates:
            logger.info("No candidates found")
            return

        signals_found = 0

        for cand in candidates:
            symbol = cand["symbol"]
            try:
                self._process_candidate(cand)
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                continue

        logger.info(f"Scan cycle complete. Signals: {signals_found}")

    def _process_candidate(self, candidate: dict):
        """Process a single coin candidate through the full pipeline."""
        symbol = candidate["symbol"]

        # Step 2: Fetch 1H data and classify scenario
        df_1h = self.fetcher.fetch_ohlcv(symbol, TF_DIRECTION, limit=CANDLE_LIMIT)
        if df_1h is None or len(df_1h) < 60:
            return

        df_1h_ind = compute_indicators(df_1h)
        candidate = self.screener.classify_scenario(candidate, df_1h_ind)

        scenario = candidate["scenario"]
        if scenario is None:
            return  # Not tradeable

        logger.info(f"  {symbol}: scenario={scenario}, strength={candidate['trend_strength']:.4f}")

        # Step 3: Fetch working TF data
        df_working = self.fetcher.fetch_ohlcv(symbol, TF_WORKING, limit=CANDLE_LIMIT)
        if df_working is None or len(df_working) < 60:
            return

        df_working_ind = compute_indicators(df_working)
        if df_working_ind is None:
            return

        # Step 4: Fetch entry TF data
        df_entry = self.fetcher.fetch_ohlcv(symbol, TF_ENTRY, limit=50)

        # Step 5: Fetch context TF for trend
        df_4h = self.fetcher.fetch_ohlcv(symbol, TF_CONTEXT, limit=CANDLE_LIMIT)
        df_4h_ind = compute_indicators(df_4h) if df_4h is not None else None
        trend_4h = get_trend_direction(df_4h_ind) if df_4h_ind is not None else "neutral"
        trend_1h = get_trend_direction(df_1h_ind) if df_1h_ind is not None else "neutral"

        # Step 6: Detect diagonal levels
        levels = detect_diagonal_levels(df_working_ind, TF_WORKING)
        if not levels:
            return

        logger.info(f"  {symbol}: found {len(levels)} diagonal levels")

        # Step 7: Evaluate signals for each level
        for level in levels[:5]:  # Check top 5 strongest levels
            signal = evaluate_signal(
                symbol=symbol,
                scenario=scenario,
                level=level,
                df_working=df_working_ind,
                df_entry=df_entry,
                trend_4h=trend_4h,
                trend_1h=trend_1h,
                timeframe=TF_WORKING,
            )

            if signal is None:
                continue

            # Dedup check
            key = self._signal_key(symbol, signal.direction, signal.entry_price)
            if key in self._sent_signals:
                continue

            # Send signal!
            logger.info(
                f"  >>> SIGNAL: {signal.direction} {symbol} "
                f"@ {signal.entry_price} | {signal.num_confirmations}/5 conf"
            )
            self.notifier.send_signal_sync(signal)
            self._sent_signals.add(key)

        # Cleanup old dedup keys (keep last 1000)
        if len(self._sent_signals) > 1000:
            self._sent_signals = set(list(self._sent_signals)[-500:])

    def run(self):
        """Run the scanner in a continuous loop."""
        logger.info("DLS Scanner started")
        self.notifier.send_status_sync(
            "\U0001f680 <b>DLS Scanner \u0437\u0430\u043f\u0443\u0449\u0435\u043d</b>\n\n"
            "\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043a\u0430\u0436\u0434\u044b\u0435 "
            f"{SCAN_INTERVAL_SECONDS} \u0441\u0435\u043a.\n"
            "\u0421\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u044f: Diagonal Level Strategy\n"
            "\u0411\u0438\u0440\u0436\u0430: Bybit USDT-M Perpetual"
        )

        while True:
            try:
                self.scan_once()
            except KeyboardInterrupt:
                logger.info("Scanner stopped by user")
                self.notifier.send_status_sync("\u23f9 <b>DLS Scanner \u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d</b>")
                break
            except Exception as e:
                logger.error(f"Scan cycle error: {e}")

            logger.info(f"Sleeping {SCAN_INTERVAL_SECONDS}s until next cycle...")
            time.sleep(SCAN_INTERVAL_SECONDS)
