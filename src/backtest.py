"""
Backtest Engine — replays historical data to validate strategy performance.

Features:
  - Downloads extended historical OHLCV data (1000+ candles)
  - Walk-forward approach with configurable step size
  - Simulates entries/exits with partial TP + trailing stop
  - Progress logging every 50 steps
  - Comprehensive performance metrics
"""

import pandas as pd
import numpy as np
import logging
from dataclasses import dataclass, field

from src.config import (
    TF_WORKING, TF_CONTEXT, TF_DIRECTION,
    CANDLE_LIMIT, TP1_R, TP2_R, TP3_R,
    TP1_PCT, TP2_PCT, TP3_PCT,
    MIN_CONFIRMATIONS,
)
from src.data_fetcher import DataFetcher
from src.indicators import compute_indicators, get_trend_direction
from src.diagonal_levels import detect_diagonal_levels
from src.signal_engine import evaluate_signal

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """A simulated trade in the backtest."""
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    entry_time: str = ""
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""
    pnl_pct: float = 0.0
    confirmations: int = 0
    level_strength: float = 0.0
    grade: str = ""
    scenario: str = ""
    is_open: bool = True
    candles_held: int = 0
    partial_exits: list = field(default_factory=list)
    remaining_pct: float = 1.0


@dataclass
class BacktestResult:
    """Complete backtest results."""
    symbol: str
    period: str
    total_candles: int = 0
    total_signals: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    total_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    avg_hold_candles: float = 0.0
    levels_detected: int = 0
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


class BacktestEngine:
    """Replays historical data through the DLS strategy."""

    def __init__(self):
        self.fetcher = DataFetcher()

    def run_backtest(
        self,
        symbol: str,
        timeframe: str = TF_WORKING,
        limit: int = 1000,
        walk_forward_window: int = 150,
        step_size: int = 3,
    ) -> BacktestResult:
        """
        Run backtest on a single symbol.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT:USDT")
            timeframe: Candle timeframe
            limit: Total candles to fetch
            walk_forward_window: Candles used for level detection
            step_size: Check for signals every N candles (faster)
        """
        logger.info(f"Backtest: {symbol} | {timeframe} | {limit} candles | window={walk_forward_window} | step={step_size}")
        print(f"\n  Загрузка {limit} свечей с Bybit...")

        # Fetch main data
        df_full = self.fetcher.fetch_ohlcv(symbol, timeframe, limit=limit)
        if df_full is None or len(df_full) < walk_forward_window + 50:
            logger.error(f"Insufficient data for {symbol}: got {len(df_full) if df_full is not None else 0}")
            return BacktestResult(symbol=symbol, period="insufficient data")

        print(f"  Получено {len(df_full)} свечей: {df_full.index[0]} — {df_full.index[-1]}")

        # Fetch context timeframes for trend
        print(f"  Загрузка контекста (4H, 1H)...")
        df_4h = self.fetcher.fetch_ohlcv(symbol, TF_CONTEXT, limit=CANDLE_LIMIT)
        df_1h = self.fetcher.fetch_ohlcv(symbol, TF_DIRECTION, limit=CANDLE_LIMIT)

        df_4h_ind = compute_indicators(df_4h) if df_4h is not None else None
        df_1h_ind = compute_indicators(df_1h) if df_1h is not None else None
        trend_4h = get_trend_direction(df_4h_ind) if df_4h_ind is not None else "neutral"
        trend_1h = get_trend_direction(df_1h_ind) if df_1h_ind is not None else "neutral"

        print(f"  Тренды: 4H={trend_4h}, 1H={trend_1h}")
        print(f"  Запуск walk-forward бэктеста...\n")

        result = BacktestResult(
            symbol=symbol,
            period=f"{df_full.index[0]} — {df_full.index[-1]}",
            total_candles=len(df_full),
        )

        open_trades: list[BacktestTrade] = []
        equity = 0.0
        result.equity_curve = [0.0]
        total_levels_found = 0
        steps_evaluated = 0
        total_steps = (len(df_full) - walk_forward_window) // step_size

        # Walk forward
        for end_idx in range(walk_forward_window, len(df_full)):
            current_candle = df_full.iloc[end_idx]
            current_high = float(current_candle["high"])
            current_low = float(current_candle["low"])
            current_close = float(current_candle["close"])

            # ── Update open trades every candle ──────────────
            closed_this_candle = []
            for trade in open_trades:
                if not trade.is_open:
                    continue

                trade.candles_held += 1

                # Check stop loss
                hit_stop = False
                if trade.direction == "LONG" and current_low <= trade.stop_loss:
                    hit_stop = True
                elif trade.direction == "SHORT" and current_high >= trade.stop_loss:
                    hit_stop = True

                if hit_stop:
                    trade.exit_price = trade.stop_loss
                    trade.exit_reason = "stop"
                    trade.is_open = False
                    if trade.direction == "LONG":
                        trade.pnl_pct = ((trade.stop_loss - trade.entry_price) / trade.entry_price) * trade.remaining_pct
                    else:
                        trade.pnl_pct = ((trade.entry_price - trade.stop_loss) / trade.entry_price) * trade.remaining_pct
                    trade.pnl_pct += sum(p["pnl"] for p in trade.partial_exits)
                    equity += trade.pnl_pct
                    closed_this_candle.append(trade)
                    continue

                # Check TP levels
                for tp_price, tp_pct, tp_name in [(trade.tp1, TP1_PCT, "tp1"), (trade.tp2, TP2_PCT, "tp2"), (trade.tp3, TP3_PCT, "tp3")]:
                    if any(p["tp"] == tp_name for p in trade.partial_exits):
                        continue

                    hit_tp = (trade.direction == "LONG" and current_high >= tp_price) or \
                             (trade.direction == "SHORT" and current_low <= tp_price)

                    if hit_tp:
                        if trade.direction == "LONG":
                            partial_pnl = ((tp_price - trade.entry_price) / trade.entry_price) * tp_pct
                        else:
                            partial_pnl = ((trade.entry_price - tp_price) / trade.entry_price) * tp_pct

                        trade.partial_exits.append({"tp": tp_name, "pnl": partial_pnl, "price": tp_price})
                        trade.remaining_pct -= tp_pct

                        if tp_name == "tp3" or trade.remaining_pct <= 0.01:
                            trade.exit_price = tp_price
                            trade.exit_reason = tp_name
                            trade.is_open = False
                            trade.pnl_pct = sum(p["pnl"] for p in trade.partial_exits)
                            equity += trade.pnl_pct
                            closed_this_candle.append(trade)

                        if tp_name == "tp1" and trade.is_open:
                            trade.stop_loss = trade.entry_price  # Move to breakeven

                # Time stop
                if trade.is_open and trade.candles_held >= 20:
                    trade.exit_price = current_close
                    trade.exit_reason = "time_stop"
                    trade.is_open = False
                    if trade.direction == "LONG":
                        final_pnl = ((current_close - trade.entry_price) / trade.entry_price) * trade.remaining_pct
                    else:
                        final_pnl = ((trade.entry_price - current_close) / trade.entry_price) * trade.remaining_pct
                    trade.pnl_pct = final_pnl + sum(p["pnl"] for p in trade.partial_exits)
                    equity += trade.pnl_pct
                    closed_this_candle.append(trade)

            for t in closed_this_candle:
                t.exit_time = str(current_candle.name)

            open_trades = [t for t in open_trades if t.is_open]
            result.equity_curve.append(equity)

            # ── Signal detection only every step_size candles ─
            if (end_idx - walk_forward_window) % step_size != 0:
                continue

            if open_trades:
                continue

            steps_evaluated += 1

            # Progress logging
            if steps_evaluated % 30 == 0:
                progress = steps_evaluated / max(total_steps, 1) * 100
                print(
                    f"  [{progress:5.1f}%] Свеча {end_idx}/{len(df_full)} | "
                    f"Уровней: {total_levels_found} | "
                    f"Сигналов: {result.total_signals} | "
                    f"P&L: {equity:+.2%}"
                )

            # Build window and compute indicators
            start_idx = max(0, end_idx - walk_forward_window)
            window = df_full.iloc[start_idx:end_idx + 1]

            df_window_ind = compute_indicators(window)
            if df_window_ind is None:
                continue

            # Determine scenario from window trend
            window_trend = get_trend_direction(df_window_ind)
            if window_trend == "bullish":
                scenario = "uptrend"
            elif window_trend == "bearish":
                scenario = "downtrend"
            else:
                scenario = "ranging"

            # Detect diagonal levels
            levels = detect_diagonal_levels(df_window_ind, timeframe)
            if levels:
                total_levels_found += len(levels)

            if not levels:
                continue

            # Entry TF approximation (last 50 candles of window)
            df_entry_approx = window.iloc[-50:] if len(window) >= 50 else window

            for level in levels[:5]:
                signal = evaluate_signal(
                    symbol=symbol,
                    scenario=scenario,
                    level=level,
                    df_working=df_window_ind,
                    df_entry=df_entry_approx,
                    trend_4h=trend_4h,
                    trend_1h=trend_1h,
                    timeframe=timeframe,
                )

                if signal is None:
                    continue

                result.total_signals += 1

                trade = BacktestTrade(
                    symbol=symbol,
                    direction=signal.direction,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    tp1=signal.tp1,
                    tp2=signal.tp2,
                    tp3=signal.tp3,
                    entry_time=str(current_candle.name),
                    confirmations=signal.num_confirmations,
                    level_strength=signal.level_strength,
                    grade=signal.signal_grade,
                    scenario=signal.scenario,
                )
                open_trades.append(trade)
                result.trades.append(trade)

                print(
                    f"  >> СИГНАЛ [{signal.signal_grade}]: {signal.direction} "
                    f"@ {signal.entry_price:.2f} | "
                    f"SL={signal.stop_loss:.2f} TP1={signal.tp1:.2f} | "
                    f"{signal.num_confirmations}/5 conf"
                )
                break

        # Close remaining open trades
        for trade in open_trades:
            last_close = float(df_full["close"].iloc[-1])
            trade.exit_price = last_close
            trade.exit_reason = "end_of_data"
            trade.is_open = False
            if trade.direction == "LONG":
                final_pnl = ((last_close - trade.entry_price) / trade.entry_price) * trade.remaining_pct
            else:
                final_pnl = ((trade.entry_price - last_close) / trade.entry_price) * trade.remaining_pct
            trade.pnl_pct = final_pnl + sum(p["pnl"] for p in trade.partial_exits)

        result.levels_detected = total_levels_found
        print(f"\n  Завершено. Уровней найдено: {total_levels_found}, сигналов: {result.total_signals}")

        # ── Calculate statistics ─────────────────────────
        result.total_trades = len(result.trades)
        closed_trades = [t for t in result.trades if not t.is_open]

        for trade in closed_trades:
            if trade.pnl_pct > 0.001:
                result.wins += 1
            elif trade.pnl_pct < -0.001:
                result.losses += 1
            else:
                result.breakeven += 1

        if result.total_trades > 0:
            result.win_rate = result.wins / result.total_trades

        winning_pnls = [t.pnl_pct for t in closed_trades if t.pnl_pct > 0]
        losing_pnls = [t.pnl_pct for t in closed_trades if t.pnl_pct < 0]

        result.avg_win_pct = float(np.mean(winning_pnls)) if winning_pnls else 0
        result.avg_loss_pct = float(np.mean(losing_pnls)) if losing_pnls else 0
        result.total_pnl_pct = sum(t.pnl_pct for t in closed_trades)
        result.best_trade_pnl = max(winning_pnls) if winning_pnls else 0
        result.worst_trade_pnl = min(losing_pnls) if losing_pnls else 0

        total_wins_sum = sum(winning_pnls) if winning_pnls else 0
        total_loss_sum = abs(sum(losing_pnls)) if losing_pnls else 0
        result.profit_factor = total_wins_sum / total_loss_sum if total_loss_sum > 0 else 0

        if closed_trades:
            result.avg_hold_candles = float(np.mean([t.candles_held for t in closed_trades]))

        # Max drawdown
        peak = 0.0
        max_dd = 0.0
        for val in result.equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = max_dd

        # Sharpe ratio
        if len(result.equity_curve) > 1:
            returns = np.diff(result.equity_curve)
            std = float(np.std(returns))
            if std > 0:
                result.sharpe_ratio = float(np.mean(returns)) / std * np.sqrt(252)

        return result


def format_backtest_report(result: BacktestResult) -> str:
    """Format backtest results as a readable report."""
    pf_str = f"{result.profit_factor:.2f}" if result.profit_factor > 0 else "N/A"

    lines = [
        f"{'=' * 55}",
        f"  BACKTEST: {result.symbol}",
        f"{'=' * 55}",
        f"  Период:           {result.period}",
        f"  Свечей:           {result.total_candles}",
        f"  Уровней найдено:  {result.levels_detected}",
        f"  Сигналов:         {result.total_signals}",
        f"  Сделок:           {result.total_trades}",
        f"{'─' * 55}",
        f"  РЕЗУЛЬТАТЫ",
        f"{'─' * 55}",
        f"  Побед:            {result.wins}",
        f"  Проигрышей:       {result.losses}",
        f"  Безубытков:       {result.breakeven}",
        f"  Win Rate:         {result.win_rate:.1%}",
        f"  Ср. выигрыш:     {result.avg_win_pct:+.2%}",
        f"  Ср. проигрыш:    {result.avg_loss_pct:+.2%}",
        f"  Profit Factor:    {pf_str}",
        f"{'─' * 55}",
        f"  КАПИТАЛ",
        f"{'─' * 55}",
        f"  Итого P&L:        {result.total_pnl_pct:+.2%}",
        f"  Макс. просадка:   {result.max_drawdown_pct:.2%}",
        f"  Sharpe Ratio:     {result.sharpe_ratio:.2f}",
        f"  Лучшая сделка:    {result.best_trade_pnl:+.2%}",
        f"  Худшая сделка:    {result.worst_trade_pnl:+.2%}",
        f"  Ср. удержание:    {result.avg_hold_candles:.1f} свечей",
        f"{'=' * 55}",
    ]

    if result.trades:
        lines.append("")
        lines.append(f"  {'#':>3} {'Напр':>5} {'Грейд':>5} {'Вход':>10} {'Выход':>10} {'P&L':>8} {'Причина':>10} {'Свечей':>6}")
        lines.append(f"  {'─' * 60}")
        for i, t in enumerate(result.trades, 1):
            lines.append(
                f"  {i:>3} {t.direction:>5} {t.grade:>5} {t.entry_price:>10.2f} "
                f"{t.exit_price:>10.2f} {t.pnl_pct:>+7.2%} "
                f"{t.exit_reason:>10} {t.candles_held:>6}"
            )

    if not result.trades:
        lines.append("")
        lines.append("  Сделок не было. Попробуйте:")
        lines.append("  - Другой символ (альткоины более волатильны)")
        lines.append("  - Больше данных: python main.py --backtest SOLUSDT")
        lines.append("  - Монеты с трендом: SOLUSDT, DOGEUSDT, XRPUSDT")

    return "\n".join(lines)
