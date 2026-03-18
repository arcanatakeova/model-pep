# ARCANA AI — Claude Code Build Instructions

## WHAT THIS IS
ARCANA AI is a fully autonomous AI economic entity that makes money 24/7 through ANY legal means. It runs on Docker on a NAS, trades crypto, posts content to X, produces UGC videos, generates consulting leads, and operates 50+ revenue channels. All profits flow to Arcana Operations (Ian & Tan's AI consulting business in Portland, OR).

**One directive: Make money. Every day. However you can.**

## READ THESE DOCS BEFORE BUILDING SPECIFIC MODULES
Before working on ANY module, read the relevant doc file first:
- Building the orchestrator → read `docs/ARCHITECTURE.md`
- Building trading agents → read `docs/TRADING.md`
- Building content/X agent → read `docs/SOCIAL_MEDIA.md`
- Building UGC production → read `docs/UGC.md`
- Building revenue channels → read `docs/REVENUE_CHANNELS.md`
- Understanding competitors → read `docs/COMPETITIVE_INTEL.md`
- API integrations → read `docs/API_REFERENCE.md`
- Agent personality → read `SOUL.md`

## BUILD ORDER (Follow this exactly)

### Phase 1: Foundation (Build First)
1. `src/utils/llm.py` — OpenRouter client with model routing + SOUL.md injection
2. `src/utils/memory.py` — Supabase pgvector memory (embed, store, recall)
3. `src/utils/notify.py` — Discord/Telegram alerting
4. `src/orchestrator.py` — LangGraph decision loop (SCAN→EVALUATE→PRIORITIZE→EXECUTE→LEARN)
5. Run Supabase migrations from `supabase/migrations/`

### Phase 2: Get Posting (Revenue Day 1)
6. `src/agents/communicator.py` — X API client: post tweets, threads, reply to mentions, monitor DMs
7. Content templates: Morning Briefing, Trade Receipt, Weekly Postmortem
8. Schedule: Morning Briefing at 7 AM PT daily, 3-5 tweets throughout the day

### Phase 3: Get Trading (Proof of Capability)
9. `src/agents/scanner.py` — Aggregate signals from DexScreener, Birdeye, Unusual Whales, Finnhub, Rugcheck
10. `src/agents/trader.py` — Execute trades on Jupiter (Solana), Polymarket, Coinbase
11. Risk management gate (ALL trades must pass — see docs/TRADING.md)
12. Trade receipts auto-posted to X after every trade

### Phase 4: Get Paid (Multiple Revenue Streams)
13. `src/agents/creator.py` — UGC video production via MakeUGC/HeyGen API
14. `src/agents/automator.py` — Builds micro-SaaS tools, manages digital products, handles Stripe payments
15. Lead qualification pipeline: X DMs → Supabase CRM → Discord notification to Ian/Tan

### Phase 5: Scale
16. Programmatic SEO content site
17. AI newsletter via Beehiiv
18. Prompt library / digital products on Gumroad
19. Chrome extensions
20. AI podcast factory via ElevenLabs

## PRIORITY FUNCTION (The Autonomous Brain)
Every 15 minutes, evaluate all available actions using:
```
Priority = (Revenue × Probability) / (Time × Risk)
```
- Revenue: Expected dollar value
- Probability: Likelihood of success (0-1), informed by memory
- Time: Hours required to execute
- Risk: Potential downside (1 = no risk, 10 = high risk)

**Consulting lead DMs ALWAYS score highest** — even at 10% probability, a $5K contract in 15 min response time = priority score of 2,000. Lead gen is ALWAYS top priority.

## CODE STYLE
- Python 3.11+, type hints everywhere
- async/await for all I/O operations
- Pydantic models for all data structures
- Every function that calls an external API must have try/except with retry logic
- Log every action to Supabase agent_log table
- Use OpenRouter for ALL LLM calls (never direct API calls to Anthropic/OpenAI)
- Load SOUL.md into every LLM system prompt

## ENVIRONMENT VARIABLES (see config/.env.example)
Never hardcode API keys. All secrets in .env file. See config/.env.example for the full list.

## KEY CONSTRAINTS
- Trading: Max 5% per position, -15% stop-loss, -10% daily drawdown circuit breaker
- Content: Must match SOUL.md personality, no hype language, no price predictions
- Kill switch: If file named STOP exists in project root, halt ALL activity within 60 seconds
- Wallet: Use ONLY a dedicated burner Solana wallet funded with trading capital only

## ARCANA OPERATIONS CONTEXT
Ian & Tan run three businesses:
- **Arcana Operations** — AI consulting ($2-10K/mo contracts): strategy, SEO, fulfillment, marketing, agents
- **Navigate Peptides** — Research-grade peptide e-commerce (Shopify headless)
- **Autobahn Collective** — Used OEM BMW parts (Shopify + eBay + FB Marketplace)

ARCANA AI's content should reference real industry experience from these businesses when creating Case File threads. The agent IS the pitch for Arcana Operations' consulting services.

## REFERENCE REPOS (Study these for patterns)
- github.com/elizaOS/eliza — 17.5K stars, TypeScript agent framework, best for crypto+social
- github.com/sendaifun/solana-agent-kit — 60+ Solana actions via natural language
- github.com/sendaifun/solana-mcp — MCP server for Solana Agent Kit
- github.com/georgezouq/awesome-ai-in-finance — Curated AI finance tools
- github.com/virattt/ai-hedge-fund — LLM trading decisions
- github.com/TradingAgents — Multi-agent financial trading
- github.com/AI4Finance-Foundation/FinRL — Deep RL for trading
