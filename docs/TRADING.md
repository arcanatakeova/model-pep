# TRADING.md — ARCANA AI Trading Engine

## Five-Layer Signal Pipeline
1. **Data Collection**: DexScreener (trending tokens), Birdeye (whale wallets), Unusual Whales (options flow, dark pool, congress trades), Finnhub (news sentiment), Rugcheck (token safety)
2. **Signal Detection**: Haiku monitors feeds ($0.001/call). Anomaly → escalate to Sonnet ($0.015/call)
3. **Multi-Model Ensemble**: For high-conviction signals, query 3+ models via OpenRouter. Require 2/3 agreement.
4. **Risk Gate**: Every trade must pass ALL checks (see below)
5. **Execution + Documentation**: Trade executed → Receipt posted to X → Signal stack logged to memory

## Risk Management Gate (ALL trades MUST pass)
- Position size ≤ 5% of total portfolio
- Solana tokens must score < 50 on Rugcheck (auto-reject above)
- No token with enabled mint authority or freeze authority
- No token where top 10 holders control > 50% of supply
- Hard stop-loss at -15% per position
- Daily drawdown limit: -10% of portfolio (circuit breaker — pause ALL trading 24h)
- Max 3 open Solana token positions simultaneously

## Portfolio Allocation ($1,000 starting capital)
- Polymarket Standard Arbitrage: $300 (30%) — 5-15% monthly, only markets where YES prices sum < $1.00
- Solana Token Swing Trades: $250 (25%) — 10-30% monthly, high variance
- Polymarket Conviction Bets: $200 (20%) — Variable, multi-model ensemble, 8%+ edge, Kelly sizing
- Near-Resolution Harvesting: $150 (15%) — 3-8% monthly, markets resolving within 48h at 90%+ prob
- Cash Reserve / Gas: $100 (10%) — Never deployed, emergency buffer

## Solana Trading via Jupiter
```python
# Jupiter V6 swap flow
# 1. Get quote
quote = httpx.get("https://lite-api.jup.ag/swap/v1/quote", params={
    "inputMint": "So11111111111111111111111111111111111111112",  # SOL
    "outputMint": token_mint,
    "amount": amount_in_lamports,
    "slippageBps": 50  # 0.5%
})
# 2. Get swap transaction
swap = httpx.post("https://lite-api.jup.ag/swap/v1/swap", json={
    "quoteResponse": quote.json(),
    "userPublicKey": wallet_pubkey,
    "prioritizationFeeLamports": "auto"
})
# 3. Sign and submit via Helius RPC
```
Ultra API alternative handles slippage and priority fees automatically.
Use Jito bundles for MEV protection on sensitive trades.

## Polymarket Market Discovery via Gamma API
```python
# Gamma API — for finding markets and metadata (NOT for trading)
# GET https://gamma-api.polymarket.com/markets — list all active markets
# GET https://gamma-api.polymarket.com/markets/{condition_id} — single market details
# Returns: question, description, outcomes, liquidity, volume, end_date_iso
# No auth needed. Use this to find markets, then trade via CLOB API.
```

## Polymarket Trading via CLOB API
```python
# Authentication: L1 (wallet EIP-712) → L2 (HMAC-SHA256)
# Install: pip install py-clob-client
from py_clob_client.client import ClobClient
client = ClobClient("https://clob.polymarket.com", key=private_key, chain_id=137)

# Get markets
markets = client.get_markets()

# Place order
order = client.create_and_post_order(OrderArgs(
    token_id=condition_token_id,
    price=0.55,  # Buy YES at $0.55
    size=100,     # $100 position
    side="BUY"
))
```
Rate limits: 9,000 req/10s general, 3,500 req/10s orders, 1,500 req/10s orderbook.
Fees: 0% on most markets. Fee-enabled markets: ~1.56% taker at 50% prob.
WebSocket: wss://ws-subscriptions-clob.polymarket.com for real-time data.
NegRisk: Multi-outcome events where exactly one resolves YES. NegRisk does NOT create arbitrage — it provides capital efficiency (always costs exactly $1.00 per complete set). True arbitrage exists only when the sum of all YES prices is LESS THAN 1.0 in STANDARD (non-NegRisk) markets — scan for these.
CRITICAL: Polymarket is restricted for US users for most markets. Review current regulatory status before deploying. Consider using Polymarket's separate CFTC-regulated election markets or non-US infrastructure.

## DexScreener API (Free, no key needed)
```
GET https://api.dexscreener.com/token-boosts/latest/v1  # Trending tokens
GET https://api.dexscreener.com/token-pairs/v1/solana/{address}  # Pair data
```
Rate limit: 60 req/min. No auth needed.

## Birdeye API
```
GET https://public-api.birdeye.so/defi/token_overview?address={mint}
GET https://public-api.birdeye.so/wallet/token_list?wallet={address}
```
Header: X-API-KEY. Free tier: 100 req/day. Paid: $99/mo for 1000/day.

## Unusual Whales MCP
Install: `npx -y @unusualwhales/mcp`
33 tools including: get_option_flow, get_darkpool_transactions, get_congress_trades, get_etf_info, get_market_overview
Cost: $250/month for full API access.

## Rugcheck API
```
GET https://api.rugcheck.xyz/v1/tokens/{mint}/report
```
Returns: risk score 0-100, mint authority status, freeze authority, LP locks, holder concentration.
Cost: $0.02/call. No auth for public endpoint.

## Finnhub API
```
GET https://finnhub.io/api/v1/news?category=crypto&token={key}
GET https://finnhub.io/api/v1/stock/metric?symbol=COIN&metric=all&token={key}
```
Free tier: 60 calls/minute.

## Coinbase Advanced Trade API
For larger-cap positions (BTC, ETH, SOL on CEX).
REST API with HMAC authentication. Maker/taker fee schedule.

## Reference Trading Repos
- github.com/virattt/ai-hedge-fund — LLM-driven trading decisions
- github.com/AI4Finance-Foundation/FinRL — Deep RL for automated trading
- github.com/TensorTrade-org/tensortrade — RL-based trade environments
- github.com/microsoft/MarS — Financial market simulation engine
