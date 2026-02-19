"""
Telegram Notifier — sends trade signals to Telegram.

Enhanced with:
  - Signal grade (A+/A/B/C/D)
  - Composite score display
  - Confluence factors breakdown
  - Derivatives context (funding rate, bias)
  - Pump analysis details
"""

import asyncio
import logging
from telegram import Bot
from telegram.constants import ParseMode

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from src.signal_engine import Signal

logger = logging.getLogger(__name__)

DIRECTION_EMOJI = {"LONG": "\U0001f7e2", "SHORT": "\U0001f534"}
SCENARIO_LABELS = {
    "uptrend": "\u2191 \u0412\u043e\u0441\u0445\u043e\u0434\u044f\u0449\u0438\u0439 \u0442\u0440\u0435\u043d\u0434",
    "downtrend": "\u2193 \u041d\u0438\u0441\u0445\u043e\u0434\u044f\u0449\u0438\u0439 \u0442\u0440\u0435\u043d\u0434",
    "ranging": "\u2194\ufe0f \u0424\u043b\u044d\u0442 (\u043e\u0442 \u0443\u0440\u043e\u0432\u043d\u0435\u0439)",
    "pump": "\u26a1 \u041f\u0430\u043c\u043f (\u0441\u0434\u0443\u0442\u0438\u0435)",
    "new_listing": "\u2728 \u041d\u043e\u0432\u044b\u0439 \u043b\u0438\u0441\u0442\u0438\u043d\u0433",
}
CONFIRM_ICON = {True: "\u2705", False: "\u274c"}

GRADE_EMOJI = {
    "A+": "\U0001f31f",  # Star
    "A": "\U0001f7e2",   # Green circle
    "B": "\U0001f7e1",   # Yellow circle
    "C": "\U0001f7e0",   # Orange circle
    "D": "\U0001f534",   # Red circle
}


def format_signal(signal: Signal) -> str:
    """Format a Signal into a rich Telegram message."""
    dir_emoji = DIRECTION_EMOJI.get(signal.direction, "")
    scenario_label = SCENARIO_LABELS.get(signal.scenario, signal.scenario)
    grade_emoji = GRADE_EMOJI.get(signal.signal_grade, "")

    conf = signal.confirmations
    conf_lines = (
        f"  {CONFIRM_ICON[conf.get('diagonal_level', False)]} \u041d\u0430\u043a\u043b\u043e\u043d\u043d\u044b\u0439 \u0443\u0440\u043e\u0432\u0435\u043d\u044c ({signal.level_touches} \u043a\u0430\u0441\u0430\u043d\u0438\u0439)\n"
        f"  {CONFIRM_ICON[conf.get('candle_pattern', False)]} \u0421\u0432\u0435\u0447\u043d\u043e\u0439 \u043f\u0430\u0442\u0442\u0435\u0440\u043d"
        f"{': ' + signal.candle_pattern if signal.candle_pattern else ''}\n"
        f"  {CONFIRM_ICON[conf.get('volume', False)]} \u041e\u0431\u044a\u0451\u043c (x{signal.volume_ratio})\n"
        f"  {CONFIRM_ICON[conf.get('indicator', False)]} \u0418\u043d\u0434\u0438\u043a\u0430\u0442\u043e\u0440 (RSI: {signal.rsi_value})\n"
        f"  {CONFIRM_ICON[conf.get('confluence', False)]} \u041a\u043e\u043d\u0444\u043b\u044e\u0435\u043d\u0446\u0438\u044f"
    )

    # Confluence details
    confluence_detail = ""
    if signal.confluence and signal.confluence.factors:
        factors = ", ".join(signal.confluence.factors)
        confluence_detail = f"\n\U0001f4cd <b>\u041a\u043e\u043d\u0444\u043b\u044e\u0435\u043d\u0446\u0438\u044f:</b> {factors}"

    # Derivatives context
    derivatives_detail = ""
    if signal.derivatives and signal.derivatives.reason:
        fund_icon = "\u26a0\ufe0f" if signal.derivatives.is_warning else "\u2705"
        derivatives_detail = f"\n{fund_icon} <b>\u0424\u0430\u043d\u0434\u0438\u043d\u0433:</b> {signal.derivatives.reason}"

    # Pump info
    pump_detail = ""
    if signal.pump_analysis and signal.pump_analysis.is_pump:
        pump_detail = f"\n\u26a1 <b>\u041f\u0430\u043c\u043f:</b> {signal.pump_analysis.reason}"

    # Size warning
    size_label = ""
    if signal.size_multiplier < 1.0:
        pct = int(signal.size_multiplier * 100)
        size_label = f"\n\u26a0\ufe0f \u0420\u0430\u0437\u043c\u0435\u0440 \u043f\u043e\u0437\u0438\u0446\u0438\u0438: {pct}% \u043e\u0442 \u0441\u0442\u0430\u043d\u0434\u0430\u0440\u0442\u0430"

    # Score bar
    score = signal.composite_score
    bar_filled = int(score / 10)
    bar_empty = 10 - bar_filled
    score_bar = "\u2588" * bar_filled + "\u2591" * bar_empty

    msg = (
        f"{dir_emoji} <b>{signal.direction} {signal.symbol}</b>  "
        f"{grade_emoji} <b>Grade: {signal.signal_grade}</b>\n"
        f"\n"
        f"\U0001f3af <b>\u0421\u0446\u0435\u043d\u0430\u0440\u0438\u0439:</b> {scenario_label}\n"
        f"\U0001f4ca <b>\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f:</b> {signal.num_confirmations}/5\n"
        f"{conf_lines}\n"
        f"\n"
        f"\U0001f4af <b>\u0421\u043a\u043e\u0440:</b> [{score_bar}] {score:.0f}/100\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4b0 <b>\u0412\u0445\u043e\u0434:</b>    {signal.entry_price}\n"
        f"\U0001f6d1 <b>\u0421\u0442\u043e\u043f:</b>     {signal.stop_loss}  ({signal.risk_pct:.2%})\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f3c1 <b>TP1 (30%):</b> {signal.tp1}\n"
        f"\U0001f3c1 <b>TP2 (40%):</b> {signal.tp2}\n"
        f"\U0001f3c1 <b>TP3 (30%):</b> {signal.tp3}\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4c8 R:R = 1:{signal.rr_ratio}\n"
        f"\U0001f4aa \u0423\u0440\u043e\u0432\u0435\u043d\u044c: {signal.level_strength:.0f}/100 "
        f"(\u0443\u0433\u043e\u043b {signal.level_angle}\u00b0)\n"
        f"\u2696\ufe0f \u041f\u043b\u0435\u0447\u043e: {signal.leverage}x"
        f"{size_label}\n"
        f"{confluence_detail}"
        f"{derivatives_detail}"
        f"{pump_detail}\n"
        f"\n"
        f"\U0001f5c2 <b>\u0422\u0440\u0435\u043d\u0434\u044b:</b> 4H={signal.trend_4h} | "
        f"1H={signal.trend_1h} | 15m={signal.trend_15m}\n"
        f"\u23f0 {signal.timestamp}"
    )
    return msg


class TelegramNotifier:
    """Sends formatted signals to Telegram."""

    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
        self.chat_id = TELEGRAM_CHAT_ID

    async def send_signal(self, signal: Signal):
        """Send a trade signal to Telegram."""
        if not self.bot or not self.chat_id:
            logger.warning("Telegram not configured, printing to console only")
            print(format_signal(signal))
            return

        msg = format_signal(signal)
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"Signal sent: {signal.direction} {signal.symbol} [{signal.signal_grade}]")
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

    def send_signal_sync(self, signal: Signal):
        """Synchronous wrapper for send_signal."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.send_signal(signal))
        except Exception as e:
            logger.error(f"send_signal_sync error: {e}")

    def send_status_sync(self, text: str):
        """Synchronous wrapper for send_status."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.send_status(text))
        except Exception as e:
            logger.error(f"send_status_sync error: {e}")
