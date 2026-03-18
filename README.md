# ARCANA AI — Autonomous Economic Entity

> One directive. Make money. Every day. However you can.

## Quick Start
1. Clone this repo
2. Copy `config/.env.example` to `config/.env` and fill in your API keys
3. Run Supabase migrations: paste `supabase/migrations/001_initial_schema.sql` into your Supabase SQL editor
4. Open Claude Code in this directory
5. Tell Claude Code: "Read CLAUDE.md and SOUL.md. Follow the build order. Start with Phase 1."

## Architecture
- LangGraph orchestrator running every 15 minutes
- 5 sub-agents: Scanner, Trader, Creator, Communicator, Automator
- Supabase PostgreSQL + pgvector for memory
- OpenRouter for multi-model LLM routing
- Docker Compose for deployment on NAS

## Docs
- `CLAUDE.md` — Build instructions for Claude Code (read automatically)
- `SOUL.md` — Agent personality configuration + 10 example tweets

### Core Reference
- `docs/ARCHITECTURE.md` — Technical architecture deep dive
- `docs/TRADING.md` — Trading engine, APIs, risk management
- `docs/SOCIAL_MEDIA.md` — X algorithm, content strategy, 5 templates
- `docs/UGC.md` — Video production pipeline (HeyGen + MakeUGC)
- `docs/REVENUE_CHANNELS.md` — 50 revenue channels with deployment order
- `docs/COMPETITIVE_INTEL.md` — Felix, AIXBT, Polystrat, Truth Terminal analysis
- `docs/API_REFERENCE.md` — All 23+ API integrations with auth + rate limits

### Strategy & Planning
- `docs/FINANCIAL_PROJECTIONS.md` — 12-month revenue model
- `docs/RISK_MANAGEMENT.md` — Trading limits, kill switch, compliance
- `docs/SUPABASE_SCHEMA.md` — Full database schema documentation
- `docs/playbooks/LAUNCH_120_DAY.md` — Day-by-day launch plan

### Deep Research
- `docs/research/DEEP_RESEARCH_COMPENDIUM.md` — 20-topic research with hard numbers
- `docs/research/X_ALGORITHM_DEEP_DIVE.md` — X engagement multipliers and strategies
- `docs/research/FRAMEWORK_COMPARISON.md` — ElizaOS vs LangGraph vs CrewAI vs n8n

### Code Examples (Reference Patterns for Claude Code)
- `docs/examples/llm_client_example.py` — OpenRouter client with SOUL.md injection
- `docs/examples/memory_system_example.py` — pgvector memory embed/store/recall
- `docs/examples/trading_patterns_example.py` — Jupiter swap + Polymarket + risk gate
- `docs/examples/x_posting_example.py` — Tweet, thread, self-reply, templates
- `docs/examples/orchestrator_example.py` — LangGraph decision loop pattern

## Kill Switch
```bash
./scripts/kill.sh  # Halts all activity within 60 seconds
rm STOP             # Resume operations
```
