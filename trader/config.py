"""
AI Trader Configuration
All trading parameters, API endpoints, and strategy weights.
"""
import os

# ─── Trading Mode ─────────────────────────────────────────────────────────────
PAPER_TRADING = True          # Set False to enable live trading (requires API keys)
INITIAL_CAPITAL = 10_000.0    # Starting capital in USD (overridden by wallet balance)

# ─── Risk Management ──────────────────────────────────────────────────────────
MAX_POSITION_PCT = 0.10       # Max 10% of portfolio per position
MAX_OPEN_POSITIONS = 12       # Maximum concurrent positions (more markets = more slots)
STOP_LOSS_PCT = 0.03          # 3% stop loss
TAKE_PROFIT_PCT = 0.06        # 6% take profit (2:1 R/R ratio)
RISK_PER_TRADE_PCT = 0.02     # Risk 2% of portfolio per trade (Kelly-based)
MIN_SIGNAL_STRENGTH = 0.38    # Minimum ensemble score to trigger trade
TRAILING_STOP_PCT = 0.025     # 2.5% trailing stop

# ─── Market Coverage ──────────────────────────────────────────────────────────
CRYPTO_TOP_N = 5              # Only top few coins (SOL focus, CEX scanner minimal)
CRYPTO_MIN_VOLUME_USD = 1_000_000   # Minimum 24h volume to consider

# Target crypto assets (overrides top-N if set)
CRYPTO_WATCHLIST = [
    "solana",
]

# Major forex pairs — disabled (Solana focus)
FOREX_PAIRS = []

# Stock/ETF symbols — disabled (Solana focus)
STOCK_WATCHLIST = []

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
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")

# Exchange API keys for live CEX trading (optional)
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET  = os.getenv("BINANCE_SECRET", "")
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_SECRET  = os.getenv("COINBASE_SECRET", "")

# ─── Timing ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC = 60        # Scan markets every 60 seconds (every minute)
OHLCV_CANDLES = 100           # Number of candles to fetch for analysis
CANDLE_INTERVAL = "1h"        # 1-hour candles for analysis (crypto)
DATA_CACHE_TTL = 45           # Cache market data for 45 seconds

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

# ─── Solana / Phantom Wallet ──────────────────────────────────────────────────
PHANTOM_PRIVATE_KEY = os.getenv("PHANTOM_PRIVATE_KEY", "")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
SOL_TRADE_SIZE_USD = 50.0          # Default Solana trade size in USD
SOL_MAX_SLIPPAGE_BPS = 150         # 1.5% max slippage for Solana swaps
SOL_PRIORITY_FEE_LAMPORTS = 10000  # Priority fee for fast execution

# ─── Polymarket ───────────────────────────────────────────────────────────────
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")  # Polygon EVM key
POLYMARKET_MIN_EDGE = 0.04         # Minimum 4% edge to trade
POLYMARKET_MIN_VOLUME = 5_000      # Minimum $5k/24h market volume
POLYMARKET_MAX_POSITION_USD = 200  # Max per prediction market position
POLYMARKET_SCAN_INTERVAL_SEC = 120 # Scan Polymarket every 2 minutes

# ─── Compounding ──────────────────────────────────────────────────────────────
COMPOUND_ALL_PROFITS = True        # Always reinvest — never withdraw
TARGET_DAILY_RETURN_PCT = 0.5     # 0.5%/day target = ~520% APY compounded
API_COST_MONTHLY_USD = 0.0         # Using only free APIs (no cost)
API_10X_TARGET = True              # Ensure returns >> 10x API costs
