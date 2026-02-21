"""
Position Monitor — automatic virtual position tracking.

After a signal is sent, opens a virtual position at the level price
and checks every scan cycle (60s) using 1-minute candle high/low
to catch price spikes that may have already retraced.

Tracks:
  - TP1/TP2/TP3 progressive hits (partial closes)
  - Stop loss (with breakeven move after TP1)
  - Time stop (TIME_STOP_CANDLES * TF_WORKING)
  - Automatic P&L calculation in R-multiples and %

Results are written to both RiskTracker (in-memory) and
TradeJournal (SQLite) for weekly statistics.
"""

import time
import logging
from dataclasses import dataclass, field

from src.config import (
    TP1_R, TP2_R, TP3_R,
    TP1_PCT, TP2_PCT, TP3_PCT,
    TIME_STOP_CANDLES, RISK_PER_TRADE_PCT,
    POSITION_CHECK_INTERVAL,
)

logger = logging.getLogger(__name__)

# TF_WORKING duration in seconds (15m = 900s)
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}

# How many 1m candles to fetch per check (covers gap between checks)
CANDLES_TO_CHECK = 5


@dataclass
class VirtualPosition:
    """A virtual position opened at level price."""
    journal_id: int               # ID in trade_journal.signals table
    symbol: str
    direction: str                # LONG / SHORT
    entry_price: float            # level_price — for tracking
    stop_loss: float              # current SL (moves to BE after TP1)
    original_stop: float          # preserved for P&L calc
    tp1: float
    tp2: float
    tp3: float
    risk_pct: float               # stop distance as % of entry
    size_multiplier: float
    opened_at: float              # unix timestamp
    timeframe: str = "15m"        # working TF for time stop calc
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    status: str = "open"          # open / closed
    outcome: str = ""             # win / loss / breakeven
    exit_price: float = 0.0
    closed_at: float = 0.0
    pnl_r: float = 0.0           # P&L in R-multiples
    pnl_pct: float = 0.0         # P&L as % of deposit


class PositionMonitor:
    """Monitors open virtual positions and auto-records outcomes."""

    def __init__(self, fetcher, risk_tracker, journal, notifier):
        self.fetcher = fetcher
        self.risk_tracker = risk_tracker
        self.journal = journal
        self.notifier = notifier
        self._positions: list[VirtualPosition] = []
        self._last_check = 0.0

    def close_position_manual(self, symbol: str, direction: str, outcome: str):
        """Close a virtual position manually (from Telegram button callback).

        This ensures the monitor stops tracking a position that the user
        already closed via inline buttons.
        """
        for pos in self._positions:
            if pos.symbol == symbol and pos.direction == direction and pos.status == "open":
                pos.status = "closed"
                pos.outcome = outcome
                pos.closed_at = time.time()
                # Calculate approximate P&L based on TP progress
                if outcome == "win":
                    pos.pnl_r = self._calc_partial_pnl(pos) if pos.tp1_hit else 1.0
                elif outcome == "loss":
                    pos.pnl_r = -1.0
                else:
                    pos.pnl_r = 0.0
                pos.pnl_pct = round(pos.pnl_r * RISK_PER_TRADE_PCT * pos.size_multiplier, 5)

                # Update journal
                self.journal.update_outcome(
                    signal_id=pos.journal_id,
                    outcome=outcome,
                    exit_price=0.0,
                    pnl_pct=pos.pnl_pct,
                    pnl_r=pos.pnl_r,
                )
                logger.info(
                    f"Position closed manually: {pos.direction} {pos.symbol} "
                    f"outcome={outcome} pnl={pos.pnl_r:+.2f}R"
                )
                return True
        return False

    def open_position(self, signal, journal_id: int):
        """Open a virtual position after signal is sent.

        Uses entry_price (market price at signal time), NOT level_price,
        because TP1/TP2/TP3 and SL are calculated from entry_price.
        Using level_price would create false TP hits when level != entry.
        """
        pos = VirtualPosition(
            journal_id=journal_id,
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            original_stop=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            risk_pct=signal.risk_pct,
            size_multiplier=signal.size_multiplier,
            opened_at=time.time(),
            timeframe=getattr(signal, '_timeframe', '15m'),
        )
        self._positions.append(pos)
        logger.info(
            f"Position opened: {pos.direction} {pos.symbol} "
            f"entry={pos.entry_price:.6f} SL={pos.stop_loss:.6f} "
            f"TP1={pos.tp1:.6f} TP2={pos.tp2:.6f} TP3={pos.tp3:.6f}"
        )

    def should_check(self) -> bool:
        """Return True if enough time has passed since last check."""
        return time.time() - self._last_check >= POSITION_CHECK_INTERVAL

    def check_all(self):
        """Check all open positions using 1m candle high/low.

        Uses candle extremes (high for LONG TPs, low for SHORT TPs)
        to catch price spikes that may have already retraced by now.
        """
        open_positions = [p for p in self._positions if p.status == "open"]
        if not open_positions:
            return

        self._last_check = time.time()
        logger.info(f"Position monitor: checking {len(open_positions)} open positions...")

        for pos in open_positions:
            if pos.status != "open":
                continue
            try:
                # Fetch recent 1m candles to catch intra-candle spikes
                df = self.fetcher.fetch_ohlcv(pos.symbol, "1m", limit=CANDLES_TO_CHECK)
                if df is None or df.empty:
                    logger.warning(f"Position check: no 1m data for {pos.symbol}")
                    continue

                # Check each candle chronologically
                for _, candle in df.iterrows():
                    if pos.status != "open":
                        break
                    high = float(candle["high"])
                    low = float(candle["low"])
                    close = float(candle["close"])
                    self._check_position(pos, high, low, close)

            except Exception as e:
                logger.error(f"Position check error {pos.symbol}: {e}")

    def _check_position(self, pos: VirtualPosition, high: float, low: float, close: float):
        """Check a single position against candle high/low/close.

        For LONG:  TP checks use high (best price), SL checks use low (worst price)
        For SHORT: TP checks use low (best price), SL checks use high (worst price)

        TP checks go from highest to lowest (TP3→TP2→TP1).
        """
        is_long = pos.direction == "LONG"
        # Best price for TPs (high for long, low for short)
        tp_price = high if is_long else low
        # Worst price for SL (low for long, high for short)
        sl_price = low if is_long else high

        # ── Stop loss check ──────────────────────────────────
        sl_hit = (is_long and sl_price <= pos.stop_loss) or \
                 (not is_long and sl_price >= pos.stop_loss)
        if sl_hit:
            if pos.tp1_hit:
                # TP1 was hit, SL moved to breakeven → partial win
                self._close_position(pos, pos.stop_loss, "win", self._calc_partial_pnl(pos))
            else:
                self._close_position(pos, pos.stop_loss, "loss", -1.0)
            return

        # ── TP3 → full win, close immediately ────────────────
        tp3_hit = (is_long and tp_price >= pos.tp3) or \
                  (not is_long and tp_price <= pos.tp3)
        if not pos.tp3_hit and tp3_hit:
            pos.tp1_hit = True
            pos.tp2_hit = True
            pos.tp3_hit = True
            full_r = TP1_PCT * TP1_R + TP2_PCT * TP2_R + TP3_PCT * TP3_R
            self._close_position(pos, pos.tp3, "win", full_r)
            return

        # ── TP2 reached → mark TP1+TP2, move SL to BE ───────
        tp2_hit = (is_long and tp_price >= pos.tp2) or \
                  (not is_long and tp_price <= pos.tp2)
        if not pos.tp2_hit and tp2_hit:
            pos.tp1_hit = True
            pos.tp2_hit = True
            pos.stop_loss = pos.entry_price
            self._notify_tp_hit(pos, 2, pos.tp2)

        # ── TP1 reached → mark TP1, move SL to BE ───────────
        elif not pos.tp1_hit:
            tp1_hit = (is_long and tp_price >= pos.tp1) or \
                      (not is_long and tp_price <= pos.tp1)
            if tp1_hit:
                pos.tp1_hit = True
                pos.stop_loss = pos.entry_price
                self._notify_tp_hit(pos, 1, pos.tp1)

        # ── Time stop ───────────────────────────────────────
        tf_sec = TF_SECONDS.get(pos.timeframe, 900)
        max_duration = TIME_STOP_CANDLES * tf_sec
        elapsed = time.time() - pos.opened_at

        if elapsed >= max_duration:
            if pos.tp1_hit:
                pnl_r = self._calc_partial_pnl(pos)
                outcome = "win" if pnl_r > 0 else "breakeven"
            else:
                pnl_r = self._calc_market_pnl(pos, close)
                outcome = "win" if pnl_r > 0.1 else ("loss" if pnl_r < -0.1 else "breakeven")
            self._close_position(pos, close, outcome, pnl_r, is_time_stop=True)

    def _calc_partial_pnl(self, pos: VirtualPosition) -> float:
        """Calculate P&L in R for partial TP hits (remaining at breakeven)."""
        pnl_r = 0.0
        if pos.tp1_hit:
            pnl_r += TP1_PCT * TP1_R       # 30% * 1R = 0.30R
        if pos.tp2_hit:
            pnl_r += TP2_PCT * TP2_R       # 40% * 2R = 0.80R
        # Remaining portion closes at breakeven (0R)
        return round(pnl_r, 2)

    def _calc_market_pnl(self, pos: VirtualPosition, exit_price: float) -> float:
        """Calculate P&L in R based on exit price vs entry."""
        stop_dist = abs(pos.entry_price - pos.original_stop)
        if stop_dist <= 0:
            return 0.0
        if pos.direction == "LONG":
            raw_pnl = exit_price - pos.entry_price
        else:
            raw_pnl = pos.entry_price - exit_price
        return round(raw_pnl / stop_dist, 2)

    def _close_position(self, pos: VirtualPosition, exit_price: float,
                        outcome: str, pnl_r: float, is_time_stop: bool = False):
        """Close position, update journal and risk tracker, notify."""
        pos.status = "closed"
        pos.outcome = outcome
        pos.exit_price = exit_price
        pos.closed_at = time.time()
        pos.pnl_r = pnl_r
        pos.pnl_pct = round(pnl_r * RISK_PER_TRADE_PCT * pos.size_multiplier, 5)

        # Duration
        duration_min = int((pos.closed_at - pos.opened_at) / 60)
        hours = duration_min // 60
        mins = duration_min % 60
        duration_str = f"{hours}ч {mins}м" if hours > 0 else f"{mins}м"

        # TP progress string
        tp_str = ""
        if pos.tp1_hit:
            tp_str += "TP1"
        if pos.tp2_hit:
            tp_str += "+TP2"
        if pos.tp3_hit:
            tp_str += "+TP3"

        reason = "time stop" if is_time_stop else ("SL" if outcome == "loss" else tp_str or "market")

        logger.info(
            f"Position closed: {pos.direction} {pos.symbol} "
            f"outcome={outcome} pnl={pnl_r:+.2f}R ({pos.pnl_pct:+.3%}) "
            f"reason={reason} duration={duration_str}"
        )

        # Update journal (SQLite)
        self.journal.update_outcome(
            signal_id=pos.journal_id,
            outcome=outcome,
            exit_price=exit_price,
            pnl_pct=pos.pnl_pct,
            pnl_r=pnl_r,
        )

        # Update risk tracker (in-memory)
        self.risk_tracker.record_outcome(
            pos.symbol, pos.direction, outcome, pnl_pct=pos.pnl_pct
        )

        # Telegram notification
        self._notify_close(pos, reason, duration_str)

    def _notify_tp_hit(self, pos: VirtualPosition, tp_num: int, price: float):
        """Notify user about a TP hit (position still open)."""
        tp_pct = {1: TP1_PCT, 2: TP2_PCT, 3: TP3_PCT}[tp_num]
        tp_r = {1: TP1_R, 2: TP2_R, 3: TP3_R}[tp_num]

        msg = (
            f"{'=' * 26}\n"
            f"TP{tp_num} HIT {pos.direction} {pos.symbol}\n"
            f"{'=' * 26}\n"
            f"Цена: {price:.6f}\n"
            f"Закрыто: {tp_pct:.0%} позиции (+{tp_r:.1f}R)\n"
        )
        if tp_num == 1:
            msg += f"Стоп перемещён в б/у ({pos.entry_price:.6f})\n"
        msg += f"Осталось: до TP{tp_num + 1}" if tp_num < 3 else ""

        self.notifier.send_status_sync(msg)
        logger.info(f"TP{tp_num} hit: {pos.symbol} @ {price:.6f}")

    def _notify_close(self, pos: VirtualPosition, reason: str, duration: str):
        """Send final position close notification to Telegram."""
        if pos.outcome == "win":
            emoji = "+" if pos.pnl_r > 0 else ""
            header = f"ПОЗИЦИЯ ЗАКРЫТА — ПРОФИТ {emoji}{pos.pnl_r:.2f}R"
        elif pos.outcome == "loss":
            header = f"ПОЗИЦИЯ ЗАКРЫТА — ЛОСС {pos.pnl_r:.2f}R"
        else:
            header = f"ПОЗИЦИЯ ЗАКРЫТА — БЕЗУБЫТОК"

        tp_progress = ""
        if pos.tp1_hit:
            tp_progress = "TP1"
        if pos.tp2_hit:
            tp_progress += " > TP2"
        if pos.tp3_hit:
            tp_progress += " > TP3"

        msg = (
            f"{'=' * 26}\n"
            f"{header}\n"
            f"{'=' * 26}\n"
            f"Монета: {pos.symbol}\n"
            f"Направление: {pos.direction}\n"
            f"Вход: {pos.entry_price:.6f}\n"
            f"Выход: {pos.exit_price:.6f}\n"
            f"Причина: {reason}\n"
        )
        if tp_progress:
            msg += f"Прогресс: {tp_progress}\n"
        msg += (
            f"P&L: {pos.pnl_r:+.2f}R ({pos.pnl_pct:+.3%})\n"
            f"Длительность: {duration}\n"
        )

        self.notifier.send_status_sync(msg)

    def get_open_positions(self) -> list[VirtualPosition]:
        """Return list of currently open positions."""
        return [p for p in self._positions if p.status == "open"]

    def get_stats(self) -> dict:
        """Get position tracking statistics."""
        closed = [p for p in self._positions if p.status == "closed"]
        wins = [p for p in closed if p.outcome == "win"]
        losses = [p for p in closed if p.outcome == "loss"]
        total = len(closed)

        return {
            "open": len(self.get_open_positions()),
            "total_closed": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / total if total > 0 else 0,
            "total_pnl_r": sum(p.pnl_r for p in closed),
            "total_pnl_pct": sum(p.pnl_pct for p in closed),
            "avg_r": sum(p.pnl_r for p in closed) / total if total > 0 else 0,
        }
