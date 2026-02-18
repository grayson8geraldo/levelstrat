"""
Backtest Engine — replays historical data to validate strategy performance.

Features:
  - Downloads historical OHLCV data
  - Replays candle-by-candle through the strategy pipeline
  - Simulates entries/exits with stop loss and take profit
  - Calculates comprehensive performance metrics
  - Outputs results as formatted report
"""

import pandas as pd
import numpy as np
import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.config import (
    TF_WORKING, TF_ENTRY, TF_CONTEXT, TF_DIRECTION,
    CANDLE_LIMIT, STOP_ATR_MULT, TP1_R, TP2_R, TP3_R,
    TP1_PCT, TP2_PCT, TP3_PCT, ATR_PERIOD,
    MIN_CONFIRMATIONS, RISK_PER_TRADE_PCT,
)
from src.data_fetcher import DataFetcher
from src.indicators import compute_indicators, get_trend_direction
from src.diagonal_levels import detect_diagonal_levels
from src.signal_engine import evaluate_signal
from src.screener import CoinScreener

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
    exit_reason: str = ""          # "tp1", "tp2", "tp3", "stop", "time_stop"
    pnl_pct: float = 0.0
    pnl_r: float = 0.0
    confirmations: int = 0
    level_strength: float = 0.0
    scenario: str = ""
    is_open: bool = True
    candles_held: int = 0
    partial_exits: list = field(default_factory=list)  # Track partial TP fills
    remaining_pct: float = 1.0     # Remaining position percentage


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
        limit: int = 500,
        walk_forward_window: int = 200,
    ) -> BacktestResult:
        """
        Run backtest on a single symbol.

        Uses walk-forward approach:
        - Window of `walk_forward_window` candles for level detection
        - Steps forward one candle at a time
        - Evaluates signals at each step
        - Simulates trade execution
        """
        logger.info(f"Backtest: starting {symbol} on {timeframe}, {limit} candles")

        # Fetch data
        df_full = self.fetcher.fetch_ohlcv(symbol, timeframe, limit=limit)
        if df_full is None or len(df_full) < walk_forward_window + 50:
            logger.error(f"Insufficient data for {symbol}")
            return BacktestResult(symbol=symbol, period="insufficient data")

        # Fetch context timeframes
        df_4h = self.fetcher.fetch_ohlcv(symbol, TF_CONTEXT, limit=CANDLE_LIMIT)
        df_1h = self.fetcher.fetch_ohlcv(symbol, TF_DIRECTION, limit=CANDLE_LIMIT)

        df_4h_ind = compute_indicators(df_4h) if df_4h is not None else None
        df_1h_ind = compute_indicators(df_1h) if df_1h is not None else None
        trend_4h = get_trend_direction(df_4h_ind) if df_4h_ind is not None else "neutral"
        trend_1h = get_trend_direction(df_1h_ind) if df_1h_ind is not None else "neutral"

        result = BacktestResult(
            symbol=symbol,
            period=f"{df_full.index[0]} to {df_full.index[-1]}",
            total_candles=len(df_full),
        )

        open_trades: list[BacktestTrade] = []
        equity = 0.0
        result.equity_curve = [0.0]

        # Walk forward
        for end_idx in range(walk_forward_window, len(df_full)):
            start_idx = max(0, end_idx - walk_forward_window)
            window = df_full.iloc[start_idx:end_idx + 1]

            current_candle = df_full.iloc[end_idx]
            current_high = float(current_candle["high"])
            current_low = float(current_candle["low"])
            current_close = float(current_candle["close"])

            # Update open trades
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
                    # Add partial profits already taken
                    trade.pnl_pct += sum(p["pnl"] for p in trade.partial_exits)
                    equity += trade.pnl_pct
                    closed_this_candle.append(trade)
                    continue

                # Check TP levels (partial exits)
                tp_levels = [
                    (trade.tp1, TP1_PCT, "tp1"),
                    (trade.tp2, TP2_PCT, "tp2"),
                    (trade.tp3, TP3_PCT, "tp3"),
                ]

                for tp_price, tp_pct, tp_name in tp_levels:
                    if any(p["tp"] == tp_name for p in trade.partial_exits):
                        continue  # Already took this TP

                    hit_tp = False
                    if trade.direction == "LONG" and current_high >= tp_price:
                        hit_tp = True
                    elif trade.direction == "SHORT" and current_low <= tp_price:
                        hit_tp = True

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

                        # Move stop to breakeven after TP1
                        if tp_name == "tp1" and trade.is_open:
                            trade.stop_loss = trade.entry_price

                # Time stop: close after 20 candles
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

            # Remove closed trades from open list
            open_trades = [t for t in open_trades if t.is_open]

            # Skip signal detection if we have open trades
            if open_trades:
                result.equity_curve.append(equity)
                continue

            # Detect new signals
            df_window_ind = compute_indicators(window)
            if df_window_ind is None:
                result.equity_curve.append(equity)
                continue

            levels = detect_diagonal_levels(df_window_ind, timeframe)
            if not levels:
                result.equity_curve.append(equity)
                continue

            # Use last 50 candles as "entry" timeframe approximation
            df_entry_approx = window.iloc[-50:] if len(window) >= 50 else window

            for level in levels[:3]:  # Top 3 levels
                signal = evaluate_signal(
                    symbol=symbol,
                    scenario="uptrend" if trend_1h == "bullish" else ("downtrend" if trend_1h == "bearish" else "uptrend"),
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
                    scenario=signal.scenario,
                )
                open_trades.append(trade)
                result.trades.append(trade)
                break  # One trade at a time

            result.equity_curve.append(equity)

        # Close any remaining open trades at market
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

        # Calculate final statistics
        result.total_trades = len(result.trades)
        closed_trades = [t for t in result.trades if not t.is_open]

        for trade in closed_trades:
            if trade.pnl_pct > 0:
                trade.exit_reason_category = "win"
                result.wins += 1
            elif trade.pnl_pct < -0.001:
                trade.exit_reason_category = "loss"
                result.losses += 1
            else:
                result.breakeven += 1

        if result.total_trades > 0:
            result.win_rate = result.wins / result.total_trades

        winning_pnls = [t.pnl_pct for t in closed_trades if t.pnl_pct > 0]
        losing_pnls = [t.pnl_pct for t in closed_trades if t.pnl_pct < 0]

        result.avg_win_pct = np.mean(winning_pnls) if winning_pnls else 0
        result.avg_loss_pct = np.mean(losing_pnls) if losing_pnls else 0
        result.total_pnl_pct = sum(t.pnl_pct for t in closed_trades)
        result.best_trade_pnl = max(winning_pnls) if winning_pnls else 0
        result.worst_trade_pnl = min(losing_pnls) if losing_pnls else 0

        total_wins_sum = sum(winning_pnls) if winning_pnls else 0
        total_loss_sum = abs(sum(losing_pnls)) if losing_pnls else 0
        result.profit_factor = total_wins_sum / total_loss_sum if total_loss_sum > 0 else float("inf")

        if closed_trades:
            result.avg_hold_candles = np.mean([t.candles_held for t in closed_trades])

        # Max drawdown
        peak = 0
        max_dd = 0
        for val in result.equity_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = max_dd

        # Sharpe ratio (simplified: daily returns)
        if len(result.equity_curve) > 1:
            returns = np.diff(result.equity_curve)
            if np.std(returns) > 0:
                result.sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252)

        return result


def format_backtest_report(result: BacktestResult) -> str:
    """Format backtest results as a readable report."""
    lines = [
        f"{'=' * 50}",
        f"  BACKTEST REPORT: {result.symbol}",
        f"{'=' * 50}",
        f"  Period:          {result.period}",
        f"  Total candles:   {result.total_candles}",
        f"  Signals found:   {result.total_signals}",
        f"  Trades taken:    {result.total_trades}",
        f"{'─' * 50}",
        f"  PERFORMANCE",
        f"{'─' * 50}",
        f"  Wins:            {result.wins}",
        f"  Losses:          {result.losses}",
        f"  Breakeven:       {result.breakeven}",
        f"  Win Rate:        {result.win_rate:.1%}",
        f"  Avg Win:         {result.avg_win_pct:+.2%}",
        f"  Avg Loss:        {result.avg_loss_pct:+.2%}",
        f"  Profit Factor:   {result.profit_factor:.2f}",
        f"{'─' * 50}",
        f"  EQUITY",
        f"{'─' * 50}",
        f"  Total PnL:       {result.total_pnl_pct:+.2%}",
        f"  Max Drawdown:    {result.max_drawdown_pct:.2%}",
        f"  Sharpe Ratio:    {result.sharpe_ratio:.2f}",
        f"  Best Trade:      {result.best_trade_pnl:+.2%}",
        f"  Worst Trade:     {result.worst_trade_pnl:+.2%}",
        f"  Avg Hold:        {result.avg_hold_candles:.1f} candles",
        f"{'=' * 50}",
    ]

    # Trade list
    if result.trades:
        lines.append("\n  TRADE LOG:")
        lines.append(f"  {'#':>3} {'Dir':>5} {'Entry':>10} {'Exit':>10} {'PnL':>8} {'Reason':>10} {'Hold':>5}")
        lines.append(f"  {'─' * 55}")
        for i, t in enumerate(result.trades, 1):
            lines.append(
                f"  {i:>3} {t.direction:>5} {t.entry_price:>10.4f} "
                f"{t.exit_price:>10.4f} {t.pnl_pct:>+7.2%} "
                f"{t.exit_reason:>10} {t.candles_held:>5}"
            )

    return "\n".join(lines)
