"""
Risk Tracker — enforces daily/weekly loss limits and position management.

Tracks:
  - Open positions count
  - Daily P&L (from signal outcomes)
  - Weekly P&L
  - Consecutive losses
  - Kill switch conditions
"""

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.config import (
    MAX_DAILY_LOSS_PCT, MAX_WEEKLY_LOSS_PCT,
    MAX_OPEN_POSITIONS, RISK_PER_TRADE_PCT,
)

logger = logging.getLogger(__name__)

# Max consecutive losses before pausing
MAX_CONSECUTIVE_LOSSES = 3
# Cooldown after max losses (minutes)
LOSS_COOLDOWN_MINUTES = 60


@dataclass
class TradeRecord:
    """Record of a signal/trade for risk tracking."""
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    tp1: float
    risk_pct: float
    size_multiplier: float
    timestamp: float = 0.0        # Unix timestamp
    outcome: str = "open"         # "open", "win", "loss", "breakeven"
    pnl_pct: float = 0.0         # Realized P&L as percentage of deposit
    closed_at: float = 0.0


@dataclass
class RiskState:
    """Current risk state."""
    can_trade: bool = True
    reason: str = ""
    open_positions: int = 0
    daily_pnl_pct: float = 0.0
    weekly_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    signals_today: int = 0
    cooldown_until: float = 0.0   # Unix timestamp


class RiskTracker:
    """Tracks risk exposure and enforces limits."""

    def __init__(self):
        self._trades: list[TradeRecord] = []
        self._consecutive_losses = 0
        self._cooldown_until = 0.0
        self._daily_signals = 0
        self._last_reset_day = datetime.utcnow().date()

    def _reset_daily_if_needed(self):
        """Reset daily counters at midnight UTC."""
        today = datetime.utcnow().date()
        if today > self._last_reset_day:
            self._daily_signals = 0
            self._last_reset_day = today

    def record_signal(self, signal) -> TradeRecord:
        """Record a new signal being sent."""
        self._reset_daily_if_needed()
        self._daily_signals += 1

        record = TradeRecord(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            risk_pct=signal.risk_pct,
            size_multiplier=signal.size_multiplier,
            timestamp=time.time(),
        )
        self._trades.append(record)
        return record

    def record_outcome(self, symbol: str, direction: str, outcome: str, pnl_pct: float = 0.0):
        """Record the outcome of a trade."""
        for trade in reversed(self._trades):
            if trade.symbol == symbol and trade.direction == direction and trade.outcome == "open":
                trade.outcome = outcome
                trade.pnl_pct = pnl_pct
                trade.closed_at = time.time()

                if outcome == "loss":
                    self._consecutive_losses += 1
                    if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                        self._cooldown_until = time.time() + LOSS_COOLDOWN_MINUTES * 60
                        logger.warning(
                            f"Cooldown activated: {MAX_CONSECUTIVE_LOSSES} consecutive losses. "
                            f"Pausing for {LOSS_COOLDOWN_MINUTES} minutes."
                        )
                elif outcome == "win":
                    self._consecutive_losses = 0

                break

    def get_open_count(self) -> int:
        """Count currently open positions."""
        return sum(1 for t in self._trades if t.outcome == "open")

    def get_daily_pnl(self) -> float:
        """Calculate today's realized P&L as percentage."""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
        today_ts = today_start.timestamp()

        daily_pnl = 0.0
        for trade in self._trades:
            if trade.closed_at >= today_ts and trade.outcome in ("win", "loss"):
                daily_pnl += trade.pnl_pct
        return daily_pnl

    def get_weekly_pnl(self) -> float:
        """Calculate this week's realized P&L as percentage."""
        now = datetime.utcnow()
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0)
        week_ts = week_start.timestamp()

        weekly_pnl = 0.0
        for trade in self._trades:
            if trade.closed_at >= week_ts and trade.outcome in ("win", "loss"):
                weekly_pnl += trade.pnl_pct
        return weekly_pnl

    def check_can_trade(self) -> RiskState:
        """
        Check all risk conditions and return whether trading is allowed.

        Blocks trading if:
        1. Max open positions reached
        2. Daily loss limit hit
        3. Weekly loss limit hit
        4. Consecutive loss cooldown active
        """
        self._reset_daily_if_needed()

        state = RiskState(
            open_positions=self.get_open_count(),
            daily_pnl_pct=self.get_daily_pnl(),
            weekly_pnl_pct=self.get_weekly_pnl(),
            consecutive_losses=self._consecutive_losses,
            signals_today=self._daily_signals,
        )

        # Check cooldown
        now = time.time()
        if self._cooldown_until > now:
            remaining = int((self._cooldown_until - now) / 60)
            state.can_trade = False
            state.reason = f"Кулдаун: {remaining} мин осталось ({MAX_CONSECUTIVE_LOSSES} лоссов подряд)"
            state.cooldown_until = self._cooldown_until
            return state

        # Check open positions
        if state.open_positions >= MAX_OPEN_POSITIONS:
            state.can_trade = False
            state.reason = f"Макс. позиций: {state.open_positions}/{MAX_OPEN_POSITIONS}"
            return state

        # Check daily loss limit
        if state.daily_pnl_pct <= -MAX_DAILY_LOSS_PCT:
            state.can_trade = False
            state.reason = f"Дневной лимит: {state.daily_pnl_pct:.2%} (макс. -{MAX_DAILY_LOSS_PCT:.0%})"
            return state

        # Check weekly loss limit
        if state.weekly_pnl_pct <= -MAX_WEEKLY_LOSS_PCT:
            state.can_trade = False
            state.reason = f"Недельный лимит: {state.weekly_pnl_pct:.2%} (макс. -{MAX_WEEKLY_LOSS_PCT:.0%})"
            return state

        state.can_trade = True
        state.reason = "OK"
        return state

    def get_stats_summary(self) -> dict:
        """Get summary statistics for dashboard."""
        self._reset_daily_if_needed()

        all_closed = [t for t in self._trades if t.outcome in ("win", "loss", "breakeven")]
        wins = [t for t in all_closed if t.outcome == "win"]
        losses = [t for t in all_closed if t.outcome == "loss"]

        total = len(all_closed)
        win_rate = len(wins) / total if total > 0 else 0
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        total_pnl = sum(t.pnl_pct for t in all_closed)

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "total_pnl_pct": total_pnl,
            "daily_pnl_pct": self.get_daily_pnl(),
            "weekly_pnl_pct": self.get_weekly_pnl(),
            "open_positions": self.get_open_count(),
            "consecutive_losses": self._consecutive_losses,
            "signals_today": self._daily_signals,
        }
