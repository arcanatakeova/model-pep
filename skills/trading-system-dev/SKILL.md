---
name: trading-system-dev
description: Develops features, fixes bugs, and manages the multi-market trading bot (Solana DEX, Polymarket, CEX). Use when user asks to modify trading logic, add strategies, debug positions, update risk parameters, enhance the dashboard, or work on any trader/ module. Also use for "add indicator", "fix trading", "new strategy", "update config", "improve dashboard".
license: MIT
metadata:
  author: arcanatakeova
  version: 1.0.0
  category: trading-development
  tags: [solana, polymarket, dex, trading-bot, crypto]
---

# Trading System Development Skill

## Critical: Read Before Any Change

1. **Check if live trading is running** before modifying core files:
   ```bash
   pgrep -f "main.py.*--live" && echo "LIVE TRADING ACTIVE" || echo "Safe to edit"
   ```
2. **Never modify these during live trading**: `config.py`, `solana_wallet.py`, `executor.py`, `risk_manager.py`
3. **Always back up state** before risky changes:
   ```bash
   cp trader/trades.json trader/backups/trades_$(date +%s).json
   cp trader/dex_positions.json trader/backups/dex_$(date +%s).json 2>/dev/null
   ```

## Architecture Quick Reference

All traders inherit from `core/base_trader.py` (abstract base class):
- `run_cycle()` — main trading loop iteration
- `get_state()` — emit state for orchestrator aggregation
- `health_check()` — alive/ready status

The orchestrator (`orchestrator.py`) manages trader lifecycle:
- Registers traders, starts them in threads
- Aggregates state into `bot_state.json` for dashboard
- Handles crash recovery with configurable restart delays

State flows: **Trader** -> JSON state file -> **Orchestrator** aggregates -> `bot_state.json` -> **Dashboard** reads

## Instructions

### Adding a New Trading Strategy

1. Create file in `trader/strategies/` following the ensemble pattern:
   ```python
   # trader/strategies/your_strategy.py
   def generate_signals(data: pd.DataFrame, config: dict) -> list[dict]:
       """Return list of {symbol, action, strength, reason}"""
   ```
2. Register in `strategies/__init__.py`
3. Add config toggle in `config.py`: `YOUR_STRATEGY_ENABLED = bool(os.getenv("YOUR_STRATEGY_ENABLED", "False").lower() == "true")`
4. Wire into scanner or ensemble signal aggregation
5. Test with backtest: `python trader/backtest.py`

### Adding a New Technical Indicator

1. Add calculation to `trader/indicators.py`
2. Add weight in `config.py` `INDICATOR_WEIGHTS` dict
3. Wire into `strategies/ensemble.py` signal generation
4. Validate: run `python trader/main.py --scan` and check signal output

### Modifying Risk Parameters

1. All risk params are in `config.py` — search for the parameter name
2. Key risk params and their safe ranges:
   - `MAX_POSITION_PCT`: 0.05-0.25 (5-25% of portfolio per position)
   - `STOP_LOSS_PCT`: 0.02-0.10 (2-10%)
   - `TAKE_PROFIT_PCT`: 0.03-0.15 (3-15%)
   - `MAX_OPEN_POSITIONS`: 5-20
   - `RISK_PER_TRADE_PCT`: 0.01-0.10 (Kelly fraction)
3. After changing, validate with `python trader/main.py --scan` to check position sizing

### Working on Solana DEX Trading

Key files (read in this order):
1. `config.py` — DEX-specific settings (search "DEX" or "SOLANA")
2. `solana_trader.py` — Main DEX trading engine
3. `solana_wallet.py` — Wallet operations, Jupiter swaps, Jito MEV
4. `dex_screener.py` — Token discovery and screening
5. `token_safety.py` — RugCheck, honeypot detection
6. `birdeye.py` — Real-time Solana token data

DEX position lifecycle:
```
DexScreener finds token -> token_safety checks -> solana_trader opens position
-> price_monitor watches -> partial_profit at tiers -> close at stop/target/timeout
```

### Working on Polymarket

The `trader/polymarket/` module is self-contained. Key files:
1. `engine.py` — Core trading engine, market scanning
2. `trader.py` — BaseTrader subclass, integrates with orchestrator
3. `probability_engine.py` — LLM probability estimation (Claude API)
4. `strategies.py` — Entry/exit logic
5. `position_manager.py` — P&L tracking

Polymarket trade lifecycle:
```
scan_markets -> probability_engine estimates fair value -> compare to market price
-> strategies decide entry -> api_client places order -> position_manager tracks
```

### Enhancing the Dashboard

`trader/dashboard.py` is a Streamlit app reading JSON state files:
- `bot_state.json` — Main state (equity, positions, health)
- `trades.json` — Trade history
- `dex_positions.json` — Active DEX positions
- `equity_curve.json` — Historical equity

To add a new dashboard section:
1. Add data source in the appropriate trader's `get_state()` method
2. Read the new field in `dashboard.py`
3. Test: `streamlit run trader/dashboard.py`

### Running Tests

```bash
python -m pytest trader/ -v                    # All tests
python trader/backtest.py                      # Backtesting
python trader/backtest.py --plot               # With charts
python trader/main.py --scan                   # Live signal check
python trader/main.py --status                 # Portfolio health
```

## Common Patterns

### Atomic JSON Writes
Always use atomic writes for state files to prevent corruption:
```python
import json, os, tempfile
def atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
```

### Config-Driven Parameters
Never hardcode trading parameters. Always add to `config.py`:
```python
YOUR_PARAM = float(os.getenv("YOUR_PARAM", "0.05"))
```

### Thread-Safe State Access
Use `core/state_manager.py` for any shared state:
```python
from trader.core.state_manager import StateManager
state = StateManager("your_state.json")
with state.lock:
    state.data["key"] = value
    state.save()
```

## Troubleshooting

### Position not closing
- Check `solana_wallet.py` Jupiter swap execution logs
- Verify token still has liquidity on DEX Screener
- Check if max hold time exceeded (config: `DEX_MAX_HOLD_HOURS`)

### Dashboard not updating
- Verify bot is writing `bot_state.json`: `ls -la trader/bot_state.json`
- Check orchestrator is running: `pgrep -f orchestrator`
- Look for JSON write errors in `trader.log`

### Polymarket probability seems off
- Check `probability_engine.py` LLM prompt — may need tuning
- Verify news sentiment sources in `news_sentiment.py`
- Check smart money signals in `smart_money.py`
- API key issues: verify `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `.env`

### Wallet balance mismatch
- Run `python trader/main.py --status` to see current portfolio state
- Check `dex_positions.json` for stale positions
- Verify on-chain balance matches tracked balance in logs
