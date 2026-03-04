"""
Self-Learning Module — analyzes historical trade outcomes and adjusts scoring.

Reads closed trades from SQLite journal, computes win rates per factor,
and generates adaptive penalty/bonus adjustments. These are applied
on top of the base composite score in signal_engine.

Factors analyzed:
  1. Volume confirmation (present vs absent)
  2. Candle pattern confirmation (present vs absent)
  3. Number of confirmations (3 vs 4 vs 5)
  4. Trend alignment (all aligned vs mixed vs counter)
  5. Confluence score (high vs low)
  6. RSI zone at entry

Recalculates every LEARNING_RECALC_INTERVAL new closed trades.
Requires LEARNING_MIN_TRADES total before learning activates.
"""

import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

from src.config import (
    LEARNING_MIN_TRADES,
    LEARNING_RECALC_INTERVAL,
    LEARNING_MAX_PENALTY,
    LEARNING_DATA_PATH,
)
from src.trade_journal import DB_PATH

logger = logging.getLogger(__name__)

ADJUSTMENTS_PATH = Path(__file__).parent.parent / LEARNING_DATA_PATH


def _win_rate(trades: list[dict]) -> float:
    """Compute win rate from a list of trades."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t["outcome"] == "win")
    return wins / len(trades)


def _avg_pnl_r(trades: list[dict]) -> float:
    """Compute average P&L in R from a list of trades."""
    if not trades:
        return 0.0
    return sum(t.get("pnl_r", 0) or 0 for t in trades) / len(trades)


def _compute_penalty(wr_with: float, wr_without: float, n_with: int, n_without: int) -> float:
    """
    Compute a penalty for the absence of a factor.

    If win rate without the factor is significantly lower than with it,
    produce a proportional penalty (0 to LEARNING_MAX_PENALTY).

    Requires at least 5 samples in each group to avoid noise.
    """
    if n_with < 5 or n_without < 5:
        return 0.0

    diff = wr_with - wr_without
    if diff <= 0:
        # Factor doesn't help — no penalty for absence
        return 0.0

    # Scale: 20% difference → ~10 penalty, 40%+ → max penalty
    raw = diff * LEARNING_MAX_PENALTY / 0.40
    return round(min(raw, LEARNING_MAX_PENALTY), 1)


class SelfLearning:
    """Analyzes trade history and produces adaptive scoring adjustments."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self._adjustments: dict = {}
        self._last_trade_count = 0
        self._load_adjustments()

    def _load_adjustments(self):
        """Load previously computed adjustments from disk."""
        if ADJUSTMENTS_PATH.exists():
            try:
                with open(ADJUSTMENTS_PATH, "r") as f:
                    self._adjustments = json.load(f)
                self._last_trade_count = self._adjustments.get("sample_size", 0)
                logger.info(
                    f"Self-learning: loaded adjustments "
                    f"(n={self._last_trade_count}, "
                    f"updated={self._adjustments.get('updated_at', 'never')})"
                )
            except Exception as e:
                logger.warning(f"Self-learning: failed to load adjustments: {e}")
                self._adjustments = {}

    def _save_adjustments(self):
        """Save computed adjustments to disk."""
        ADJUSTMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ADJUSTMENTS_PATH, "w") as f:
            json.dump(self._adjustments, f, indent=2)
        logger.info(f"Self-learning: saved adjustments to {ADJUSTMENTS_PATH}")

    def _fetch_closed_trades(self) -> list[dict]:
        """Fetch all closed trades from the journal DB."""
        if not self.db_path.exists():
            return []
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM signals WHERE outcome IN ('win', 'loss') ORDER BY unix_ts ASC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Self-learning: DB query error: {e}")
            return []

    def should_recalculate(self) -> bool:
        """Check if we have enough new trades to warrant recalculation."""
        try:
            if not self.db_path.exists():
                return False
            conn = sqlite3.connect(self.db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE outcome IN ('win', 'loss')"
            ).fetchone()[0]
            conn.close()

            if count < LEARNING_MIN_TRADES:
                return False

            new_trades = count - self._last_trade_count
            return new_trades >= LEARNING_RECALC_INTERVAL
        except Exception:
            return False

    def recalculate(self) -> dict:
        """
        Analyze all closed trades and compute adaptive adjustments.

        Returns dict of adjustments that can be applied to signal scoring.
        """
        trades = self._fetch_closed_trades()
        n = len(trades)

        if n < LEARNING_MIN_TRADES:
            logger.info(f"Self-learning: only {n} trades, need {LEARNING_MIN_TRADES}")
            return self._adjustments

        logger.info(f"Self-learning: analyzing {n} closed trades...")

        # Parse confirmations from JSON
        for t in trades:
            try:
                t["_conf"] = json.loads(t.get("confirmations_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                t["_conf"] = {}

        overall_wr = _win_rate(trades)
        overall_avg_r = _avg_pnl_r(trades)

        # ── Factor 1: Volume confirmation ──────────────────
        with_vol = [t for t in trades if t["_conf"].get("volume", False)]
        no_vol = [t for t in trades if not t["_conf"].get("volume", False)]
        vol_penalty = _compute_penalty(
            _win_rate(with_vol), _win_rate(no_vol),
            len(with_vol), len(no_vol)
        )

        # ── Factor 2: Candle pattern ──────────────────────
        with_pattern = [t for t in trades if t["_conf"].get("candle_pattern", False)]
        no_pattern = [t for t in trades if not t["_conf"].get("candle_pattern", False)]
        pattern_penalty = _compute_penalty(
            _win_rate(with_pattern), _win_rate(no_pattern),
            len(with_pattern), len(no_pattern)
        )

        # ── Factor 3: High confirmations (4-5) vs low (3) ──
        high_conf = [t for t in trades if t.get("num_confirmations", 0) >= 4]
        low_conf = [t for t in trades if t.get("num_confirmations", 0) == 3]
        conf_penalty = _compute_penalty(
            _win_rate(high_conf), _win_rate(low_conf),
            len(high_conf), len(low_conf)
        )

        # ── Factor 4: Trend alignment (all aligned vs not) ──
        aligned = []
        misaligned = []
        for t in trades:
            direction = t.get("direction", "")
            t4h = t.get("trend_4h", "neutral")
            t1h = t.get("trend_1h", "neutral")
            t15m = t.get("trend_15m", "neutral")
            expected = "bullish" if direction == "LONG" else "bearish"
            n_aligned = sum(1 for tf in [t4h, t1h, t15m] if tf == expected)
            if n_aligned >= 2:
                aligned.append(t)
            else:
                misaligned.append(t)
        trend_penalty = _compute_penalty(
            _win_rate(aligned), _win_rate(misaligned),
            len(aligned), len(misaligned)
        )

        # ── Factor 5: High confluence (>30) vs low ──────────
        high_confl = [t for t in trades if (t.get("confluence_score") or 0) >= 30]
        low_confl = [t for t in trades if (t.get("confluence_score") or 0) < 30]
        confl_penalty = _compute_penalty(
            _win_rate(high_confl), _win_rate(low_confl),
            len(high_confl), len(low_confl)
        )

        # ── Factor 6: RSI zone (comfortable vs extreme) ────
        rsi_good = [t for t in trades if 35 <= (t.get("rsi_value") or 50) <= 65]
        rsi_extreme = [t for t in trades if not (35 <= (t.get("rsi_value") or 50) <= 65)]
        rsi_penalty = _compute_penalty(
            _win_rate(rsi_good), _win_rate(rsi_extreme),
            len(rsi_good), len(rsi_extreme)
        )

        # ── Build adjustments ──────────────────────────────
        self._adjustments = {
            "no_volume_penalty": vol_penalty,
            "no_pattern_penalty": pattern_penalty,
            "low_conf_penalty": conf_penalty,
            "trend_misalign_penalty": trend_penalty,
            "low_confluence_penalty": confl_penalty,
            "rsi_extreme_penalty": rsi_penalty,
            "updated_at": datetime.utcnow().isoformat(),
            "sample_size": n,
            "overall_win_rate": round(overall_wr, 3),
            "overall_avg_r": round(overall_avg_r, 3),
            "factor_details": {
                "volume": {
                    "with": {"n": len(with_vol), "wr": round(_win_rate(with_vol), 3), "avg_r": round(_avg_pnl_r(with_vol), 3)},
                    "without": {"n": len(no_vol), "wr": round(_win_rate(no_vol), 3), "avg_r": round(_avg_pnl_r(no_vol), 3)},
                },
                "pattern": {
                    "with": {"n": len(with_pattern), "wr": round(_win_rate(with_pattern), 3)},
                    "without": {"n": len(no_pattern), "wr": round(_win_rate(no_pattern), 3)},
                },
                "confirmations": {
                    "high_4_5": {"n": len(high_conf), "wr": round(_win_rate(high_conf), 3)},
                    "low_3": {"n": len(low_conf), "wr": round(_win_rate(low_conf), 3)},
                },
                "trend": {
                    "aligned": {"n": len(aligned), "wr": round(_win_rate(aligned), 3)},
                    "misaligned": {"n": len(misaligned), "wr": round(_win_rate(misaligned), 3)},
                },
                "confluence": {
                    "high_30plus": {"n": len(high_confl), "wr": round(_win_rate(high_confl), 3)},
                    "low_below30": {"n": len(low_confl), "wr": round(_win_rate(low_confl), 3)},
                },
            },
        }

        self._last_trade_count = n
        self._save_adjustments()

        # Log summary
        penalties = [
            f"vol={vol_penalty}", f"pattern={pattern_penalty}",
            f"conf={conf_penalty}", f"trend={trend_penalty}",
            f"confl={confl_penalty}", f"rsi={rsi_penalty}",
        ]
        logger.info(
            f"Self-learning: recalculated (n={n}, WR={overall_wr:.1%}, "
            f"avg_R={overall_avg_r:+.2f}) penalties: {', '.join(penalties)}"
        )

        return self._adjustments

    def get_signal_adjustment(self, signal_data: dict) -> float:
        """
        Compute total penalty for a specific signal based on learned adjustments.

        signal_data should contain: confirmations (dict), num_confirmations (int),
        trend_4h, trend_1h, trend_15m, direction, confluence_score, rsi_value.

        Returns a non-negative penalty to subtract from composite score.
        """
        if not self._adjustments or self._adjustments.get("sample_size", 0) < LEARNING_MIN_TRADES:
            return 0.0

        penalty = 0.0
        reasons = []
        conf = signal_data.get("confirmations", {})

        # Volume
        if not conf.get("volume", False):
            p = self._adjustments.get("no_volume_penalty", 0)
            if p > 0:
                penalty += p
                reasons.append(f"vol={p}")

        # Pattern
        if not conf.get("candle_pattern", False):
            p = self._adjustments.get("no_pattern_penalty", 0)
            if p > 0:
                penalty += p
                reasons.append(f"pattern={p}")

        # Low confirmations
        if signal_data.get("num_confirmations", 0) <= 3:
            p = self._adjustments.get("low_conf_penalty", 0)
            if p > 0:
                penalty += p
                reasons.append(f"conf={p}")

        # Trend misalignment
        direction = signal_data.get("direction", "")
        expected = "bullish" if direction == "LONG" else "bearish"
        n_aligned = sum(
            1 for tf in ["trend_4h", "trend_1h", "trend_15m"]
            if signal_data.get(tf, "neutral") == expected
        )
        if n_aligned < 2:
            p = self._adjustments.get("trend_misalign_penalty", 0)
            if p > 0:
                penalty += p
                reasons.append(f"trend={p}")

        # Low confluence
        if (signal_data.get("confluence_score", 0) or 0) < 30:
            p = self._adjustments.get("low_confluence_penalty", 0)
            if p > 0:
                penalty += p
                reasons.append(f"confl={p}")

        # RSI extreme
        rsi = signal_data.get("rsi_value", 50)
        if not (35 <= rsi <= 65):
            p = self._adjustments.get("rsi_extreme_penalty", 0)
            if p > 0:
                penalty += p
                reasons.append(f"rsi={p}")

        # Cap total learned penalty
        penalty = min(penalty, LEARNING_MAX_PENALTY * 2)

        if penalty > 0:
            logger.info(
                f"  Self-learning penalty: -{penalty:.1f} ({', '.join(reasons)})"
            )

        return penalty

    def get_adjustments(self) -> dict:
        """Return current learned adjustments (for dashboard display)."""
        return self._adjustments.copy()
