# ARCHITECTURE.md — ARCANA AI Technical Architecture

## System Overview
Docker Compose on NAS (4+ CPU, 8GB+ RAM). LangGraph orchestrator runs every 15 minutes.
Decision loop: SCAN → EVALUATE → PRIORITIZE → EXECUTE → LEARN

## Components
- **Orchestrator**: LangGraph-based. Queries all sub-agents for available actions, scores them with priority function, executes highest-priority action.
- **Scanner**: Aggregates market signals from DexScreener, Birdeye, Unusual Whales, Finnhub, Rugcheck. Stores in Supabase `signals` table.
- **Trader**: Executes trades on Jupiter (Solana swaps), Polymarket (prediction markets), Coinbase (CEX). All trades logged to `trades` table.
- **Creator**: Generates content for X, produces UGC videos via HeyGen/MakeUGC, writes newsletter content, builds digital products.
- **Communicator**: Posts to X API, monitors mentions/DMs, qualifies leads, engages with relevant conversations.
- **Automator**: Builds micro-SaaS tools, manages Stripe payments, handles affiliate tracking, runs programmatic SEO.

## LLM Routing via OpenRouter
- **Haiku** ($0.001/call): Routine monitoring, signal aggregation, simple responses
- **Sonnet** ($0.015/call): Trading decisions, content generation, lead qualification
- **Opus** ($0.075/call): Strategy reviews, complex analysis, weekly postmortems
- Always inject SOUL.md as system prompt

## Memory System (pgvector)
Every trade outcome, content metric, market observation embedded as 1536-dim vector.
Before every major decision, query: "What happened last time I saw a signal like this?"
Categories: trade_outcome, market_pattern, content_performance, lead_interaction, strategy_adjustment
Importance scoring: 0-1, outcomes that deviate from prediction score highest (learning from surprises).

## State Management Pattern (Critical for 24/7 operation)
Use PostgreSQL checkpointing via LangGraph's PostgresSaver. Every super-step checkpointed.
80% of production AI agent failures are state management issues, not prompt quality.
Circuit breakers between agent boundaries. Stuck-task detection with max_iterations limits.
Semantic caching reduces redundant LLM calls by up to 73%.

## Docker Compose Structure
See `docker-compose.yml` in project root. Three services:
- `arcana`: Main agent (builds from Dockerfile, mounts SOUL.md + logs, reads config/.env)
- `db`: Supabase Postgres 15 with pgvector (migrations auto-run from /docker-entrypoint-initdb.d)
- `n8n`: Self-hosted workflow automation on port 5678

## n8n Integration
n8n handles: triggers, scheduling, webhook ingestion, cross-system integration, notifications.
LangGraph handles: complex agent reasoning, multi-step planning, state management.
Connect via HTTP Request nodes or MCP protocol.
Key n8n nodes: Schedule Trigger, Webhook, AI Agent, HTTP Request, Code, IF/Switch.
Self-host n8n alongside the agent stack. $0/month additional cost.

## Error Recovery
- All API calls wrapped in try/except with exponential backoff (3 retries)
- Failed trades logged with full error context for postmortem
- Agent health check every 5 minutes via heartbeat to Supabase
- Discord/Telegram alert on any error or anomaly
- Daily automated summary of all actions, revenue, costs sent to Ian/Tan
