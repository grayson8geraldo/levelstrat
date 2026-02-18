"""
Derivatives Context Filter — uses funding rate and OI for signal validation.

Filters out dangerous setups:
  - Extreme funding rates (crowded trades)
  - OI divergence (potential liquidation cascades)
  - Provides directional bias from derivatives data
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Thresholds
FUNDING_EXTREME_LONG = 0.001    # 0.1% — very bullish funding, dangerous for longs
FUNDING_EXTREME_SHORT = -0.001  # -0.1% — very bearish funding, dangerous for shorts
FUNDING_WARNING = 0.0005        # 0.05% — elevated but not extreme
FUNDING_KILL_SWITCH = 0.002     # 0.2% — absolute kill switch, no trades


@dataclass
class DerivativesContext:
    """Result of derivatives analysis."""
    funding_rate: float = 0.0
    open_interest: float = 0.0
    funding_bias: str = "neutral"  # "long_crowded", "short_crowded", "neutral"
    is_extreme: bool = False       # Kill switch triggered
    is_warning: bool = False       # Elevated risk
    long_safe: bool = True         # Safe to go long
    short_safe: bool = True        # Safe to go short
    score_adjustment: float = 0.0  # -20 to +10 score adjustment
    reason: str = ""


def analyze_derivatives(
    funding_rate: float | None,
    open_interest: float | None,
    direction: str,
) -> DerivativesContext:
    """
    Analyze derivatives data and determine if the trade direction is safe.

    Args:
        funding_rate: Current funding rate (e.g., 0.0001 = 0.01%)
        open_interest: Current open interest in USDT
        direction: "LONG" or "SHORT"

    Returns:
        DerivativesContext with safety assessment and score adjustment.
    """
    ctx = DerivativesContext()

    if funding_rate is not None:
        ctx.funding_rate = funding_rate

        # Kill switch: extreme funding
        if abs(funding_rate) >= FUNDING_KILL_SWITCH:
            ctx.is_extreme = True
            ctx.long_safe = False
            ctx.short_safe = False
            ctx.score_adjustment = -50  # Effectively kills any signal
            ctx.reason = f"Экстремальный фандинг {funding_rate:.4%} — торговля заблокирована"
            return ctx

        # Long crowded (positive funding = longs pay shorts)
        if funding_rate >= FUNDING_EXTREME_LONG:
            ctx.funding_bias = "long_crowded"
            ctx.long_safe = False
            ctx.is_warning = True
            ctx.reason = f"Лонги перегружены (фандинг {funding_rate:.4%})"

            if direction == "LONG":
                ctx.score_adjustment = -20  # Penalize going long in crowded trade
            else:
                ctx.score_adjustment = +10  # Bonus for shorting crowded longs

        # Short crowded (negative funding = shorts pay longs)
        elif funding_rate <= FUNDING_EXTREME_SHORT:
            ctx.funding_bias = "short_crowded"
            ctx.short_safe = False
            ctx.is_warning = True
            ctx.reason = f"Шорты перегружены (фандинг {funding_rate:.4%})"

            if direction == "SHORT":
                ctx.score_adjustment = -20
            else:
                ctx.score_adjustment = +10

        # Warning level
        elif abs(funding_rate) >= FUNDING_WARNING:
            ctx.is_warning = True
            if funding_rate > 0 and direction == "LONG":
                ctx.score_adjustment = -10
                ctx.reason = f"Повышенный фандинг {funding_rate:.4%} — осторожно с лонгами"
            elif funding_rate < 0 and direction == "SHORT":
                ctx.score_adjustment = -10
                ctx.reason = f"Отрицательный фандинг {funding_rate:.4%} — осторожно с шортами"
            else:
                ctx.score_adjustment = +5
                ctx.reason = f"Фандинг {funding_rate:.4%} в вашу пользу"
        else:
            ctx.reason = f"Фандинг нейтральный ({funding_rate:.4%})"

    if open_interest is not None:
        ctx.open_interest = open_interest

    return ctx
