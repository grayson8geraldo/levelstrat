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
    "1m": 0.0015,
    "5m": 0.0025,
    "15m": 0.0040,                    # Was 0.0015 — too tight, 0 signals
    "1h": 0.0050,
    "4h": 0.0060,
}
MIN_CANDLES_BETWEEN_TOUCHES = 5       # Min candles between touches
MIN_BOUNCE_PCT = 0.002                # 0.2% min bounce after touch (was 0.3%)
MIN_ANGLE_DEG = 10                    # Min trendline angle (was 15)
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
VOLUME_SURGE_MULT = 1.2              # 120% of MA for bounce (was 1.5 — too strict)
VOLUME_BREAKOUT_MULT = 1.8           # 180% of MA for breakout

# ── Entry Rules ────────────────────────────────────────────
MIN_CONFIRMATIONS = 3                 # Minimum 3 out of 5 confirmations
ENTRY_TYPES = ["aggressive", "conservative", "breakout", "retest"]

# ── Exit Rules ─────────────────────────────────────────────
TP1_R = 1.0                          # Take profit 1: 1R
TP2_R = 2.0                          # Take profit 2: 2R
TP3_R = 3.0                          # Take profit 3: 3R
TP1_PCT = 0.60                        # Close 60% at TP1 (was 30% — too little profit taken early)
TP2_PCT = 0.25                        # Close 25% at TP2
TP3_PCT = 0.15                        # Close 15% at TP3
STOP_ATR_MULT = 0.5                   # Stop = level ± 0.5 * ATR
TIME_STOP_CANDLES = 20                # Close after 20 candles if no move
BE_OFFSET_R = 0.0                     # After TP1, move stop to exact entry (BE)

# ── Trading Costs ─────────────────────────────────────────
TRADING_FEE_PCT = 0.00055              # Taker fee per side (Bybit 0.055%)
SLIPPAGE_PCT = 0.0003                  # Estimated slippage per side
TOTAL_COST_PER_SIDE = TRADING_FEE_PCT + SLIPPAGE_PCT  # ~0.085% per side

# ── Trailing Stop ─────────────────────────────────────────
TRAILING_AFTER_TP1 = True              # Enable dynamic trailing after TP1
TRAILING_STEP_R = 0.7                  # Trail stop 0.7R behind peak price (loose trail)

# ── Risk Management ────────────────────────────────────────
RISK_PER_TRADE_PCT = 0.01            # 1% risk per trade
MAX_DAILY_LOSS_PCT = 0.03            # 3% max daily loss
MAX_WEEKLY_LOSS_PCT = 0.05           # 5% max weekly loss
MAX_OPEN_POSITIONS = 2
MIN_SIGNAL_SCORE = 55                 # Minimum composite score to send (Grade B+)
MAX_LEVERAGE = 10
RECOMMENDED_LEVERAGE = 5
COUNTER_TREND_SIZE_MULT = 0.50       # 50% size for counter-trend
BLOCK_FULL_COUNTER_TREND = True      # Block when ALL TFs agree against direction
MAX_ENTRY_DISTANCE_PCT = 0.005       # 0.5% max distance from entry to send signal
PUMP_SIZE_MULT = 0.75                # 75% size for pump trades
NEW_LISTING_SIZE_MULT = 0.75         # 75% size for new listings

# ── Momentum Freshness ───────────────────────────────
MOMENTUM_RSI_EXHAUSTION_LONG = 65    # RSI above this for LONG = rally may be exhausted
MOMENTUM_RSI_EXHAUSTION_SHORT = 35   # RSI below this for SHORT = sell-off may be exhausted
MOMENTUM_RSI_PENALTY = 8             # Composite score penalty for RSI exhaustion
MOMENTUM_VOLUME_FADE_PENALTY = 4     # Penalty when volume surge was historical, not current

# ── Pump Detection ─────────────────────────────────────────
PUMP_MIN_MOVE_PCT = 0.10             # 10% minimum move
PUMP_MIN_VOLUME_MULT = 3.0           # 300% of average volume
PUMP_MIN_CONSECUTIVE_CANDLES = 5     # 5+ same-direction candles
PUMP_DEFLATION_MIN_RETRACE = 0.03    # 3% retrace to confirm deflation
PUMP_MAX_AGE_HOURS = 24              # Don't trade pumps older than 24h

# ── Confluence ─────────────────────────────────────────────
CONFLUENCE_FIB_BONUS = 25            # Score bonus for Fibonacci alignment
CONFLUENCE_HORIZONTAL_BONUS = 20     # Score bonus for horizontal S/R
CONFLUENCE_VPOC_BONUS = 20           # Score bonus for Volume POC
CONFLUENCE_ROUND_BONUS = 15          # Score bonus for round number
CONFLUENCE_EMA_BONUS = 20            # Score bonus for EMA proximity

# ── Derivatives Filter ────────────────────────────────────
FUNDING_EXTREME_THRESHOLD = 0.001    # 0.1% = crowded trade
FUNDING_KILL_SWITCH = 0.002          # 0.2% = no trades allowed

# ── Market Regime ─────────────────────────────────────────
VOL_EXTREME_THRESHOLD = 0.04        # 4% ATR/price = extreme volatility
MACRO_BLACKOUT_MINUTES = 30          # No trades near macro events
BTC_CRASH_THRESHOLD = -0.05          # -5% BTC = avoid alt trades
BTC_TREND_PENALTY = 15               # Score penalty when signal opposes BTC 4H trend

# ── Retry / Resilience ────────────────────────────────────
API_MAX_RETRIES = 3                  # Max retry attempts
API_RETRY_BASE_DELAY = 2            # Base delay in seconds (exponential)
API_DELAY_BETWEEN_COINS = 0.3       # Delay (s) between coin API calls to avoid rate limit

# ── Volume Acceleration ──────────────────────────────────
VOLUME_ACCEL_MIN_READINGS = 3        # Min readings before acceleration kicks in
VOLUME_ACCEL_LOOKBACK = 5            # Compare current vs N cycles ago
VOLUME_ACCEL_MAX_HISTORY = 15        # Max cached readings per coin
VOLUME_ACCEL_WEIGHT = 0.3            # How much acceleration affects sorting (0=none, 1=full)
VOLUME_ACCEL_MIN_24H = 10_000_000    # $10M — lower bar to catch coins heating up early

# ── Scanner ────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 60           # Scan every 60 seconds
TOP_COINS_TO_SCAN = 50               # Scan top 50 coins by volume
MAX_LEVELS_PER_COIN = 10             # Evaluate top N levels per coin (was 5)
DASHBOARD_INTERVAL_HOURS = 4         # Send dashboard every N hours

# ── Signal Dedup ─────────────────────────────────────────
SIGNAL_RESEND_COOLDOWN = 14400       # 4 hours before resending same signal
SIGNAL_RESEND_MIN_IMPROVEMENT = 10   # Resend early if score improved by ≥10
SIGNAL_DEDUP_PRICE_TOLERANCE = 0.02  # 2% — same level if within this range

# ── Position Monitor ────────────────────────────────────
POSITION_CHECK_INTERVAL = 60         # Check positions every scan cycle (seconds)

# ── OHLCV Cache ────────────────────────────────────────
OHLCV_CACHE_TTL = 30                 # Cache candles for 30 seconds
