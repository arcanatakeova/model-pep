# AI Trader v3.0 — Autonomous 24/7 Leveraged Bot

> Trades crypto (CEX + DEX), stocks, forex, and Solana memecoins — with leverage — around the clock. Designed to compound profits without ever stopping.

---

## Quick Start (5 minutes)

```bash
# 1. Install dependencies
cd model-pep
pip3 install -r requirements.txt
pip3 install ccxt          # Required for Binance Futures (leveraged trading)

# 2. Set up your keys
cp .env.example .env
nano .env                 # Fill in your API keys (see "API Keys" section below)

# 3. Verify it works (paper mode — no real money)
python trader/main.py --scan

# 4. Start paper trading
python trader/main.py

# 5. Go live 24/7
cd trader
./run_forever.sh --live
```

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.9+ |
| pip packages | see `requirements.txt` |
| ccxt | `pip3 install ccxt` (for Binance Futures) |

```bash
# Install everything
pip3 install -r requirements.txt
pip3 install ccxt
```

---

## API Keys Setup

Copy `.env.example` to `.env` and fill in the keys you need:

```bash
cp .env.example .env
```

| Key | Where to Get It | Required For |
|-----|----------------|--------------|
| `PHANTOM_PRIVATE_KEY` | Phantom → Settings → Security & Privacy → Export Private Key | Solana DEX trading (Jupiter swaps) |
| `BINANCE_API_KEY` | Binance → Account → API Management → Create API | Spot + Futures live trading |
| `BINANCE_SECRET` | Same page as above | Spot + Futures live trading |
| `POLYMARKET_PRIVATE_KEY` | MetaMask / any Polygon wallet → export private key | Prediction market trading |
| `SOLANA_RPC_URL` | Optional — use a private RPC (e.g., Helius, Triton) for faster execution | Faster Solana transactions |
| `COINGECKO_API_KEY` | CoinGecko Pro — optional, removes rate limits | Higher data freshness |

> **Security:** Never commit `.env` to git. It's in `.gitignore` by default.

---

## Running the Bot

### Paper mode (no real money — safe to test)
```bash
python trader/main.py
```

### Live trading (real money)
```bash
python trader/main.py --live
```

### 24/7 mode — auto-restarts on crash
```bash
cd trader
./run_forever.sh --live
```

### Background (detached, survives terminal close)
```bash
cd trader
nohup ./run_forever.sh --live > trader.log 2>&1 &
echo "Bot started. Monitor with: tail -f trader/trader.log"
```

### Start on system boot (systemd)
```bash
# Edit the WorkingDirectory and ExecStart paths in trader.service if needed
sudo cp trader/trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trader
sudo systemctl start trader

# Check it's running
sudo systemctl status trader

# Stop it
sudo systemctl stop trader
```

---

## Updating the Bot

### One-command update (pulls latest code + restarts automatically)
```bash
cd trader
./update.sh
```

### Update without restarting
```bash
./update.sh --no-restart
```

### Force restart in a specific mode after update
```bash
./update.sh --live     # update + restart live
./update.sh --paper    # update + restart paper
```

### What `update.sh` does
1. Backs up `trades.json` + `dex_positions.json` to `backups/` (safety)
2. Gracefully shuts down the running bot (SIGTERM, waits up to 30s)
3. `git pull` (with automatic retry on network failure)
4. `pip3 install -r requirements.txt --upgrade`
5. Syntax-checks `main.py` before restarting
6. Shows what changed (git log)
7. Restarts the bot in the same mode it was running before

---

## Monitoring

```bash
# Watch live logs
tail -f trader/trader.log

# Portfolio status + open positions
python trader/main.py --status

# One-shot market scan — see what signals are firing right now
python trader/main.py --scan

# Full performance report (JSON)
python trader/main.py --report

# Compound growth projections
python trader/main.py --growth
```

---

## Markets Traded

| Market | Data Source | Timeframe | Leverage |
|--------|------------|-----------|----------|
| Crypto CEX (BTC/ETH/SOL) | CryptoCompare | 1h candles | No (spot) |
| Crypto Futures (BTC/ETH/SOL) | CryptoCompare → Binance Futures | 1h swings | 2–8x |
| Crypto Scalps (BTC/ETH/SOL) | CryptoCompare 5m | 5m candles | 2–8x |
| Solana DEX Tokens | DEX Screener | Real-time | No (spot) |
| Solana On-Chain Swaps | Jupiter Aggregator | Real-time | No (spot) |
| Polymarket | Polymarket API | Every 2 min | No |
| Stocks/ETFs (SPY/QQQ/NVDA) | Yahoo Finance | 1h candles | No |
| Forex (EUR/USD, GBP/USD) | CryptoCompare | 1h candles | No |

---

## Leverage Settings

The bot uses **Binance USDT-M Perpetual Futures** for leveraged trades.

| Signal Conviction | Leverage Used |
|------------------|---------------|
| 80%+ | 8x |
| 65%+ | 5x |
| 50%+ | 3x |
| Below 50% | 2x |

**Safety features:**
- **Liquidation guard** — emergency-closes positions within 15% of liquidation price
- **Isolated margin** — each futures position uses only its own margin (losses capped)
- **Tighter daily loss limit** — 3% with futures active (vs 5% for spot-only)

To disable leverage:
```python
# trader/config.py
FUTURES_ENABLED = False
SCALP_ENABLED = False
```

---

## Configuration (`trader/config.py`)

Key parameters you may want to adjust:

| Parameter | Default | What It Controls |
|-----------|---------|-----------------|
| `INITIAL_CAPITAL` | 10,000 | Starting paper balance (USD) |
| `SCAN_INTERVAL_SEC` | 30 | Main cycle speed (seconds) |
| `MAX_OPEN_POSITIONS` | 20 | Max concurrent positions |
| `RISK_PER_TRADE_PCT` | 0.02 | % of equity at risk per spot trade |
| `FUTURES_RISK_PCT` | 0.01 | % of equity at risk per futures trade |
| `MAX_LEVERAGE` | 8 | Leverage hard cap |
| `MIN_SIGNAL_STRENGTH` | 0.30 | Minimum score to open a trade |
| `STOP_LOSS_PCT` | 0.03 | 3% stop loss |
| `TAKE_PROFIT_PCT` | 0.06 | 6% take profit |
| `DEX_MAX_POSITION_USD` | 500 | Max size per DEX token trade |
| `FUTURES_ENABLED` | True | Enable/disable leveraged futures |
| `SCALP_ENABLED` | True | Enable/disable 5m scalping |

---

## Architecture

```
trader/
├── main.py               # Entry point, main 30s loop
├── config.py             # All parameters — edit here
├── executor.py           # Spot + Futures trade execution (Binance, paper)
├── risk_manager.py       # Position sizing, leverage math, circuit breakers
├── portfolio.py          # Position tracking, P&L, persistence
├── compounding_engine.py # Profit reinvestment, allocation rebalancing
├── data_fetcher.py       # CoinGecko, CryptoCompare, yfinance, forex APIs
├── dex_screener.py       # DEX Screener — Solana memecoin sniping
├── token_safety.py       # RugCheck + honeypot detection
├── solana_wallet.py      # Phantom wallet + Jupiter DEX swaps
├── polymarket.py         # Prediction market trading (Polygon)
├── run_forever.sh        # 24/7 watchdog — auto-restarts on crash
├── update.sh             # One-command updater — pull, upgrade, restart
├── trader.service        # systemd unit for boot-time auto-start
└── strategies/
    ├── ensemble.py       # 6-indicator signal aggregation (1h)
    ├── scanner.py        # Multi-market parallel scanner
    └── scalper.py        # 5-minute scalp signal engine
```

---

## Output Files

| File | Content |
|------|---------|
| `trader/trader.log` | Full timestamped operation log |
| `trader/trades.json` | Portfolio state + all trade history |
| `trader/equity_curve.json` | Equity snapshots every cycle |
| `trader/dex_positions.json` | Open DEX positions (survives restarts) |
| `trader/backups/` | Automatic backups created by `update.sh` |

---

## Live Trading Checklist

Before enabling live trading, go through this checklist:

- [ ] Ran in paper mode for at least a few cycles (`python trader/main.py`)
- [ ] Verified `--scan` output shows signals for your markets
- [ ] Set `BINANCE_API_KEY` + `BINANCE_SECRET` in `.env`
- [ ] Binance API key has **Futures trading** enabled (for leverage)
- [ ] Binance API key has **NO withdrawal** permissions (security)
- [ ] Set `PHANTOM_PRIVATE_KEY` in `.env` (for Solana DEX)
- [ ] Tested Solana wallet connection: `python -c "from trader.solana_wallet import SolanaWallet; w = SolanaWallet(); print(w.get_sol_balance())"`
- [ ] Comfortable with the risk parameters in `config.py`
- [ ] Running on a machine that stays online (VPS/server, not laptop)

```bash
# Final launch
cd trader
./run_forever.sh --live
```

---

## Disclaimer

This software is for **educational purposes**. Cryptocurrency and futures trading involves substantial risk of total loss of capital. Leverage amplifies both gains and losses. Past performance does not guarantee future results. Run in paper mode first. Never trade more than you can afford to lose completely.
