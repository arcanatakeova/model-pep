# AI Autonomous Trader

A production-quality, autonomous AI trading bot covering **Cryptocurrency**, **Forex**, and **Stock/ETF** markets. Uses an ensemble of technical analysis strategies, regime-aware signal weighting, and strict risk management.

---

## Architecture

```
trader/
├── main.py           # Entry point, CLI, main trading loop
├── config.py         # All parameters (risk, APIs, strategies)
├── data_fetcher.py   # Free public API integrations
├── indicators.py     # RSI, MACD, Bollinger, EMA, Momentum, Volume
├── portfolio.py      # Position tracking, P&L, persistence
├── risk_manager.py   # Position sizing, circuit breakers
├── executor.py       # Paper/live trade execution
└── strategies/
    ├── ensemble.py   # Signal aggregation engine
    └── scanner.py    # Multi-market parallel scanner
```

---

## Free Data Sources (No API Key Required)

| Source | Markets | Endpoint |
|--------|---------|----------|
| **CoinGecko** | Crypto OHLCV, market cap | `api.coingecko.com/api/v3` |
| **CryptoCompare** | Crypto hourly OHLCV | `min-api.cryptocompare.com` |
| **CoinCap** | Real-time crypto prices | `api.coincap.io/v2` |
| **Open Exchange Rates** | Forex rates | `open.er-api.com/v6` |
| **Yahoo Finance** | Stocks, ETFs (via yfinance) | unofficial |
| **Messari** | Crypto fundamentals | `data.messari.io/api/v1` |

---

## Signal Engine

### Indicators (Ensemble Weighted)

| Indicator | Default Weight | Purpose |
|-----------|---------------|---------|
| RSI (14) | 20% | Overbought/oversold detection |
| MACD (12/26/9) | 20% | Trend direction and crossovers |
| Bollinger Bands (20, 2σ) | 15% | Mean reversion + breakouts |
| EMA Crossover (9/21) | 20% | Golden/death cross trend following |
| Momentum (10-period) | 15% | Rate-of-change and trend strength |
| Volume Analysis | 10% | Price/volume confirmation |

### Regime Detection

The bot automatically detects market regime and adjusts weights:
- **Trending**: Boost EMA crossover (+50%) and momentum (+40%)
- **Ranging**: Boost RSI (+50%) and Bollinger Bands (+50%)
- **Volatile**: Boost volume (+100%) and MACD (+30%)

### Signal Score: `[-1.0, +1.0]`
- `>= +0.45` → **BUY**
- `<= -0.45` → **SELL**
- Between → **HOLD**

---

## Risk Management

| Parameter | Default | Description |
|-----------|---------|-------------|
| Risk per trade | 2% of equity | Kelly-based position sizing |
| Max position | 10% of equity | Single position cap |
| Max open positions | 8 | Diversification limit |
| Stop loss | ATR × 2 | Dynamic, volatility-adjusted |
| Take profit | ATR × 4 | 2:1 reward/risk ratio |
| Trailing stop | 2.5% | Locks in profits on winners |
| Daily loss limit | 5% | Circuit breaker |
| Max drawdown | 15% | Portfolio protection circuit breaker |

---

## Quick Start

### 1. Install Dependencies

```bash
cd model-pep
pip install -r requirements.txt
```

### 2. Configure (Optional)

```bash
cp .env.example .env
# Edit .env — only needed for optional API keys
```

### 3. Run

```bash
# Paper trading bot (safe simulation, default)
python trader/main.py

# One-shot market scan — see what the bot is seeing right now
python trader/main.py --scan

# Check portfolio status
python trader/main.py --status

# Full performance report (JSON)
python trader/main.py --report

# Custom scan interval (every 60 seconds for faster testing)
python trader/main.py --interval 60

# Live trading (requires exchange API keys in .env)
python trader/main.py --live
```

---

## Configuration (`trader/config.py`)

All parameters are centralized in `config.py`:

```python
PAPER_TRADING = True           # False = real money, real trades
INITIAL_CAPITAL = 10_000.0     # Starting paper balance (USD)
SCAN_INTERVAL_SEC = 300        # Scan every 5 minutes
MAX_OPEN_POSITIONS = 8         # Portfolio concentration limit
RISK_PER_TRADE_PCT = 0.02      # 2% of equity at risk per trade
CRYPTO_TOP_N = 50              # Watch top 50 coins by market cap
```

---

## Output Files

| File | Content |
|------|---------|
| `trader.log` | Full operation log |
| `trades.json` | Portfolio state + trade history |
| `equity_curve.json` | Timestamped equity snapshots |

---

## Live Trading

> **WARNING**: Live trading uses real money. Always test extensively in paper mode first.

1. Set exchange API keys in `.env`
2. Ensure keys have **spot trading** permissions only (no withdrawals)
3. Start with small capital: `INITIAL_CAPITAL = 500`
4. Run: `python trader/main.py --live`

Supported exchanges: **Binance** (default). Add others via `ccxt` in `executor.py`.

---

## Strategy Performance Characteristics

The ensemble approach is designed to:
- **Avoid overtrading**: Minimum signal strength threshold prevents noise trades
- **Ride trends**: EMA crossover + momentum favors trend continuation
- **Mean-revert in ranges**: RSI + Bollinger exploit ranging markets
- **Protect capital**: 2:1 R/R ratio + trailing stops + circuit breakers
- **Diversify**: Multi-market scanning finds opportunities when one market is flat

---

## Disclaimer

This software is for **educational and research purposes**. Trading involves substantial risk of financial loss. Past performance of any strategy does not guarantee future results. Use paper trading mode to validate performance before risking real capital.
