"""
Main Scanner — orchestrates the full DLS signal pipeline.

Enhanced with:
  - Risk tracking (daily/weekly limits, position management)
  - Trade journal (SQLite logging)
  - Market regime filter (volatility, sessions, macro events)
  - Derivatives context (funding rate, OI)
  - Performance dashboard (periodic stats)

Flow:
  1. Check risk limits (can we trade?)
  2. Check market regime (should we trade?)
  3. Screen coins (volume, spread, trend)
  4. Classify scenario (uptrend, downtrend, pump, new_listing)
  5. Fetch OHLCV on multiple timeframes
  6. Detect diagonal levels on working TF
  7. Compute indicators
  8. Fetch funding rate + OI (derivatives context)
  9. Evaluate Triple Confirmation + confluence + derivatives
  10. Log to journal
  11. Send signals via Telegram
  12. Update dashboard
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
from src.risk_tracker import RiskTracker
from src.trade_journal import TradeJournal
from src.market_regime import analyze_market_regime
from src.performance import PerformanceDashboard

logger = logging.getLogger(__name__)


class DLSScanner:
    """Main scanner that runs the DLS strategy pipeline."""

    def __init__(self):
        self.fetcher = DataFetcher()
        self.screener = CoinScreener(self.fetcher)
        self.notifier = TelegramNotifier()
        self.risk_tracker = RiskTracker()
        self.journal = TradeJournal()
        self.dashboard = PerformanceDashboard(
            self.notifier, self.journal, self.risk_tracker
        )
        self._sent_signals: set[str] = set()
        self._scan_count = 0

    def _signal_key(self, symbol: str, direction: str, level_price: float) -> str:
        """Create a dedup key for a signal (valid for ~15 min)."""
        ts_bucket = int(time.time() / 900)
        price_bucket = round(level_price, 2)
        return f"{symbol}_{direction}_{price_bucket}_{ts_bucket}"

    def scan_once(self):
        """Run one full scan cycle."""
        self._scan_count += 1
        logger.info("=" * 60)
        logger.info(f"Starting scan cycle #{self._scan_count}...")

        # Step 0: Check risk limits
        risk_state = self.risk_tracker.check_can_trade()
        if not risk_state.can_trade:
            logger.warning(f"Trading blocked: {risk_state.reason}")
            if self._scan_count % 10 == 0:  # Remind every 10 cycles
                self.notifier.send_status_sync(
                    f"\U0001f6d1 <b>Торговля заблокирована</b>\n{risk_state.reason}"
                )
            return

        # Step 1: Screen coins
        candidates = self.screener.get_candidates()
        if not candidates:
            logger.info("No candidates found")
            return

        # Step 1.5: Fetch BTC for market regime
        df_btc = self.fetcher.fetch_ohlcv("BTC/USDT:USDT", TF_WORKING, limit=50)

        signals_found = 0

        for cand in candidates:
            symbol = cand["symbol"]
            try:
                found = self._process_candidate(cand, df_btc)
                if found:
                    signals_found += found
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                continue

        logger.info(f"Scan cycle #{self._scan_count} complete. Signals: {signals_found}")

        # Step 12: Dashboard check
        regime_info = ""
        if df_btc is not None:
            regime = analyze_market_regime(df_btc)
            regime_info = " | ".join(regime.reasons) if regime.reasons else ""
        self.dashboard.check_and_send(regime_info)

    def _process_candidate(self, candidate: dict, df_btc=None) -> int:
        """Process a single coin candidate. Returns number of signals sent."""
        symbol = candidate["symbol"]
        signals_sent = 0

        # Fetch 1H data and classify scenario
        df_1h = self.fetcher.fetch_ohlcv(symbol, TF_DIRECTION, limit=CANDLE_LIMIT)
        if df_1h is None or len(df_1h) < 60:
            return 0

        df_1h_ind = compute_indicators(df_1h)
        candidate = self.screener.classify_scenario(candidate, df_1h_ind)

        scenario = candidate["scenario"]
        if scenario is None:
            return 0

        logger.info(f"  {symbol}: scenario={scenario}, strength={candidate['trend_strength']:.4f}")

        # Fetch working TF data
        df_working = self.fetcher.fetch_ohlcv(symbol, TF_WORKING, limit=CANDLE_LIMIT)
        if df_working is None or len(df_working) < 60:
            return 0

        df_working_ind = compute_indicators(df_working)
        if df_working_ind is None:
            return 0

        # Fetch entry TF data
        df_entry = self.fetcher.fetch_ohlcv(symbol, TF_ENTRY, limit=50)

        # Fetch context TF for trend
        df_4h = self.fetcher.fetch_ohlcv(symbol, TF_CONTEXT, limit=CANDLE_LIMIT)
        df_4h_ind = compute_indicators(df_4h) if df_4h is not None else None
        trend_4h = get_trend_direction(df_4h_ind) if df_4h_ind is not None else "neutral"
        trend_1h = get_trend_direction(df_1h_ind) if df_1h_ind is not None else "neutral"

        # Market regime check
        regime = analyze_market_regime(df_working_ind, df_btc)
        if not regime.can_trade:
            logger.info(f"  {symbol}: market regime blocks trading — {regime.reasons}")
            return 0

        # Fetch derivatives data
        funding_rate = self.fetcher.fetch_funding_rate(symbol)
        open_interest = self.fetcher.fetch_open_interest(symbol)

        # Detect diagonal levels
        levels = detect_diagonal_levels(df_working_ind, TF_WORKING)
        if not levels:
            return 0

        logger.info(f"  {symbol}: found {len(levels)} diagonal levels")

        # Evaluate signals for each level
        for level in levels[:5]:
            signal = evaluate_signal(
                symbol=symbol,
                scenario=scenario,
                level=level,
                df_working=df_working_ind,
                df_entry=df_entry,
                trend_4h=trend_4h,
                trend_1h=trend_1h,
                timeframe=TF_WORKING,
                funding_rate=funding_rate,
                open_interest=open_interest,
                regime_adjustment=regime.score_adjustment,
            )

            if signal is None:
                continue

            # Dedup check
            key = self._signal_key(symbol, signal.direction, signal.entry_price)
            if key in self._sent_signals:
                continue

            # Re-check risk (may have changed since cycle start)
            risk_state = self.risk_tracker.check_can_trade()
            if not risk_state.can_trade:
                logger.warning(f"  {symbol}: risk limit hit during scan — {risk_state.reason}")
                break

            # Log to journal
            journal_id = self.journal.log_signal(
                signal,
                confluence_score=signal.confluence.score,
                confluence_factors=signal.confluence.factors,
                funding_rate=funding_rate,
                derivatives_bias=signal.derivatives.funding_bias,
                pump_analysis=signal.pump_analysis.reason if signal.pump_analysis.is_pump else "",
            )

            # Track in risk manager
            self.risk_tracker.record_signal(signal)

            # Send signal!
            logger.info(
                f"  >>> SIGNAL [{signal.signal_grade}]: {signal.direction} {symbol} "
                f"@ {signal.entry_price} | {signal.num_confirmations}/5 conf "
                f"| score {signal.composite_score:.0f}"
            )
            self.notifier.send_signal_sync(signal)
            self._sent_signals.add(key)
            signals_sent += 1

        # Cleanup old dedup keys
        if len(self._sent_signals) > 1000:
            self._sent_signals = set(list(self._sent_signals)[-500:])

        return signals_sent

    def run(self):
        """Run the scanner in a continuous loop."""
        logger.info("DLS Scanner started (Enhanced v2.0)")
        self.notifier.send_status_sync(
            "\U0001f680 <b>DLS Scanner v2.0 \u0437\u0430\u043f\u0443\u0449\u0435\u043d</b>\n\n"
            "\u2705 \u041d\u043e\u0432\u044b\u0435 \u043c\u043e\u0434\u0443\u043b\u0438:\n"
            "  \u2022 \u041a\u043e\u043d\u0444\u043b\u044e\u0435\u043d\u0446\u0438\u044f (Fib + \u0433\u043e\u0440\u0438\u0437\u043e\u043d\u0442\u0430\u043b\u044c\u043d\u044b\u0435 + VPOC)\n"
            "  \u2022 \u0424\u0430\u043d\u0434\u0438\u043d\u0433 + OI \u0444\u0438\u043b\u044c\u0442\u0440\n"
            "  \u2022 \u041f\u0430\u043c\u043f-\u0442\u0440\u0435\u0439\u0434\u0438\u043d\u0433 (FVG + \u0441\u0434\u0443\u0442\u0438\u0435)\n"
            "  \u2022 \u0420\u0438\u0441\u043a-\u0442\u0440\u0435\u043a\u0435\u0440 (\u0434\u043d\u0435\u0432\u043d\u044b\u0435/\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u044b\u0435 \u043b\u0438\u043c\u0438\u0442\u044b)\n"
            "  \u2022 \u0416\u0443\u0440\u043d\u0430\u043b \u0441\u0434\u0435\u043b\u043e\u043a (SQLite)\n"
            "  \u2022 \u041c\u0430\u0440\u043a\u0435\u0442-\u0440\u0435\u0436\u0438\u043c (\u0432\u043e\u043b\u0430\u0442\u0438\u043b\u044c\u043d\u043e\u0441\u0442\u044c + \u0441\u0435\u0441\u0441\u0438\u0438 + \u043c\u0430\u043a\u0440\u043e)\n"
            "  \u2022 \u041a\u043e\u043c\u043f\u043e\u0437\u0438\u0442\u043d\u044b\u0439 \u0441\u043a\u043e\u0440\u0438\u043d\u0433 (A+/A/B/C)\n"
            "\n"
            f"\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043a\u0430\u0436\u0434\u044b\u0435 {SCAN_INTERVAL_SECONDS} \u0441\u0435\u043a.\n"
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
