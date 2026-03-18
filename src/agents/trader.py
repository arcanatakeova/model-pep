"""ARCANA AI — Trader Agent
Read docs/TRADING.md before implementing.

Responsibilities:
- Receive scored opportunities from Scanner
- Apply risk management gate (ALL checks must pass — see TRADING.md)
- Execute trades via: Jupiter (Solana), Polymarket CLOB, Coinbase
- Log every trade to Supabase `trades` table with full signal stack
- Trigger Communicator to post Trade Receipt to X
- Monitor open positions, execute stop-losses
- Update portfolio table after every trade

Risk Gate (MANDATORY — never skip):
- Position size <= 5% of portfolio
- Rugcheck score < 50 for Solana tokens
- No enabled mint/freeze authority
- Top 10 holders < 50% supply
- Hard stop-loss at -15%
- Daily drawdown circuit breaker at -10%
- Max 3 simultaneous Solana positions

DRY_RUN mode:
- When env DRY_RUN=true, log everything but don't execute actual trades
- Still post simulated trade receipts to X (marked as paper trades)
- Use this for first 2 weeks to validate signal quality
"""
# TODO: Implement trader agent
