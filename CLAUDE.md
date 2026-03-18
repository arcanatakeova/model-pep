# ARCANA AI — Claude Code Build Instructions

## WHAT THIS IS
ARCANA AI is a fully autonomous AI economic entity that makes money 24/7 through ANY legal means. It runs on Docker on a NAS, trades crypto, posts content to X, produces UGC videos, generates consulting leads, and operates 50+ revenue channels. All profits flow to Arcana Operations (Ian & Tan's AI consulting business in Portland, OR).

**One directive: Make money. Every day. However you can.**

## HUMAN PREREQUISITES (Ian must do these BEFORE Claude Code builds)
1. Create @ArcanaAI_ X account — subscribe to X Premium ($8/mo)
2. Apply for X API Basic tier ($200/mo) — get API keys
3. Create Supabase project — run `supabase/migrations/001_initial_schema.sql`
4. Create OpenRouter account — fund with $20 to start
5. Create burner Solana wallet (Phantom) — fund with $500-1000 trading capital ONLY
6. Create Polygon wallet for Polymarket — fund with $300 USDC.e
7. Get Helius RPC API key (free tier)
8. Get Birdeye API key (free tier)
9. Get Finnhub API key (free tier)
10. Get Unusual Whales API key ($250/mo) — optional, can add later
11. Set up Discord webhook for notifications
12. Fill in ALL keys in `config/.env`

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
7. Content templates: Morning Briefing, Trade Receipt, Weekly Postmortem, Case File, Live Trade
8. Schedule: Morning Briefing at 7 AM PT daily, 3-5 tweets throughout the day
9. **TEST**: Post 3 test tweets manually. Verify formatting, personality matches SOUL.md.

### Phase 3: Get Trading (Proof of Capability)
10. `src/agents/scanner.py` — Aggregate signals from DexScreener, Birdeye, Unusual Whales, Finnhub, Rugcheck
11. `src/agents/trader.py` — Execute trades on Jupiter (Solana), Polymarket, Coinbase
12. Risk management gate (ALL trades must pass — see docs/TRADING.md)
13. **DRY_RUN FIRST**: Set DRY_RUN=true in .env. Run for 1-2 weeks. Review paper trade logs before going live.
14. Trade receipts auto-posted to X after every trade (marked as paper trades during DRY_RUN)

### Phase 4: Get Paid (Multiple Revenue Streams)
15. `src/agents/creator.py` — UGC video production via MakeUGC/HeyGen API
16. `src/agents/automator.py` — Builds micro-SaaS tools, manages digital products, handles Stripe payments
17. Lead qualification pipeline: X DMs → Supabase CRM → Discord notification to Ian/Tan

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
- Every function that calls an external API must have try/except with retry logic (3 retries, exponential backoff)
- Log every action to Supabase agent_log table
- Use OpenRouter for ALL LLM calls (never direct API calls to Anthropic/OpenAI)
- Load SOUL.md into every LLM system prompt
- For embeddings: use OpenAI text-embedding-ada-002 directly ($0.0001/1K tokens) — OpenRouter does not proxy embeddings

## OPENROUTER MODEL IDS (Use these exact strings)
- Routine/cheap: `anthropic/claude-3.5-haiku-20241022` (~$0.001/call)
- Decisions/content: `anthropic/claude-sonnet-4-20250514` (~$0.015/call)
- Strategy/complex: `anthropic/claude-opus-4-20250514` (~$0.075/call)
- Fallback/cheap: `openai/gpt-4o-mini` (~$0.0003/call)
- Ensemble member: `google/gemini-2.0-flash-001` (~$0.001/call)

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
- **github.com/arcanatakeova/model-pep** — Ian's existing repo. Push this kit there.
- github.com/elizaOS/eliza — 17.5K stars, TypeScript agent framework, best for crypto+social
- github.com/sendaifun/solana-agent-kit — 60+ Solana actions via natural language
- github.com/sendaifun/solana-mcp — MCP server for Solana Agent Kit
- github.com/georgezouq/awesome-ai-in-finance — Curated AI finance tools (4.9K stars)
- github.com/virattt/ai-hedge-fund — LLM trading decisions
- github.com/AI4Finance-Foundation/FinRL — Deep RL for trading
- github.com/TradingAgents — Multi-agent financial trading framework

## FULL CONTEXT
The complete 100-page specification PDF (ARCANA_AI_Definitive_Blueprint.pdf) contains deep research on:
competitive agent teardowns, X algorithm weights, UGC market economics, 50+ revenue channel analysis,
Polymarket CLOB API docs, MakeUGC/HeyGen API details, ElizaOS framework analysis, and more.
If you need deeper context on ANY topic, ask Ian to share the relevant section from the PDF.
