"""
AI Trader Configuration
All trading parameters, API endpoints, and strategy weights.
"""
import os

# ─── Trading Mode ─────────────────────────────────────────────────────────────
PAPER_TRADING = False         # Live mode by default — real Solana DEX trades via Phantom
INITIAL_CAPITAL = 100_000.0   # Starting capital in USD (overridden by wallet balance)

# ─── Risk Management ──────────────────────────────────────────────────────────
MAX_POSITION_PCT = 0.10       # Max 10% of portfolio per position
MAX_OPEN_POSITIONS = 20       # Maximum concurrent positions
STOP_LOSS_PCT = 0.03          # 3% stop loss
TAKE_PROFIT_PCT = 0.06        # 6% take profit (2:1 R/R ratio)
RISK_PER_TRADE_PCT = 0.02     # Risk 2% of portfolio per trade (Kelly-based)
MIN_SIGNAL_STRENGTH = 0.22    # Minimum ensemble score to trigger trade (lower = more trades)
TRAILING_STOP_PCT = 0.025     # 2.5% trailing stop

# ─── Leverage / Futures (Binance USDT-M Perpetuals) ──────────────────────────
FUTURES_ENABLED = True                    # Trade leveraged futures alongside spot
DEFAULT_LEVERAGE = 3                      # Default leverage multiplier
MAX_LEVERAGE = 8                          # Hard cap (safety)
FUTURES_SYMBOLS = [                       # USDT-M perpetual pairs to trade with leverage
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
]
FUTURES_RISK_PCT = 0.01                   # 1% of equity at risk per futures trade
FUTURES_DAILY_LOSS_LIMIT = 0.05           # 5% daily hard stop — protects real SOL from catastrophic loss
# Maps (min_conviction_threshold, leverage_multiplier) — highest conviction first
# BUG FIX: was (0.00, 2) — any signal got 2x. Now requires 0.40 minimum.
LEVERAGE_BY_CONVICTION = [
    (0.80, 8),
    (0.65, 5),
    (0.50, 3),
    (0.40, 2),   # Min 40% conviction required for any leverage
]
MIN_FUTURES_CONVICTION = 0.40   # Hard gate: below this = no futures trade

# ─── Funding Rate Arbitrage ───────────────────────────────────────────────────
FUNDING_ARB_ENABLED = True
FUNDING_ARB_MIN_RATE = 0.0003       # 0.03% per 8h minimum (0.27%/day) to open arb
FUNDING_ARB_MAX_POSITION_USD = 2000 # Max USD per arb pair
FUNDING_ARB_SCAN_INTERVAL_SEC = 600 # Every 10 minutes (funding updates every 8h)

# ─── Grid Trading ─────────────────────────────────────────────────────────────
GRID_TRADING_ENABLED = True
GRID_SYMBOLS = ["BTC", "ETH", "SOL"]
GRID_SPACING_PCT = 0.004          # 0.4% between grid levels
GRID_LEVELS = 8                   # Levels on each side of center
GRID_SIZE_USD_PER_LEVEL = 50.0    # USD per order
GRID_MAX_TOTAL_USD = 2000.0       # Max total grid exposure per symbol
GRID_SCAN_INTERVAL_SEC = 60       # Check grid fills every 60s

# ─── Scalping (5-minute signals) ─────────────────────────────────────────────
SCALP_ENABLED = True
SCALP_INTERVAL_SEC = 30         # Run scalp scan every 30 seconds
SCALP_CANDLE_INTERVAL = "5m"    # 5-minute candle timeframe
SCALP_CANDLES = 50              # Number of 5m candles to fetch
SCALP_RSI_OVERSOLD = 25         # Aggressive RSI threshold for scalp buys
SCALP_RSI_OVERBOUGHT = 75       # Aggressive RSI threshold for scalp sells
SCALP_MIN_SCORE = 0.28          # Minimum score to fire a scalp trade
SCALP_SYMBOLS = ["BTC", "ETH", "SOL", "LINK", "AVAX", "MATIC"]  # Symbols to scalp

# ─── Market Coverage ──────────────────────────────────────────────────────────
CRYPTO_TOP_N = 30             # Scan top 30 coins — broader coverage, more signals
CRYPTO_MIN_VOLUME_USD = 5_000_000   # Minimum $5M 24h volume (filters noise)

# Target crypto assets (always scanned, merged with top-N)
CRYPTO_WATCHLIST = [
    "bitcoin",
    "ethereum",
    "solana",
    "chainlink",
    "avalanche-2",
    "the-open-network",
    "sui",
    "injective-protocol",
    "arbitrum",
    "optimism",
]

# Major forex pairs — enabled for pro-trader coverage
FOREX_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD"]

# ─── Forex Strategy (Professional Engine) ─────────────────────────────────────
FOREX_ENABLED        = True
FOREX_SESSION_FILTER = True   # Only trade during active London/NY/Tokyo windows
FOREX_MIN_ADX        = 20     # Skip ranging markets (ADX < 20 = no clear trend)
FOREX_MIN_SCORE      = 0.35   # Min signal score (higher bar than crypto 0.22)
FOREX_ATR_STOP_MULTIPLIER = 1.5  # Stop = 1.5× ATR from entry
FOREX_RR_RATIO       = 2.2    # Target = 2.2× stop (minimum R:R)
FOREX_MAX_CORRELATED_PAIRS = 1   # Max 1 position per correlation group
FOREX_SPREADS_PIPS = {        # Typical retail spread per pair (pips)
    "EUR/USD": 0.5, "GBP/USD": 0.8, "USD/JPY": 0.6,
    "AUD/USD": 0.7, "USD/CAD": 1.0,
}

# Stock/ETF symbols — enabled
STOCK_WATCHLIST = ["SPY", "QQQ", "NVDA", "TSLA", "META", "COIN", "MSTR", "MARA"]

# ─── Strategy Weights ─────────────────────────────────────────────────────────
STRATEGY_WEIGHTS = {
    "rsi":         0.20,   # RSI overbought/oversold
    "macd":        0.20,   # MACD crossover
    "bollinger":   0.15,   # Bollinger Band mean reversion
    "ema_cross":   0.20,   # EMA 9/21 crossover
    "momentum":    0.15,   # Price momentum
    "volume":      0.10,   # Volume analysis
}

# ─── Technical Indicator Parameters ───────────────────────────────────────────
RSI_PERIOD = 14
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0

EMA_FAST = 9
EMA_SLOW = 21

MOMENTUM_PERIOD = 10

# ─── API Endpoints (Free / No-Key Required) ───────────────────────────────────
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINCAP_BASE = "https://api.coincap.io/v2"
CRYPTOCOMPARE_BASE = "https://min-api.cryptocompare.com/data"
MESSARI_BASE = "https://data.messari.io/api/v1"
EXCHANGERATE_BASE = "https://open.er-api.com/v6"

# Optional API keys (from environment)
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
ALPHAVANTAGE_KEY  = os.getenv("ALPHAVANTAGE_KEY", "")
FINNHUB_KEY       = os.getenv("FINNHUB_KEY", "")

# ─── Birdeye API (Real-time Solana data) ──────────────────────────────────────
# Get key at https://birdeye.so — Starter plan ($99/mo) recommended
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")

# Exchange API keys for live CEX trading (optional)
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET  = os.getenv("BINANCE_SECRET", "")
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_SECRET  = os.getenv("COINBASE_SECRET", "")

# ─── Timing ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC = 15        # Scan markets every 15 seconds (parallel cycle = faster)
OHLCV_CANDLES = 100           # Number of candles to fetch for analysis
CANDLE_INTERVAL = "1h"        # 1-hour candles for swing analysis
DATA_CACHE_TTL = 12           # Cache market data for 12 seconds (shorter than 15s scan cycle)

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE = "trader.log"
TRADE_LOG_FILE = "trades.json"
PORTFOLIO_SNAPSHOT_INTERVAL = 300   # Save portfolio snapshot every 5 minutes

# ─── DEX / On-Chain Settings ──────────────────────────────────────────────────
DEX_MIN_SCORE = 0.35               # Lower threshold to catch more Solana memecoins
DEX_MAX_POSITION_USD = 500.0       # Max per DEX token (volatile = small size)
DEX_PREFERRED_CHAINS = ["solana"]  # Solana only
DEX_SCAN_INTERVAL_SEC = 45         # Scan DEX every 45s for faster memecoin catching
NEW_PAIR_MAX_AGE_HOURS = 48        # Consider pairs up to 48h old
NEW_PAIR_MIN_LIQUIDITY = 15_000    # $15k minimum liquidity (lower for memecoins)

# ─── Token Safety / Rug Protection (Balanced Mode) ──────────────────────────
MIN_SAFETY_SCORE = 0.45            # Minimum safety score to trade (raised: 0.35 was too permissive)
SAFETY_SCORE_WEIGHT = 0.20         # Weight of safety in overall token score
ENABLE_SELL_SIMULATION = True      # Honeypot check via Jupiter round-trip quote
SELL_SIM_AMOUNT_USD = 1.0          # Dollar amount for sell simulation
MAX_ROUND_TRIP_TAX_PCT = 0.30      # Max acceptable round-trip tax (30% = balanced)
RUGCHECK_CACHE_TTL = 300           # Cache safety results for 5 minutes
SAFETY_CHECK_TIMEOUT = 8           # Seconds before safety check times out
BLOCK_HONEYPOTS = True             # Hard block if sell simulation fails completely
MAX_TOP10_HOLDER_PCT = 0.70        # Penalize heavily if top 10 holders own > 70% (tighter for memecoins)

# ─── Volatility-Adjusted Position Sizing ────────────────────────────────────
DEX_BASE_POSITION_USD = 50.0       # Base position for memecoin trades
DEX_MIN_POSITION_USD = 10.0        # Minimum position size
POSITION_VOL_SCALAR = 1.0          # Multiplier: size = base / (vol * scalar)
MAX_MEMECOIN_ALLOCATION_PCT = 0.40 # Max 40% of equity in memecoins total

# ─── Time-Based Exit Rules ──────────────────────────────────────────────────
DEX_MAX_HOLD_HOURS = 24            # Force exit after 24 hours
DEX_STALE_EXIT_HOURS = 6           # Exit if no momentum after 6 hours
DEX_STALE_MIN_GAIN_PCT = 0.02     # Minimum 2% gain to avoid stale exit

# ─── Partial Profit Taking ──────────────────────────────────────────────────
PARTIAL_PROFIT_ENABLED = True
PARTIAL_PROFIT_TIERS = [
    (0.25, 0.25),   # At +25% gain, sell 25% of position
    (0.50, 0.25),   # At +50% gain, sell another 25%
    (1.00, 0.25),   # At +100% gain, sell another 25%
    # Remaining 25% rides with trailing stop
]

# ─── MEV / Sandwich Protection ──────────────────────────────────────────────
MEV_PROTECTION_ENABLED = True
MEV_MAX_SLIPPAGE_BPS = 100         # Tighter slippage (1%) for MEV protection
MEV_PRIORITY_FEE_LAMPORTS = 50000  # Higher priority fee to front-run sandwich

# ─── Concentration Limits ───────────────────────────────────────────────────
MAX_DEX_POSITIONS = 8              # Max concurrent DEX/memecoin positions
MAX_SAME_DEX_POSITIONS = 6         # Max positions on same DEX (e.g., Raydium/Pumpswap)
MIN_LIQUIDITY_RATIO = 0.10         # Position must be < 10% of pool liquidity

# ─── Solana / Phantom Wallet ──────────────────────────────────────────────────
PHANTOM_PRIVATE_KEY = os.getenv("PHANTOM_PRIVATE_KEY", "")
# Use Helius for premium RPC + priority fee estimation:
#   https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
SOL_TRADE_SIZE_USD        = 50.0    # Default Solana trade size in USD
SOL_MAX_SLIPPAGE_BPS      = 300     # 3% hard cap on slippage (dynamic calc stays under this)
SOL_PRIORITY_FEE_LAMPORTS = 100_000  # Fallback priority fee (lamports) when Helius unavailable

# ─── Jito MEV Bundle Protection ───────────────────────────────────────────────
# Jito routes transactions through the block engine to prevent sandwich attacks.
# The tip is paid in SOL to a random Jito tip account alongside the swap tx.
JITO_TIP_LAMPORTS = 1_000_000      # 0.001 SOL tip — market-rate to avoid MEV sandwiching

# ─── Polymarket ───────────────────────────────────────────────────────────────
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")  # Polygon EVM key
POLYMARKET_MIN_EDGE = 0.04         # Minimum 4% edge to trade
POLYMARKET_MIN_VOLUME = 5_000      # Minimum $5k/24h market volume
POLYMARKET_MAX_POSITION_USD = 200  # Max per prediction market position
POLYMARKET_SCAN_INTERVAL_SEC = 300 # Scan Polymarket every 5 minutes (was 2 min — too frequent)

# ─── Compounding ──────────────────────────────────────────────────────────────
COMPOUND_ALL_PROFITS = True        # Always reinvest — never withdraw
TARGET_DAILY_RETURN_PCT = 0.5     # 0.5%/day target = ~520% APY compounded
API_COST_MONTHLY_USD = 0.0         # Using only free APIs (no cost)
API_10X_TARGET = True              # Ensure returns >> 10x API costs
