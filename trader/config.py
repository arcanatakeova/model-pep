"""
AI Trader Configuration
All trading parameters, API endpoints, and strategy weights.
"""
import os

# ─── Trading Mode ─────────────────────────────────────────────────────────────
PAPER_TRADING = True          # Set False to enable live trading (requires API keys)
INITIAL_CAPITAL = 10_000.0    # Starting capital in USD

# ─── Risk Management ──────────────────────────────────────────────────────────
MAX_POSITION_PCT = 0.10       # Max 10% of portfolio per position
MAX_OPEN_POSITIONS = 8        # Maximum concurrent positions
STOP_LOSS_PCT = 0.03          # 3% stop loss
TAKE_PROFIT_PCT = 0.06        # 6% take profit (2:1 R/R ratio)
RISK_PER_TRADE_PCT = 0.02     # Risk 2% of portfolio per trade (Kelly-based)
MIN_SIGNAL_STRENGTH = 0.45    # Minimum ensemble score to trigger trade
TRAILING_STOP_PCT = 0.025     # 2.5% trailing stop

# ─── Market Coverage ──────────────────────────────────────────────────────────
CRYPTO_TOP_N = 50             # Watch top N cryptocurrencies by market cap
CRYPTO_MIN_VOLUME_USD = 5_000_000   # Minimum 24h volume to consider

# Target crypto assets (overrides top-N if set)
CRYPTO_WATCHLIST = [
    "bitcoin", "ethereum", "solana", "bnb", "avalanche-2",
    "chainlink", "polkadot", "cardano", "polygon", "arbitrum",
    "optimism", "uniswap", "aave", "maker", "compound-governance-token",
]

# Major forex pairs (base/quote)
FOREX_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    "USD/CAD", "USD/CHF", "NZD/USD",
]

# Stock/ETF symbols (via Yahoo Finance unofficial)
STOCK_WATCHLIST = [
    "SPY", "QQQ", "IWM",       # Major index ETFs
    "AAPL", "MSFT", "GOOGL",   # Tech megacaps
    "NVDA", "AMD", "TSM",       # Semiconductors
    "GLD", "SLV", "USO",        # Commodities ETFs
    "TLT", "HYG",               # Bond ETFs
]

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

# Exchange API keys for live trading
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_SECRET = os.getenv("COINBASE_SECRET", "")

# ─── Timing ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC = 300       # Scan markets every 5 minutes
OHLCV_CANDLES = 100           # Number of candles to fetch for analysis
CANDLE_INTERVAL = "1h"        # 1-hour candles for analysis (crypto)
DATA_CACHE_TTL = 60           # Cache market data for 60 seconds

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FILE = "trader.log"
TRADE_LOG_FILE = "trades.json"
PORTFOLIO_SNAPSHOT_INTERVAL = 3600  # Save portfolio snapshot every hour
