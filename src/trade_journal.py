"""
Trade Journal — SQLite-based logging of all signals and trade outcomes.

Stores:
  - Every signal generated (with all confirmations, levels, indicators)
  - Trade outcomes (win/loss/breakeven)
  - Performance statistics over time
  - Exportable to CSV for analysis
"""

import sqlite3
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "trade_journal.db"

CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    unix_ts REAL NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    scenario TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    risk_pct REAL NOT NULL,
    rr_ratio REAL NOT NULL,
    leverage INTEGER NOT NULL,
    size_multiplier REAL NOT NULL,
    num_confirmations INTEGER NOT NULL,
    confirmations_json TEXT,
    level_strength REAL,
    level_touches INTEGER,
    level_angle REAL,
    candle_pattern TEXT,
    rsi_value REAL,
    volume_ratio REAL,
    trend_4h TEXT,
    trend_1h TEXT,
    trend_15m TEXT,
    confluence_score REAL DEFAULT 0,
    confluence_factors TEXT,
    funding_rate REAL,
    derivatives_bias TEXT,
    pump_analysis TEXT,
    outcome TEXT DEFAULT 'pending',
    exit_price REAL,
    exit_time TEXT,
    pnl_pct REAL DEFAULT 0,
    pnl_r REAL DEFAULT 0,
    notes TEXT
)
"""

CREATE_DAILY_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_signals INTEGER DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    breakeven INTEGER DEFAULT 0,
    total_pnl_pct REAL DEFAULT 0,
    best_trade_pnl REAL DEFAULT 0,
    worst_trade_pnl REAL DEFAULT 0,
    avg_rr REAL DEFAULT 0,
    avg_confirmations REAL DEFAULT 0
)
"""


class TradeJournal:
    """SQLite-based trade journal for signal logging and analysis."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(CREATE_SIGNALS_TABLE)
            conn.execute(CREATE_DAILY_STATS_TABLE)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def log_signal(self, signal, confluence_score: float = 0, confluence_factors: list | None = None,
                   funding_rate: float | None = None, derivatives_bias: str = "",
                   pump_analysis: str = "") -> int:
        """
        Log a signal to the journal.

        Returns the signal ID.
        """
        now = datetime.utcnow()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signals (
                    timestamp, unix_ts, symbol, direction, scenario,
                    entry_price, stop_loss, tp1, tp2, tp3,
                    risk_pct, rr_ratio, leverage, size_multiplier,
                    num_confirmations, confirmations_json,
                    level_strength, level_touches, level_angle,
                    candle_pattern, rsi_value, volume_ratio,
                    trend_4h, trend_1h, trend_15m,
                    confluence_score, confluence_factors,
                    funding_rate, derivatives_bias, pump_analysis
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now.isoformat(), time.time(),
                    signal.symbol, signal.direction, signal.scenario,
                    signal.entry_price, signal.stop_loss,
                    signal.tp1, signal.tp2, signal.tp3,
                    signal.risk_pct, signal.rr_ratio,
                    signal.leverage, signal.size_multiplier,
                    signal.num_confirmations,
                    json.dumps(signal.confirmations),
                    signal.level_strength, signal.level_touches, signal.level_angle,
                    signal.candle_pattern, signal.rsi_value, signal.volume_ratio,
                    signal.trend_4h, signal.trend_1h, signal.trend_15m,
                    confluence_score,
                    json.dumps(confluence_factors or []),
                    funding_rate, derivatives_bias, pump_analysis,
                ),
            )
            conn.commit()
            signal_id = cursor.lastrowid
            logger.info(f"Journal: signal #{signal_id} logged ({signal.direction} {signal.symbol})")
            return signal_id

    def update_outcome(self, signal_id: int, outcome: str, exit_price: float, pnl_pct: float, pnl_r: float = 0):
        """Update the outcome of a logged signal."""
        now = datetime.utcnow()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE signals SET outcome = ?, exit_price = ?, exit_time = ?, pnl_pct = ?, pnl_r = ?
                WHERE id = ?
                """,
                (outcome, exit_price, now.isoformat(), pnl_pct, pnl_r, signal_id),
            )
            conn.commit()

    def get_recent_signals(self, hours: int = 24, limit: int = 50) -> list[dict]:
        """Get recent signals within the last N hours."""
        cutoff = time.time() - hours * 3600
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE unix_ts >= ? ORDER BY unix_ts DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_daily_stats(self, date: str | None = None) -> dict:
        """Get stats for a specific date (default: today)."""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE timestamp LIKE ?",
                (f"{date}%",),
            ).fetchall()

        signals = [dict(r) for r in rows]
        closed = [s for s in signals if s["outcome"] in ("win", "loss", "breakeven")]
        wins = [s for s in closed if s["outcome"] == "win"]
        losses = [s for s in closed if s["outcome"] == "loss"]

        total = len(closed)
        return {
            "date": date,
            "total_signals": len(signals),
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / total if total > 0 else 0,
            "total_pnl_pct": sum(s["pnl_pct"] for s in closed),
            "best_trade": max((s["pnl_pct"] for s in closed), default=0),
            "worst_trade": min((s["pnl_pct"] for s in closed), default=0),
            "avg_confirmations": (
                sum(s["num_confirmations"] for s in signals) / len(signals)
                if signals else 0
            ),
        }

    def get_performance_report(self, days: int = 30) -> dict:
        """Get comprehensive performance report for the last N days."""
        cutoff = time.time() - days * 86400
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE unix_ts >= ? ORDER BY unix_ts ASC",
                (cutoff,),
            ).fetchall()

        signals = [dict(r) for r in rows]
        closed = [s for s in signals if s["outcome"] in ("win", "loss", "breakeven")]
        wins = [s for s in closed if s["outcome"] == "win"]
        losses = [s for s in closed if s["outcome"] == "loss"]

        total = len(closed)
        win_rate = len(wins) / total if total > 0 else 0
        avg_win = sum(s["pnl_pct"] for s in wins) / len(wins) if wins else 0
        avg_loss = sum(s["pnl_pct"] for s in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(s["pnl_pct"] for s in wins) / sum(s["pnl_pct"] for s in losses)) if losses and sum(s["pnl_pct"] for s in losses) != 0 else 0

        # Scenario breakdown
        scenarios = {}
        for s in closed:
            sc = s["scenario"]
            if sc not in scenarios:
                scenarios[sc] = {"total": 0, "wins": 0, "pnl": 0}
            scenarios[sc]["total"] += 1
            if s["outcome"] == "win":
                scenarios[sc]["wins"] += 1
            scenarios[sc]["pnl"] += s["pnl_pct"]

        # Max drawdown
        equity_curve = [0.0]
        for s in closed:
            equity_curve.append(equity_curve[-1] + s["pnl_pct"])

        peak = 0
        max_dd = 0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd

        # Best/worst streaks
        best_streak = 0
        worst_streak = 0
        current_streak = 0
        for s in closed:
            if s["outcome"] == "win":
                current_streak = max(0, current_streak) + 1
                best_streak = max(best_streak, current_streak)
            elif s["outcome"] == "loss":
                current_streak = min(0, current_streak) - 1
                worst_streak = max(worst_streak, abs(current_streak))
            else:
                current_streak = 0

        return {
            "period_days": days,
            "total_signals": len(signals),
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "profit_factor": profit_factor,
            "total_pnl_pct": sum(s["pnl_pct"] for s in closed),
            "max_drawdown_pct": max_dd,
            "best_streak": best_streak,
            "worst_streak": worst_streak,
            "scenarios": scenarios,
            "avg_confirmations": (
                sum(s["num_confirmations"] for s in signals) / len(signals)
                if signals else 0
            ),
            "avg_level_strength": (
                sum(s["level_strength"] for s in signals) / len(signals)
                if signals else 0
            ),
        }

    def export_csv(self, filepath: str | None = None) -> str:
        """Export all signals to CSV."""
        import csv

        if filepath is None:
            filepath = str(self.db_path.parent / "signals_export.csv")

        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM signals ORDER BY unix_ts ASC").fetchall()

        if not rows:
            return filepath

        headers = rows[0].keys()
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        logger.info(f"Journal: exported {len(rows)} signals to {filepath}")
        return filepath
