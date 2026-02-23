"""
Telegram Notifier — sends trade signals to Telegram.

Enhanced with:
  - Signal grade (A+/A/B/C/D)
  - Composite score display
  - Confluence factors breakdown
  - Derivatives context (funding rate, bias)
  - Pump analysis details
  - Inline buttons for closing positions (Profit/Loss/Breakeven)
  - Background callback listener for button presses
"""

import asyncio
import json
import logging
import threading
import time
import urllib.request
import urllib.error

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.signal_engine import Signal

logger = logging.getLogger(__name__)

DIRECTION_EMOJI = {"LONG": "\U0001f7e2", "SHORT": "\U0001f534"}
SCENARIO_LABELS = {
    "uptrend": "\u2191 Восходящий тренд",
    "downtrend": "\u2193 Нисходящий тренд",
    "ranging": "\u2194\ufe0f Флэт (от уровней)",
    "pump": "\u26a1 Памп (сдутие)",
    "new_listing": "\u2728 Новый листинг",
}
CONFIRM_ICON = {True: "\u2705", False: "\u274c"}

GRADE_EMOJI = {
    "A+": "\U0001f31f",  # Star
    "A": "\U0001f7e2",   # Green circle
    "B": "\U0001f7e1",   # Yellow circle
    "C": "\U0001f7e0",   # Orange circle
    "D": "\U0001f534",   # Red circle
}

OUTCOME_LABELS = {
    "win": "\u2705 ПРОФИТ",
    "loss": "\u274c ЛОСС",
    "be": "\u2194\ufe0f БЕЗУБЫТОК",
}


def format_signal(signal: Signal) -> str:
    """Format a Signal into a rich Telegram message."""
    dir_emoji = DIRECTION_EMOJI.get(signal.direction, "")
    scenario_label = SCENARIO_LABELS.get(signal.scenario, signal.scenario)
    grade_emoji = GRADE_EMOJI.get(signal.signal_grade, "")

    conf = signal.confirmations
    conf_lines = (
        f"  {CONFIRM_ICON[conf.get('diagonal_level', False)]} Наклонный уровень ({signal.level_touches} касаний)\n"
        f"  {CONFIRM_ICON[conf.get('candle_pattern', False)]} Свечной паттерн"
        f"{': ' + signal.candle_pattern if signal.candle_pattern else ''}\n"
        f"  {CONFIRM_ICON[conf.get('volume', False)]} Объём (x{signal.volume_ratio})\n"
        f"  {CONFIRM_ICON[conf.get('indicator', False)]} Индикатор (RSI: {signal.rsi_value})\n"
        f"  {CONFIRM_ICON[conf.get('confluence', False)]} Конфлюенция"
    )

    # Confluence details
    confluence_detail = ""
    if signal.confluence and signal.confluence.factors:
        factors = ", ".join(signal.confluence.factors)
        confluence_detail = f"\n\U0001f4cd <b>Конфлюенция:</b> {factors}"

    # Derivatives context
    derivatives_detail = ""
    if signal.derivatives and signal.derivatives.reason:
        fund_icon = "\u26a0\ufe0f" if signal.derivatives.is_warning else "\u2705"
        derivatives_detail = f"\n{fund_icon} <b>Фандинг:</b> {signal.derivatives.reason}"

    # Pump info
    pump_detail = ""
    if signal.pump_analysis and signal.pump_analysis.is_pump:
        pump_detail = f"\n\u26a1 <b>Памп:</b> {signal.pump_analysis.reason}"

    # Size warning
    size_label = ""
    if signal.size_multiplier < 1.0:
        pct = int(signal.size_multiplier * 100)
        size_label = f"\n\u26a0\ufe0f Размер позиции: {pct}% от стандарта"

    # Score bar
    score = signal.composite_score
    bar_filled = int(score / 10)
    bar_empty = 10 - bar_filled
    score_bar = "\u2588" * bar_filled + "\u2591" * bar_empty

    msg = (
        f"{dir_emoji} <b>{signal.direction} {signal.symbol}</b>  "
        f"{grade_emoji} <b>Grade: {signal.signal_grade}</b>\n"
        f"\n"
        f"\U0001f3af <b>Сценарий:</b> {scenario_label}\n"
        f"\U0001f4ca <b>Подтверждения:</b> {signal.num_confirmations}/5\n"
        f"{conf_lines}\n"
        f"\n"
        f"\U0001f4af <b>Скор:</b> [{score_bar}] {score:.0f}/100\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4b0 <b>Вход:</b>    {signal.entry_price}\n"
        f"\U0001f4cf <b>Уровень:</b>  {signal.level_price}\n"
        f"\U0001f6d1 <b>Стоп:</b>     {signal.stop_loss}  ({signal.risk_pct:.2%})\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f3c1 <b>TP1 (30%):</b> {signal.tp1}\n"
        f"\U0001f3c1 <b>TP2 (40%):</b> {signal.tp2}\n"
        f"\U0001f3c1 <b>TP3 (30%):</b> {signal.tp3}\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4c8 R:R = 1:{signal.rr_ratio} (нетто 1:{signal.rr_net})\n"
        f"\U0001f4aa Уровень: {signal.level_strength:.0f}/100 "
        f"(угол {signal.level_angle}\u00b0)\n"
        f"\u2696\ufe0f Плечо: {signal.leverage}x"
        f"{size_label}\n"
        f"{confluence_detail}"
        f"{derivatives_detail}"
        f"{pump_detail}\n"
        f"\n"
        f"\U0001f5c2 <b>Тренды:</b> 4H={signal.trend_4h} | "
        f"1H={signal.trend_1h} | 15m={signal.trend_15m}\n"
        f"\u23f0 {signal.timestamp}"
    )
    return msg


class TelegramNotifier:
    """Sends formatted signals to Telegram with inline close buttons."""

    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
        self.chat_id = TELEGRAM_CHAT_ID
        self._signal_counter = 0
        self._pending_signals = {}   # signal_id -> {"symbol", "direction"}
        self._risk_tracker = None    # Set by scanner
        self._position_monitor = None  # Set by scanner
        self._listener_running = False

    def set_risk_tracker(self, risk_tracker):
        """Set the risk tracker for callback updates."""
        self._risk_tracker = risk_tracker

    def set_position_monitor(self, position_monitor):
        """Set the position monitor for callback updates."""
        self._position_monitor = position_monitor

    # ── Callback listener (background thread) ────────────────

    def start_callback_listener(self):
        """Start background thread that listens for button presses."""
        if not TELEGRAM_BOT_TOKEN or self._listener_running:
            return

        self._listener_running = True
        t = threading.Thread(target=self._poll_callbacks, daemon=True)
        t.start()
        logger.info("Telegram callback listener started")

    def _poll_callbacks(self):
        """Long-poll Telegram for callback_query updates."""
        offset = 0
        base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

        while True:
            try:
                payload = json.dumps({
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": ["callback_query"],
                }).encode()
                req = urllib.request.Request(
                    f"{base}/getUpdates",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=35) as resp:
                    data = json.loads(resp.read())

                if data.get("ok"):
                    for update in data["result"]:
                        offset = update["update_id"] + 1
                        if "callback_query" in update:
                            self._process_callback(update["callback_query"])

            except (urllib.error.URLError, TimeoutError, OSError) as e:
                logger.debug(f"Callback poll network error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.warning(f"Callback poll error: {e}")
                time.sleep(5)

    def _process_callback(self, callback_query: dict):
        """Handle a button press from Telegram."""
        cb_data = callback_query.get("data", "")
        query_id = callback_query["id"]
        message = callback_query.get("message", {})
        message_id = message.get("message_id")
        chat_id = message.get("chat", {}).get("id")
        original_text = message.get("text", "")

        parts = cb_data.split("_", 1)
        if len(parts) != 2:
            return

        outcome_key = parts[0]
        try:
            signal_id = int(parts[1])
        except ValueError:
            return

        outcome_map = {"win": "win", "loss": "loss", "be": "breakeven"}
        outcome = outcome_map.get(outcome_key)
        label = OUTCOME_LABELS.get(outcome_key, outcome_key)

        # Update risk tracker + position monitor
        info = self._pending_signals.pop(signal_id, None)
        if info and outcome:
            symbol, direction = info["symbol"], info["direction"]

            if self._risk_tracker:
                self._risk_tracker.record_outcome(symbol, direction, outcome)

            if self._position_monitor:
                self._position_monitor.close_position_manual(symbol, direction, outcome)

            logger.info(f"Позиция закрыта: {symbol} {direction} -> {label}")

        # Answer callback (removes loading spinner on button)
        self._tg_api("answerCallbackQuery", {
            "callback_query_id": query_id,
            "text": label,
        })

        # Edit message: remove buttons, add result
        new_text = original_text + f"\n\n{'═' * 26}\n{label}"
        self._tg_api("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "HTML",
        })

    def _tg_api(self, method: str, params: dict) -> dict | None:
        """Call Telegram Bot API method via HTTP."""
        try:
            base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
            payload = json.dumps(params).encode()
            req = urllib.request.Request(
                f"{base}/{method}",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"TG API {method} error: {e}")
            return None

    # ── Send methods ─────────────────────────────────────────

    def _make_close_keyboard(self, signal_id: int) -> InlineKeyboardMarkup:
        """Create inline keyboard with close position buttons."""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\u2705 Профит", callback_data=f"win_{signal_id}"),
                InlineKeyboardButton("\u274c Лосс", callback_data=f"loss_{signal_id}"),
                InlineKeyboardButton("\u2194\ufe0f Б/У", callback_data=f"be_{signal_id}"),
            ]
        ])

    async def send_signal(self, signal: Signal, is_update: bool = False):
        """Send a trade signal to Telegram with close buttons.

        If is_update=True, this is an update to an existing position —
        adds UPDATE label and skips close buttons (original signal has them).
        """
        msg = format_signal(signal)

        if is_update:
            # Prepend update label, no close buttons
            msg = "\U0001f504 <b>ОБНОВЛЕНИЕ СИГНАЛА</b>\n\n" + msg
            keyboard = None
        else:
            # New signal — register for callbacks and add close buttons
            self._signal_counter += 1
            sid = self._signal_counter
            self._pending_signals[sid] = {
                "symbol": signal.symbol,
                "direction": signal.direction,
            }
            keyboard = self._make_close_keyboard(sid)

        if not self.bot or not self.chat_id:
            logger.warning("Telegram not configured, printing to console only")
            print(msg)
            return

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            tag = "Update" if is_update else "Signal"
            logger.info(f"{tag} sent: {signal.direction} {signal.symbol} [{signal.signal_grade}]")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            print(msg)

    async def send_status(self, text: str):
        """Send a status/info message."""
        if not self.bot or not self.chat_id:
            print(text)
            return
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Failed to send status: {e}")

    def _get_loop(self):
        """Get or create an event loop for sync wrappers."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop

    def send_signal_sync(self, signal: Signal, is_update: bool = False):
        """Synchronous wrapper for send_signal."""
        try:
            self._get_loop().run_until_complete(self.send_signal(signal, is_update=is_update))
        except Exception as e:
            logger.error(f"send_signal_sync error: {e}")

    def send_status_sync(self, text: str):
        """Synchronous wrapper for send_status."""
        try:
            self._get_loop().run_until_complete(self.send_status(text))
        except Exception as e:
            logger.error(f"send_status_sync error: {e}")
