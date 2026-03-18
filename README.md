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
- `SOUL.md` — Agent personality configuration
- `docs/ARCHITECTURE.md` — Technical architecture deep dive
- `docs/TRADING.md` — Trading engine, APIs, risk management
- `docs/SOCIAL_MEDIA.md` — X algorithm, content strategy, templates
- `docs/UGC.md` — Video production pipeline
- `docs/REVENUE_CHANNELS.md` — 50+ revenue channels with deployment order
- `docs/COMPETITIVE_INTEL.md` — Felix, AIXBT, Polystrat, Truth Terminal analysis
- `docs/API_REFERENCE.md` — All 23+ API integrations

## Kill Switch
```bash
./scripts/kill.sh  # Halts all activity within 60 seconds
rm STOP             # Resume operations
```
