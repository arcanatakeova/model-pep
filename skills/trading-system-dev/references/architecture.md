# Architecture Deep Dive

## Module Dependency Graph

```
main.py
  └── orchestrator.py
        ├── solana_trader.py (BaseTrader)
        │     ├── solana_wallet.py (Jupiter swaps, Jito MEV)
        │     ├── dex_screener.py (token discovery)
        │     ├── token_safety.py (rug/honeypot checks)
        │     ├── birdeye.py (real-time prices)
        │     └── strategies/ensemble.py (signal generation)
        │
        ├── polymarket/trader.py (BaseTrader)
        │     ├── polymarket/engine.py
        │     ├── polymarket/probability_engine.py (LLM)
        │     ├── polymarket/news_sentiment.py
        │     ├── polymarket/smart_money.py
        │     ├── polymarket/api_client.py (CLOB L2)
        │     └── polymarket/strategies.py
        │
        └── (future traders inherit BaseTrader)

Shared by all traders:
  ├── config.py (parameters)
  ├── core/state_manager.py (persistence)
  ├── core/logging_setup.py
  ├── portfolio.py (P&L tracking)
  ├── risk_manager.py (position sizing, limits)
  ├── compounding_engine.py (profit reinvestment)
  └── secrets_manager.py (Supabase vault)
```

## Data Flow

```
Market Data Sources                    Trading Engines              Output
─────────────────                     ───────────────              ──────
DEX Screener API ──┐
Birdeye API ───────┤── solana_trader ──┐
Jupiter Price ─────┘                   │
                                       ├── orchestrator ── bot_state.json ── dashboard.py
CoinGecko API ─────┐                  │
CryptoCompare ─────┤── (CEX trader) ──┘
yfinance ──────────┘

Polymarket CLOB ───┐
RSS News Feeds ────┤── polymarket/trader ──┘
Claude/OpenAI API ─┘
```

## State File Contracts

### bot_state.json (orchestrator output)
```json
{
  "cycle": 1234,
  "timestamp": 1710000000.0,
  "traders": {
    "solana": {"alive": true, "last_cycle": 1710000000.0, "positions": 3},
    "polymarket": {"alive": true, "last_cycle": 1709999990.0, "positions": 1}
  },
  "portfolio": {"cash": 95000, "equity": 102500, "open_positions": 4},
  "recent_trades": [...]
}
```

### dex_positions.json (solana_trader output)
```json
{
  "TOKEN_PAIR_ADDRESS": {
    "symbol": "BONK/SOL",
    "entry_price": 0.00001234,
    "amount": 1000000,
    "entry_time": 1710000000.0,
    "stop_loss": 0.00001172,
    "take_profit": 0.00001308,
    "partial_profits_taken": []
  }
}
```

### trades.json (portfolio output)
```json
[
  {
    "symbol": "BONK/SOL",
    "action": "BUY",
    "price": 0.00001234,
    "amount": 1000000,
    "timestamp": 1710000000.0,
    "pnl": null,
    "reason": "ensemble_signal_0.85"
  }
]
```

## BaseTrader Interface

```python
class BaseTrader(ABC):
    @abstractmethod
    def run_cycle(self) -> None:
        """Execute one trading cycle (scan + trade + manage positions)."""

    @abstractmethod
    def get_state(self) -> dict:
        """Return current state for orchestrator aggregation."""

    @abstractmethod
    def health_check(self) -> dict:
        """Return {"alive": bool, "last_heartbeat": float}."""

    def start(self) -> None:
        """Start the trading loop (blocking)."""

    def stop(self) -> None:
        """Signal graceful shutdown."""
```

## Config Parameter Categories

| Category | Prefix/Section | Example |
|----------|---------------|---------|
| Risk | `MAX_*`, `STOP_*`, `RISK_*` | `MAX_POSITION_PCT = 0.18` |
| DEX | `DEX_*`, `SOLANA_*` | `DEX_BASE_POSITION_SIZE = 100` |
| Polymarket | `POLYMARKET_*` | `POLYMARKET_MAX_POSITION = 200` |
| CEX | `FUTURES_*`, `SCALP_*` | `FUTURES_ENABLED = False` |
| Timing | `*_INTERVAL*`, `*_DELAY*` | `SCAN_INTERVAL_SEC = 5` |
| Indicators | `INDICATOR_WEIGHTS` | `{"RSI": 0.12, "MACD": 0.18}` |
