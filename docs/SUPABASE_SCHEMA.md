# Supabase Schema Documentation

## Database: arcana (PostgreSQL 15 + pgvector)

### Tables Overview
| Table | Purpose | Primary Agent |
|-------|---------|--------------|
| trades | All trade records with signal stacks | Trader |
| signals | Raw market signals from all data sources | Scanner |
| portfolio | Current portfolio state and allocations | Trader |
| agent_memory | pgvector embeddings for agent recall | All agents |
| content_posts | X tweets, threads, engagement metrics | Communicator |
| leads | Consulting CRM pipeline | Communicator |
| ugc_orders | UGC video production orders | Creator |
| products | Digital products catalog | Creator/Automator |
| affiliate_clicks | Affiliate link tracking | Automator |
| strategy_metrics | Aggregated strategy performance | Orchestrator |
| agent_log | Every agent action logged | All agents |
| daily_revenue | Daily P&L aggregation across all channels | Orchestrator |

### Table Details

#### trades
```sql
id UUID PK
agent TEXT NOT NULL DEFAULT 'trader'
market TEXT NOT NULL                    -- "SOL/USDC", "polymarket:election", etc.
direction TEXT CHECK (long, short)
entry_price NUMERIC
exit_price NUMERIC
size_usd NUMERIC
pnl_usd NUMERIC
pnl_pct NUMERIC
signal_stack JSONB                     -- {"dexscreener": "...", "birdeye": "...", "rugcheck": 42}
strategy TEXT                          -- "solana_swing", "polymarket_arb", etc.
status TEXT CHECK (open, closed, stopped)
notes TEXT
created_at TIMESTAMPTZ
closed_at TIMESTAMPTZ
```

#### signals
```sql
id UUID PK
source TEXT NOT NULL                   -- "dexscreener", "birdeye", "unusual_whales", "finnhub", "rugcheck"
signal_type TEXT                       -- "volume_spike", "whale_movement", "options_flow", etc.
asset TEXT                             -- Token mint address or market identifier
data JSONB NOT NULL                    -- Full raw signal data
confidence NUMERIC                     -- 0-100 confidence score
acted_on BOOLEAN DEFAULT false         -- Did we trade on this signal?
trade_id UUID FK → trades(id)          -- If acted on, link to trade
created_at TIMESTAMPTZ
```

#### agent_memory
```sql
id UUID PK
content TEXT NOT NULL                  -- Natural language description of memory
embedding VECTOR(1536)                 -- OpenAI ada-002 embedding
category TEXT CHECK (trade_outcome, market_pattern, content_performance, lead_interaction, strategy_adjustment)
importance_score NUMERIC DEFAULT 0.5   -- 0-1, higher = more surprising/important
metadata JSONB                         -- Arbitrary context data
created_at TIMESTAMPTZ
```
Index: ivfflat on embedding vector_cosine_ops (lists=100)

#### leads
```sql
id UUID PK
source TEXT DEFAULT 'x'                -- "x", "discord", "email", "website"
handle TEXT                            -- @username
name TEXT
industry TEXT
stated_need TEXT                       -- What they're looking for
qualification_score NUMERIC            -- 0-100 based on LLM assessment
status TEXT CHECK (new, qualified, contacted, converted, dead)
routed_to TEXT                         -- "ian", "tan", or null
notes TEXT
created_at TIMESTAMPTZ
```

#### daily_revenue
```sql
id UUID PK
date DATE NOT NULL UNIQUE
trading_pnl NUMERIC DEFAULT 0
content_revenue NUMERIC DEFAULT 0
ugc_revenue NUMERIC DEFAULT 0
affiliate_revenue NUMERIC DEFAULT 0
product_revenue NUMERIC DEFAULT 0
consulting_revenue NUMERIC DEFAULT 0
other_revenue NUMERIC DEFAULT 0
total_revenue NUMERIC DEFAULT 0
total_costs NUMERIC DEFAULT 0
net_revenue NUMERIC DEFAULT 0
created_at TIMESTAMPTZ
```

### Key Functions

#### match_memories(query_embedding, threshold, count, category)
Cosine similarity search on agent_memory. Used by memory.recall().
Returns: id, content, category, importance_score, metadata, similarity.

### Indexes
- signals: (created_at DESC), (source, created_at DESC)
- trades: (status, created_at DESC), (strategy, created_at DESC)
- leads: (status, created_at DESC)
- agent_log: (agent, created_at DESC)
- content_posts: (content_type, posted_at DESC)
- agent_memory: ivfflat on embedding

### Initial Data
Portfolio seeded with $1,000 and strategy allocations:
```json
{
  "polymarket_arb": 300,
  "solana_swing": 250,
  "polymarket_conviction": 200,
  "near_resolution": 150,
  "cash_reserve": 100
}
```
