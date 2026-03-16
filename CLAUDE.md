# Model-PEP: Multi-Market Trading System

## Project Overview

Automated trading bot covering three market domains:
- **Solana DEX** (primary): On-chain memecoin trading via Jupiter/Phantom wallet
- **Polymarket** (active): Prediction market trading with LLM probability estimation
- **CEX/Futures** (disabled): Binance futures, scalping, grid trading — code exists but all flags are `False`

## Architecture

```
trader/
  main.py              # Entry point + CLI (--live, --scan, --status, --report, --growth)
  orchestrator.py      # Coordinates multiple traders via BaseTrader interface
  config.py            # All parameters — single source of truth
  core/
    base_trader.py     # Abstract base class all traders inherit
    state_manager.py   # Thread-safe JSON persistence
    logging_setup.py   # Centralized logging
  solana_trader.py     # Solana DEX engine (extracted from old monolithic main.py)
  solana_wallet.py     # Phantom wallet + Jupiter swaps + Jito MEV bundles
  polymarket/          # Self-contained prediction market module (12 files)
    engine.py          # Core trading engine
    trader.py          # BaseTrader subclass
    probability_engine.py  # LLM-based probability (Claude/OpenAI)
    news_sentiment.py  # RSS news parsing
    smart_money.py     # Top trader tracking
    ...
  strategies/          # Signal generators (ensemble, scanner, scalper, grid, funding_arb)
  dashboard.py         # Streamlit real-time UI
  backtest.py          # Historical backtesting
```

## Key Patterns

- **BaseTrader abstract class** (`core/base_trader.py`): All traders implement `run_cycle()`, health checks, state persistence
- **Orchestrator** (`orchestrator.py`): Manages trader lifecycle, crash recovery, state aggregation
- **State files**: `bot_state.json`, `trades.json`, `dex_positions.json` — dashboard reads these
- **Config-driven**: Everything tunable in `config.py`, loaded from env vars with defaults
- **Thread safety**: `state_manager.py` handles concurrent access to shared state

## Development Commands

```bash
# Run
python trader/main.py                    # Paper trading (default)
python trader/main.py --live             # Live trading
python trader/main.py --scan             # One-shot market scan
python trader/main.py --status           # Portfolio status

# Monitor
tail -f trader/trader.log
streamlit run trader/dashboard.py

# Test
python -m pytest trader/
python trader/backtest.py --plot

# Deploy
./trader/run_forever.sh [--live]         # Watchdog with auto-restart
sudo systemctl start trader              # systemd service
./trader/update.sh                       # Safe update (backup, pull, restart)
```

## Important Conventions

- All trading parameters live in `config.py` — never hardcode values in trading logic
- JSON state files use atomic writes (write to `.tmp`, then rename) to prevent corruption
- Solana wallet operations require `PHANTOM_PRIVATE_KEY` in `.env`
- Polymarket requires `POLYMARKET_PRIVATE_KEY` (Polygon wallet)
- Risk limits: 18% max position size, 15 max open positions, 3% stop loss, 6% take profit
- DEX positions: $100 base, $750 max, 10h max hold, partial profit tiers at 25%/50%/100% gain

## File Sensitivity

- **Never commit**: `.env`, `*.json` state files, `trader.log`, `backups/`
- **Careful editing**: `config.py` (affects live trading), `solana_wallet.py` (handles real funds)
- **Safe to modify**: `strategies/`, `dashboard.py`, `backtest.py`, `polymarket/`
