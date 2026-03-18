# Framework Comparison — ElizaOS vs LangGraph vs CrewAI vs n8n

## Decision Matrix

| Feature | ElizaOS | LangGraph | CrewAI | n8n |
|---------|---------|-----------|--------|-----|
| **Language** | TypeScript | Python | Python | Visual/JS |
| **Blockchain native** | ✅ Solana, EVM plugins | ❌ Manual | ❌ Manual | ❌ Manual |
| **Social media native** | ✅ X, Discord, Telegram | ❌ Manual | ❌ Manual | ✅ 500+ integrations |
| **State management** | ⚠️ Limited | ✅ PostgresSaver | ⚠️ Basic | ❌ Stateless |
| **Workflow scheduling** | ❌ No native | ✅ Full control | ⚠️ Basic | ✅ Cron, webhooks |
| **Multi-agent** | ⚠️ Plugin-based | ✅ Graph patterns | ✅ Crew model | ✅ Sub-workflows |
| **Memory/RAG** | ✅ Built-in | ✅ Via checkpointing | ✅ Built-in | ⚠️ External DB needed |
| **Error recovery** | ⚠️ Basic | ✅ Checkpoints, retries | ⚠️ Basic | ✅ Retry logic |
| **Learning curve** | Medium | High | Medium | Low |
| **Community/Stars** | 17.5K | 8K+ | 21K+ | 55K+ |
| **Best for** | Crypto social agents | Complex stateful workflows | Task-oriented teams | Integration glue |

## ARCANA AI Recommendation: Hybrid Architecture

### Primary Stack: Python + LangGraph + n8n
**Why not ElizaOS?** Despite being the closest to what ARCANA needs (crypto + social), ElizaOS is TypeScript-only, has known memory injection vulnerabilities, and lacks workflow scheduling. The Python ecosystem has better trading libraries (py-clob-client, solders, solana-py) and LLM tooling (langchain, openai).

### Architecture:
```
n8n (Triggers & Integration Layer)
  ├── Schedule Trigger → Every 15 min → HTTP Request → Orchestrator
  ├── Webhook → Incoming X mentions → Orchestrator
  ├── Webhook → Stripe payments → Orchestrator
  └── Webhook → Supabase realtime → Orchestrator

LangGraph (Reasoning & Orchestration)
  ├── Orchestrator Graph (SCAN → EVALUATE → PRIORITIZE → EXECUTE → LEARN)
  ├── PostgresSaver for state checkpointing
  ├── Human-in-the-loop gates (for >$500 trades)
  └── Memory via Supabase pgvector

Direct Python (Trading & Content)
  ├── Jupiter swap via httpx + solders
  ├── Polymarket via py-clob-client
  ├── X API via tweepy
  ├── HeyGen via httpx
  └── All other API integrations
```

### Why This Works:
1. **n8n** handles the boring but critical stuff — cron scheduling, webhook ingestion, notification routing — things LangGraph is overkill for
2. **LangGraph** handles the hard stuff — multi-step reasoning, state management, checkpointing, complex decision trees
3. **Direct Python** handles the fast stuff — API calls that don't need LLM reasoning, trading execution, content posting

### What to Steal from ElizaOS:
- Character system design (SOUL.md is inspired by ElizaOS character files)
- Plugin architecture pattern (each agent is a "plugin" with standard interface)
- The concept of "providers" that inject real-time data into LLM context
