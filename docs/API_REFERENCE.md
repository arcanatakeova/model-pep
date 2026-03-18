# API_REFERENCE.md — Complete API Integration Guide

## LLM Routing — OpenRouter (REQUIRED for all LLM calls)
```python
# POST https://openrouter.ai/api/v1/chat/completions
headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
# Model routing:
# Routine: "anthropic/claude-haiku-4-5-20251001" (~$0.001/call)
# Decisions: "anthropic/claude-sonnet-4-6" (~$0.015/call)  
# Strategy: "anthropic/claude-opus-4-6" (~$0.075/call)
# Fallback: "openai/gpt-4o" or "google/gemini-2.0-flash" for ensemble voting
# Always inject SOUL.md content as system message
```
Cost: ~$50-150/month depending on volume. Provides unified API across 200+ models.

## Market Data APIs

| API | Base URL | Auth | Cost | Rate Limit |
|-----|----------|------|------|------------|
| DexScreener | api.dexscreener.com | None | Free | 60/min |
| Birdeye | public-api.birdeye.so | X-API-KEY | Free/$99/mo | 100-1000/day |
| Rugcheck | api.rugcheck.xyz | None | $0.02/call | Reasonable |
| Unusual Whales | MCP server | API key | $250/mo | Per plan |
| Finnhub | finnhub.io/api/v1 | Token param | Free | 60/min |

## Trading APIs

| API | Base URL | Auth | Cost | Rate Limit |
|-----|----------|------|------|------------|
| Jupiter V6 | lite-api.jup.ag | None | Free | Per RPC |
| Polymarket CLOB | clob.polymarket.com | HMAC-SHA256 | Free + fees | 9000/10s |
| Coinbase Adv | api.coinbase.com | HMAC | Maker/taker | Per plan |
| Helius RPC | mainnet.helius-rpc.com | API key | Free/$49/mo | 25+ RPS |

## Content & Communication APIs

| API | Base URL | Auth | Cost | Rate Limit |
|-----|----------|------|------|------------|
| X API v2 | api.twitter.com/2 | OAuth 1.0a/2.0 | $200/mo Basic | 15K read, 50K write/mo |
| HeyGen | api.heygen.com | X-Api-Key | $5 PAYG | Per credits |
| MakeUGC | app.makeugc.ai/api | X-Api-Key | Enterprise | Per plan |
| ElevenLabs | api.elevenlabs.io | xi-api-key | $5-99/mo | Per chars |

## Infrastructure APIs

| API | Base URL | Auth | Cost | Rate Limit |
|-----|----------|------|------|------------|
| Supabase | {project}.supabase.co | API key | Free/$25/mo | Generous |
| Stripe | api.stripe.com | Secret key | 2.9% + $0.30 | Unlimited |
| Discord Webhooks | discord.com/api/webhooks | URL token | Free | 30/min |
| Telegram Bot | api.telegram.org/bot{token} | Token | Free | 30 msg/sec |
| DeepL | api-free.deepl.com | Auth key | Free/$5.49/mo | 500K chars free |
| Keepa | api.keepa.com | API key | €19/mo | Per tokens |
| Gumroad | api.gumroad.com | Access token | 10% + processing | Unlimited |

## MCP Servers (Model Context Protocol)
```bash
# Unusual Whales — 33 tools for options, dark pool, congress trades
npx -y @unusualwhales/mcp

# Solana Agent Kit — 60+ blockchain actions
npx -y solana-mcp

# Available tools include:
# GET_ASSET, DEPLOY_TOKEN, GET_PRICE, WALLET_ADDRESS, BALANCE,
# TRANSFER, MINT_NFT, TRADE, REQUEST_FUNDS, RESOLVE_DOMAIN, GET_TPS
```

## Embedding API (for memory system)
```python
# OpenAI text-embedding-ada-002 via OpenRouter
# Cost: $0.0001 per 1K tokens
# Output: 1536-dimensional vector
# Used for: Supabase pgvector similarity search
```
