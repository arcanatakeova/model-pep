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

# ─── Disabled strategies (Solana DEX only) ────────────────────────────────────
FUTURES_ENABLED      = False   # No Binance futures
FUNDING_ARB_ENABLED  = False   # No funding rate arb
GRID_TRADING_ENABLED = False   # No grid trading
SCALP_ENABLED        = False   # No CEX scalping
FOREX_ENABLED        = False   # No forex (set once — do not override below)

# ─── Futures / Leverage constants (safe stubs — FUTURES_ENABLED=False above) ──
FUTURES_DAILY_LOSS_LIMIT = 0.05    # 5% daily hard stop if futures were ever re-enabled
FUTURES_RISK_PCT         = 0.01    # 1% equity risk per futures trade
MAX_LEVERAGE             = 3       # Conservative max leverage
LEVERAGE_BY_CONVICTION   = [       # (min_conviction, leverage) pairs
    (0.80, 3),
    (0.60, 2),
    (0.0,  1),
]
SCALP_INTERVAL_SEC       = 30      # Stub — scalping disabled

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

# Major forex pairs (kept for reference, not traded — FOREX_ENABLED=False above)
FOREX_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD"]

# ─── Forex Strategy parameters (stubs — FOREX_ENABLED=False) ─────────────────
FOREX_SESSION_FILTER = False  # Disabled
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
SCAN_INTERVAL_SEC = 5         # Scan markets every 5 seconds (short-trade focus)
OHLCV_CANDLES = 100           # Number of candles to fetch for analysis
CANDLE_INTERVAL = "1h"        # 1-hour candles for swing analysis
DATA_CACHE_TTL = 8            # Cache market data for 8 seconds (shorter than 5s cycle × 2)

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE = "trader.log"
TRADE_LOG_FILE = "trades.json"
PORTFOLIO_SNAPSHOT_INTERVAL = 300   # Save portfolio snapshot every 5 minutes

# ─── DEX / On-Chain Settings ──────────────────────────────────────────────────
DEX_MIN_SCORE = 0.45               # Quality gate — filters low-conviction setups
DEX_MAX_POSITION_USD = 500.0       # Max per DEX token (volatile = small size)
DEX_PREFERRED_CHAINS = ["solana"]  # Solana only
DEX_SCAN_INTERVAL_SEC = 8          # Scan DEX every 8s — catch entries faster
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
DEX_MIN_POSITION_USD = 4.0         # Minimum position size (covers Solana fees ~$0.50)
POSITION_VOL_SCALAR = 1.0          # Multiplier: size = base / (vol * scalar)
MAX_MEMECOIN_ALLOCATION_PCT = 0.40 # Max 40% of equity in memecoins total

# ─── Time-Based Exit Rules ──────────────────────────────────────────────────
DEX_MAX_HOLD_HOURS = 8             # Force exit after 8h — memecoins live fast
DEX_STALE_EXIT_HOURS = 2           # Exit if no momentum after 2 hours
DEX_STALE_MIN_GAIN_PCT = 0.02      # Minimum 2% gain to avoid stale exit

# ─── Partial Profit Taking ──────────────────────────────────────────────────
PARTIAL_PROFIT_ENABLED = True
PARTIAL_PROFIT_TIERS = [
    (0.10, 0.25),   # At +10% gain, sell 25% — quick capture on small pumps
    (0.25, 0.25),   # At +25% gain, sell another 25%
    (0.50, 0.25),   # At +50% gain, sell another 25%
    (1.00, 0.25),   # At +100% gain, sell last 25%
    # Any remainder rides with trailing stop
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
