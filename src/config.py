"""
DLS Strategy Configuration
All strategy parameters in one place.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Bybit API ──────────────────────────────────────────────
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

# ── Telegram ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Coin Screening ─────────────────────────────────────────
MIN_VOLUME_24H = 20_000_000          # $20M minimum 24h volume
MAX_SPREAD_PCT = 0.05                 # 0.05% max spread
MAX_LISTING_AGE_DAYS = 60             # New listings: up to 60 days

# ── Timeframes ─────────────────────────────────────────────
TF_CONTEXT = "4h"                     # Global trend
TF_DIRECTION = "1h"                   # Medium-term trend
TF_WORKING = "15m"                    # Main working timeframe
TF_ENTRY = "5m"                       # Precise entry

CANDLE_LIMIT = 200                    # Number of candles to fetch per TF

# ── Diagonal Level Detection ──────────────────────────────
MIN_TOUCHES = 3                       # Minimum touches for valid level
TOUCH_ZONE_PCT = {                    # Touch zone width by timeframe
    "1m": 0.0005,
    "5m": 0.0010,
    "15m": 0.0015,
    "1h": 0.0025,
    "4h": 0.0040,
}
MIN_CANDLES_BETWEEN_TOUCHES = 5       # Min candles between touches
MIN_BOUNCE_PCT = 0.003                # 0.3% min bounce after touch
MIN_ANGLE_DEG = 15                    # Min trendline angle
MAX_ANGLE_DEG = 70                    # Max trendline angle (>70 = pump)
SWING_LOOKBACK = 3                    # Candles left/right for swing detection

# ── Indicators ─────────────────────────────────────────────
EMA_FAST = 9
EMA_MEDIUM = 21
EMA_SLOW = 50
EMA_GLOBAL = 200

RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
RSI_EXTREME_OB = 85
RSI_EXTREME_OS = 15

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

ATR_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0

VOLUME_MA_PERIOD = 20
VOLUME_SURGE_MULT = 1.5              # 150% of MA for bounce
VOLUME_BREAKOUT_MULT = 2.0           # 200% of MA for breakout

# ── Entry Rules ────────────────────────────────────────────
MIN_CONFIRMATIONS = 3                 # Minimum 3 out of 5 confirmations
ENTRY_TYPES = ["aggressive", "conservative", "breakout", "retest"]

# ── Exit Rules ─────────────────────────────────────────────
TP1_R = 1.0                          # Take profit 1: 1R
TP2_R = 2.0                          # Take profit 2: 2R
TP3_R = 3.0                          # Take profit 3: 3R
TP1_PCT = 0.30                        # Close 30% at TP1
TP2_PCT = 0.40                        # Close 40% at TP2
TP3_PCT = 0.30                        # Close 30% at TP3
STOP_ATR_MULT = 0.5                   # Stop = level ± 0.5 * ATR
TIME_STOP_CANDLES = 20                # Close after 20 candles if no move

# ── Risk Management ────────────────────────────────────────
RISK_PER_TRADE_PCT = 0.01            # 1% risk per trade
MAX_DAILY_LOSS_PCT = 0.03            # 3% max daily loss
MAX_WEEKLY_LOSS_PCT = 0.05           # 5% max weekly loss
MAX_OPEN_POSITIONS = 2
MAX_LEVERAGE = 10
RECOMMENDED_LEVERAGE = 5
COUNTER_TREND_SIZE_MULT = 0.50       # 50% size for counter-trend
PUMP_SIZE_MULT = 0.75                # 75% size for pump trades
NEW_LISTING_SIZE_MULT = 0.75         # 75% size for new listings

# ── Pump Detection ─────────────────────────────────────────
PUMP_MIN_MOVE_PCT = 0.10             # 10% minimum move
PUMP_MIN_VOLUME_MULT = 3.0           # 300% of average volume
PUMP_MIN_CONSECUTIVE_CANDLES = 5     # 5+ same-direction candles
PUMP_DEFLATION_MIN_RETRACE = 0.03    # 3% retrace to confirm deflation
PUMP_MAX_AGE_HOURS = 24              # Don't trade pumps older than 24h

# ── Scanner ────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 60           # Scan every 60 seconds
TOP_COINS_TO_SCAN = 50               # Scan top 50 coins by volume
