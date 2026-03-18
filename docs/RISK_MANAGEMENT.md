# Risk Management Framework — ARCANA AI

## Trading Risk Controls

### Position-Level Controls
- Max position size: 5% of total portfolio per trade
- Hard stop-loss: -15% per position (auto-execute, no override)
- Trailing stop: Activate at +10% profit, trail by 5%
- Max 3 simultaneous Solana token positions
- No token with Rugcheck score > 50
- No token with enabled mint authority or freeze authority
- No token where top 10 holders control > 50% supply

### Portfolio-Level Controls
- Daily drawdown circuit breaker: -10% of portfolio → pause ALL trading for 24 hours
- Weekly drawdown limit: -20% of portfolio → pause trading, alert Ian/Tan, require manual restart
- Maximum total exposure: 90% of portfolio (always keep 10% cash/gas reserve)
- No single strategy > 30% of portfolio

### Signal Quality Gates
- Multi-model ensemble: Query 3+ models for high-conviction signals, require 2/3 agreement
- Memory check: "Have I seen this pattern before? What happened?" before every trade
- Conviction threshold: Only trade when combined signal score > 70/100
- Time-of-day filter: Reduce position sizes 50% during low-liquidity hours (2-6 AM UTC)

## Operational Risk Controls

### Kill Switch System
```
STOP file in project root → All activity halts within 60 seconds
scripts/kill.sh → Creates STOP file
rm STOP → Resume operations
```

### Spending Controls
- LLM costs: Alert if daily spend > $20 (Haiku $0.001, Sonnet $0.015, Opus $0.075)
- Total daily operational cost cap: $50 (alert at $30, halt non-essential at $50)
- Trading costs: Gas/fees tracked per trade, alert if gas > 5% of trade size

### API Failure Handling
- All API calls: try/except with exponential backoff (3 retries, 1s/2s/4s delays)
- If >3 consecutive failures on same API: alert Ian/Tan, disable that data source temporarily
- If Supabase unreachable: halt ALL operations, alert immediately
- If OpenRouter down: fall back to direct Anthropic API, then OpenAI, then halt

### Content Risk Controls
- Every post must match SOUL.md personality (LLM self-check before posting)
- No specific price predictions ("SOL will hit $200" is banned)
- No financial advice framing (analysis only, always disclaim)
- No token shilling or paid promotion
- No deleted posts (losing trades stay up permanently)
- No engagement with scam accounts or suspicious DMs
- Rate limiting: Max 100 posts/hour, random 1-15 min jitter between posts

## Security Risk Controls

### Wallet Security
- Solana trading wallet: DEDICATED BURNER only, funded with trading capital only
- Polygon/Polymarket wallet: Separate from Solana wallet
- NEVER store private keys in code — .env only, .gitignore enforced
- Weekly audit: Check wallet balances match expected positions

### API Key Security
- All keys in config/.env, never committed to git
- Rotate API keys quarterly
- Monitor for unauthorized usage (check OpenRouter dashboard daily)
- X API: Monitor for rate limit warnings (may indicate compromise)

### Infrastructure Security
- Docker containers: Run as non-root user
- Supabase: Use service_role key only in backend, anon key for reads
- n8n: Password-protect the dashboard
- NAS: Firewall rules, no public exposure except necessary ports

## Compliance Risk

### Financial Disclaimers
- Every trade receipt includes: "Not financial advice. ARCANA AI is an autonomous system. Trade at your own risk."
- No promises of returns or performance guarantees
- Clearly disclose AI-generated nature of all content
- No front-running based on insider information

### Polymarket Compliance
- US access restrictions: Review current CFTC regulatory status before deploying
- Geo-restriction: May need non-US infrastructure for full market access
- Tax implications: All trading P&L is taxable income for Arcana Operations

### Content Compliance
- FTC affiliate disclosure: "Contains affiliate links" on all affiliate content
- SEC: No securities recommendations, no investment advice
- Copyright: All content original or properly licensed
- X ToS: No automated behavior that violates platform rules
