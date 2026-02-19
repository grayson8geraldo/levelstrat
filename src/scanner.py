"""
Main Scanner — orchestrates the full DLS signal pipeline.

Enhanced with:
  - Risk tracking (daily/weekly limits, position management)
  - Trade journal (SQLite logging)
  - Market regime filter (volatility, sessions, macro events)
  - Derivatives context (funding rate, OI)
  - Performance dashboard (periodic stats)
  - Signal ranking: collects ALL signals, sends only the BEST by score

Flow:
  1. Check risk limits (can we trade?)
  2. Check market regime (should we trade?)
  3. Screen coins (volume, spread, trend)
  4. Classify scenario (uptrend, downtrend, ranging, pump, new_listing)
  5. Fetch OHLCV on multiple timeframes
  6. Detect diagonal levels on working TF
  7. Compute indicators
  8. Fetch funding rate + OI (derivatives context)
  9. Evaluate Triple Confirmation + confluence + derivatives
  10. Collect all valid signals → sort by composite score
  11. Send top N signals (N = available position slots)
  12. Log to journal, send via Telegram with close buttons
  13. Update dashboard
"""

import time
import logging

from src.config import (
    SCAN_INTERVAL_SECONDS, TF_CONTEXT, TF_DIRECTION,
    TF_WORKING, TF_ENTRY, CANDLE_LIMIT,
    MAX_LEVELS_PER_COIN, API_DELAY_BETWEEN_COINS,
    MAX_OPEN_POSITIONS, MAX_ENTRY_DISTANCE_PCT,
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

        # Connect notifier to risk tracker for button callbacks
        self.notifier.set_risk_tracker(self.risk_tracker)

    def _signal_key(self, symbol: str, direction: str, level_price: float) -> str:
        """Create a dedup key for a signal (valid for ~15 min)."""
        ts_bucket = int(time.time() / 900)
        price_bucket = round(level_price, 2)
        return f"{symbol}_{direction}_{price_bucket}_{ts_bucket}"

    def scan_once(self):
        """Run one full scan cycle. Collects ALL signals, sends BEST."""
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

        # Step 2: Collect ALL valid signals from all candidates
        all_signals = []

        for i, cand in enumerate(candidates):
            symbol = cand["symbol"]
            try:
                signals = self._collect_signals(cand, df_btc)
                all_signals.extend(signals)
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                continue

            # Rate limit protection: small delay between coins
            if API_DELAY_BETWEEN_COINS > 0 and i < len(candidates) - 1:
                time.sleep(API_DELAY_BETWEEN_COINS)

        # Step 3: Sort by composite score (best first)
        all_signals.sort(key=lambda s: s.composite_score, reverse=True)

        # Step 4: Send top N signals (limited by available slots)
        open_count = self.risk_tracker.get_open_count()
        available_slots = max(0, MAX_OPEN_POSITIONS - open_count)

        signals_sent = 0
        for signal in all_signals:
            if signals_sent >= available_slots:
                break

            # Dedup check
            key = self._signal_key(signal.symbol, signal.direction, signal.entry_price)
            if key in self._sent_signals:
                continue

            # Send the signal (may be skipped if price moved too far)
            if self._send_signal(signal):
                self._sent_signals.add(key)
                signals_sent += 1

        # Log results
        if all_signals:
            logger.info(
                f"Scan cycle #{self._scan_count}: "
                f"found {len(all_signals)} signals, "
                f"sent top {signals_sent} (slots: {available_slots})"
            )
            # Log skipped signals for transparency
            for i, s in enumerate(all_signals):
                status = "SENT" if i < signals_sent else "skipped"
                logger.info(
                    f"  #{i+1} [{status}] {s.signal_grade} {s.direction} "
                    f"{s.symbol} score={s.composite_score:.0f}"
                )
        else:
            logger.info(f"Scan cycle #{self._scan_count} complete. No signals found.")

        # Cleanup old dedup keys
        if len(self._sent_signals) > 1000:
            self._sent_signals = set(list(self._sent_signals)[-500:])

        # Step 5: Dashboard check
        regime_info = ""
        if df_btc is not None:
            regime = analyze_market_regime(df_btc)
            regime_info = " | ".join(regime.reasons) if regime.reasons else ""
        self.dashboard.check_and_send(regime_info)

    def _collect_signals(self, candidate: dict, df_btc=None) -> list:
        """
        Process a single coin candidate.
        Returns list of valid Signal objects (not yet sent).
        """
        symbol = candidate["symbol"]
        signals = []

        # Fetch 1H data and classify scenario
        df_1h = self.fetcher.fetch_ohlcv(symbol, TF_DIRECTION, limit=CANDLE_LIMIT)
        if df_1h is None or len(df_1h) < 60:
            return []

        df_1h_ind = compute_indicators(df_1h)
        candidate = self.screener.classify_scenario(candidate, df_1h_ind)

        scenario = candidate["scenario"]
        if scenario is None:
            return []

        logger.info(f"  {symbol}: scenario={scenario}, strength={candidate['trend_strength']:.4f}")

        # Fetch working TF data
        df_working = self.fetcher.fetch_ohlcv(symbol, TF_WORKING, limit=CANDLE_LIMIT)
        if df_working is None or len(df_working) < 60:
            return []

        df_working_ind = compute_indicators(df_working)
        if df_working_ind is None:
            return []

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
            return []

        # Fetch derivatives data
        funding_rate = self.fetcher.fetch_funding_rate(symbol)
        open_interest = self.fetcher.fetch_open_interest(symbol)

        # Detect diagonal levels
        levels = detect_diagonal_levels(df_working_ind, TF_WORKING)
        if not levels:
            return []

        logger.info(f"  {symbol}: found {len(levels)} diagonal levels")

        # Evaluate signals for each level
        for level in levels[:MAX_LEVELS_PER_COIN]:
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

            # Attach funding rate for journal logging
            signal._funding_rate = funding_rate
            signals.append(signal)

        return signals

    def _send_signal(self, signal) -> bool:
        """Log to journal, track in risk manager, send via Telegram.
        Returns True if signal was actually sent."""
        # Freshness check: re-fetch current price, skip if too far from entry
        try:
            ticker = self.fetcher.exchange.fetch_ticker(signal.symbol)
            live_price = ticker.get("last", 0)
            if live_price and signal.entry_price:
                distance = abs(live_price - signal.entry_price) / signal.entry_price
                if distance > MAX_ENTRY_DISTANCE_PCT:
                    logger.info(
                        f"  {signal.symbol}: пропущен — цена {live_price:.6f} "
                        f"ушла на {distance:.2%} от входа {signal.entry_price}"
                    )
                    return False
                # Update current_price with live data
                signal.current_price = round(live_price, 6)
        except Exception as e:
            logger.debug(f"  {signal.symbol}: не удалось проверить цену: {e}")

        funding_rate = getattr(signal, '_funding_rate', None)

        # Log to journal
        self.journal.log_signal(
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
            f"  >>> SIGNAL [{signal.signal_grade}]: {signal.direction} {signal.symbol} "
            f"@ {signal.entry_price} | {signal.num_confirmations}/5 conf "
            f"| score {signal.composite_score:.0f}"
        )
        self.notifier.send_signal_sync(signal)
        return True

    def run(self):
        """Run the scanner in a continuous loop."""
        logger.info("DLS Scanner started (Enhanced v2.1)")

        # Start Telegram callback listener for close buttons
        self.notifier.start_callback_listener()

        self.notifier.send_status_sync(
            "\U0001f680 <b>DLS Scanner v2.1 запущен</b>\n\n"
            "\u2705 Возможности:\n"
            "  \u2022 Ранжирование сигналов (лучшие первые)\n"
            "  \u2022 Кнопки закрытия позиций в Telegram\n"
            "  \u2022 Конфлюенция (Fib + горизонтальные + VPOC)\n"
            "  \u2022 Фандинг + OI фильтр\n"
            "  \u2022 Риск-трекер (дневные/недельные лимиты)\n"
            "  \u2022 Композитный скоринг (A+/A/B/C)\n"
            "\n"
            f"Макс. позиций: {MAX_OPEN_POSITIONS}\n"
            f"Сканирование каждые {SCAN_INTERVAL_SECONDS} сек.\n"
            "Биржа: Bybit USDT-M Perpetual"
        )

        while True:
            try:
                self.scan_once()
            except KeyboardInterrupt:
                logger.info("Scanner stopped by user")
                self.notifier.send_status_sync("\u23f9 <b>DLS Scanner остановлен</b>")
                break
            except Exception as e:
                logger.error(f"Scan cycle error: {e}")

            logger.info(f"Sleeping {SCAN_INTERVAL_SECONDS}s until next cycle...")
            time.sleep(SCAN_INTERVAL_SECONDS)
