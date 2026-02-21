"""
Performance Dashboard — generates periodic stats and sends to Telegram.

Reports:
  - Hourly signal count
  - Daily P&L and win rate
  - Weekly performance summary
  - Signal quality metrics (avg confirmations, avg level strength)
  - Strategy health indicators
"""

import time
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def format_risk_state(risk_state) -> str:
    """Format current risk state for display."""
    if not risk_state.can_trade:
        status = f"🔴 ЗАБЛОКИРОВАНО: {risk_state.reason}"
    else:
        status = "🟢 Торговля разрешена"

    return (
        f"<b>Риск-статус:</b>\n"
        f"  {status}\n"
        f"  Открыто позиций: {risk_state.open_positions}\n"
        f"  Дневной P&L: {risk_state.daily_pnl_pct:+.2%}\n"
        f"  Недельный P&L: {risk_state.weekly_pnl_pct:+.2%}\n"
        f"  Лоссов подряд: {risk_state.consecutive_losses}\n"
        f"  Сигналов сегодня: {risk_state.signals_today}"
    )


def format_daily_dashboard(journal_stats: dict, risk_stats: dict, regime_info: str = "") -> str:
    """Format a complete daily dashboard message."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Win rate bar
    wr = risk_stats.get("win_rate", 0)
    wr_bar = "█" * int(wr * 10) + "░" * (10 - int(wr * 10))

    # P&L indicator
    total_pnl = risk_stats.get("total_pnl_pct", 0)
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"

    msg = (
        f"📊 <b>DLS Dashboard</b>\n"
        f"🕐 {now}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Статистика сегодня:</b>\n"
        f"  Сигналов: {journal_stats.get('total_signals', 0)}\n"
        f"  Сделок: {journal_stats.get('total_trades', 0)}\n"
        f"  Побед: {journal_stats.get('wins', 0)} | "
        f"Проигрышей: {journal_stats.get('losses', 0)}\n"
        f"  Win Rate: [{wr_bar}] {wr:.0%}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{pnl_emoji} <b>P&L:</b>\n"
        f"  Сегодня: {risk_stats.get('daily_pnl_pct', 0):+.2%}\n"
        f"  Неделя: {risk_stats.get('weekly_pnl_pct', 0):+.2%}\n"
        f"  Всего: {total_pnl:+.2%}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 <b>Качество сигналов:</b>\n"
        f"  Ср. подтверждений: {journal_stats.get('avg_confirmations', 0):.1f}/5\n"
        f"  Открыто позиций: {risk_stats.get('open_positions', 0)}\n"
        f"  Лоссов подряд: {risk_stats.get('consecutive_losses', 0)}\n"
    )

    if regime_info:
        msg += (
            f"\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>Режим рынка:</b>\n"
            f"  {regime_info}\n"
        )

    return msg


def format_weekly_report(report: dict) -> str:
    """Format a comprehensive weekly performance report."""
    wr = report.get("win_rate", 0)
    pf = report.get("profit_factor", 0)
    total_pnl = report.get("total_pnl_pct", 0)

    # Health score (0-100)
    health = 0
    if wr >= 0.4:
        health += 25
    if wr >= 0.55:
        health += 15
    if pf >= 1.5:
        health += 25
    if pf >= 2.0:
        health += 10
    if report.get("max_drawdown_pct", 1) < 0.03:
        health += 15
    if report.get("worst_streak", 10) <= 3:
        health += 10

    health_bar = "█" * (health // 10) + "░" * (10 - health // 10)

    if health >= 70:
        health_emoji = "🟢"
        health_text = "Отличное"
    elif health >= 50:
        health_emoji = "🟡"
        health_text = "Нормальное"
    else:
        health_emoji = "🔴"
        health_text = "Требует внимания"

    msg = (
        f"📊 <b>НЕДЕЛЬНЫЙ ОТЧЁТ DLS</b>\n"
        f"\n"
        f"{'━' * 26}\n"
        f"📈 <b>Результаты за {report.get('period_days', 7)} дней:</b>\n"
        f"  Всего сигналов: {report.get('total_signals', 0)}\n"
        f"  Сделок: {report.get('total_trades', 0)}\n"
        f"  Побед: {report.get('wins', 0)} | "
        f"Проигрышей: {report.get('losses', 0)}\n"
        f"  Win Rate: {wr:.1%}\n"
        f"  Profit Factor: {pf:.2f}\n"
        f"\n"
        f"{'━' * 26}\n"
        f"💰 <b>Финансовые результаты:</b>\n"
        f"  Итого P&L: {total_pnl:+.2%}\n"
        f"  Макс. просадка: {report.get('max_drawdown_pct', 0):.2%}\n"
        f"  Ср. выигрыш: {report.get('avg_win_pct', 0):+.2%}\n"
        f"  Ср. проигрыш: {report.get('avg_loss_pct', 0):+.2%}\n"
        f"  Лучшая сделка: {report.get('best_trade_pnl', 0):+.2%}\n"
        f"  Худшая серия: {report.get('worst_streak', 0)} лоссов\n"
        f"\n"
        f"{'━' * 26}\n"
        f"{health_emoji} <b>Здоровье стратегии: {health_text}</b>\n"
        f"  [{health_bar}] {health}/100\n"
    )

    # Scenario breakdown
    scenarios = report.get("scenarios", {})
    if scenarios:
        msg += f"\n{'━' * 26}\n📋 <b>По сценариям:</b>\n"
        for sc_name, sc_data in scenarios.items():
            sc_wr = sc_data["wins"] / sc_data["total"] if sc_data["total"] > 0 else 0
            msg += f"  {sc_name}: {sc_data['total']} сделок, WR {sc_wr:.0%}, P&L {sc_data['pnl']:+.2%}\n"

    return msg


class PerformanceDashboard:
    """Manages periodic performance reporting."""

    def __init__(self, notifier, journal, risk_tracker):
        self.notifier = notifier
        self.journal = journal
        self.risk_tracker = risk_tracker
        self._last_hourly = 0
        self._last_daily = 0
        self._last_weekly = 0

    def check_and_send(self, regime_info: str = ""):
        """Check if it's time to send any dashboard reports."""
        now = time.time()

        # Hourly: every 3600 seconds
        if now - self._last_hourly >= 3600:
            self._send_hourly()
            self._last_hourly = now

        # Daily: every 86400 seconds (or at midnight UTC)
        hour = datetime.utcnow().hour
        if hour == 0 and now - self._last_daily >= 82800:  # ~23h gap
            self._send_daily(regime_info)
            self._last_daily = now

        # Weekly: Sunday at midnight
        weekday = datetime.utcnow().weekday()
        if weekday == 6 and hour == 0 and now - self._last_weekly >= 604800:
            self._send_weekly()
            self._last_weekly = now

    def _send_hourly(self):
        """Send brief hourly status."""
        risk_state = self.risk_tracker.check_can_trade()
        if not risk_state.can_trade:
            msg = f"⏰ <b>Статус (час):</b> 🔴 {risk_state.reason}"
            self.notifier.send_status_sync(msg)

    def _send_daily(self, regime_info: str = ""):
        """Send daily dashboard.

        Sent at midnight UTC — shows YESTERDAY's stats (the day that just ended),
        not the new day which has 0 signals yet.
        """
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        journal_stats = self.journal.get_daily_stats(yesterday)
        risk_stats = self.risk_tracker.get_stats_summary()

        msg = format_daily_dashboard(journal_stats, risk_stats, regime_info)
        self.notifier.send_status_sync(msg)

    def _send_weekly(self):
        """Send weekly performance report."""
        report = self.journal.get_performance_report(days=7)
        msg = format_weekly_report(report)
        self.notifier.send_status_sync(msg)

    def send_manual_report(self, days: int = 7):
        """Send a manual performance report on demand."""
        report = self.journal.get_performance_report(days=days)
        msg = format_weekly_report(report)
        self.notifier.send_status_sync(msg)
