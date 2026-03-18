-- ARCANA AI — Initial Schema Migration
-- Run this in your Supabase SQL editor

-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Trades log
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent TEXT NOT NULL DEFAULT 'trader',
    market TEXT NOT NULL,
    direction TEXT CHECK (direction IN ('long', 'short')),
    entry_price NUMERIC,
    exit_price NUMERIC,
    size_usd NUMERIC,
    pnl_usd NUMERIC,
    pnl_pct NUMERIC,
    signal_stack JSONB,
    strategy TEXT,
    status TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed', 'stopped')),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    closed_at TIMESTAMPTZ
);

-- Portfolio state
CREATE TABLE portfolio (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    total_value NUMERIC NOT NULL,
    cash_available NUMERIC NOT NULL,
    daily_pnl NUMERIC DEFAULT 0,
    all_time_pnl NUMERIC DEFAULT 0,
    strategy_allocations JSONB,
    positions JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Agent memory with vector embeddings
CREATE TABLE agent_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    embedding VECTOR(1536),
    category TEXT CHECK (category IN ('trade_outcome', 'market_pattern', 'content_performance', 'lead_interaction', 'strategy_adjustment')),
    importance_score NUMERIC DEFAULT 0.5,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON agent_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Content posts
CREATE TABLE content_posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform TEXT DEFAULT 'x',
    content_type TEXT,
    body TEXT NOT NULL,
    tweet_id TEXT,
    thread_ids TEXT[],
    engagement JSONB,
    posted_at TIMESTAMPTZ DEFAULT now(),
    status TEXT DEFAULT 'posted'
);

-- Leads CRM
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT DEFAULT 'x',
    handle TEXT,
    name TEXT,
    industry TEXT,
    stated_need TEXT,
    qualification_score NUMERIC,
    status TEXT DEFAULT 'new' CHECK (status IN ('new', 'qualified', 'contacted', 'converted', 'dead')),
    routed_to TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- UGC orders
CREATE TABLE ugc_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_name TEXT,
    product_url TEXT,
    script TEXT,
    avatar_id TEXT,
    video_url TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'generating', 'review', 'delivered', 'revision')),
    price_usd NUMERIC,
    cost_usd NUMERIC,
    stripe_payment_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    delivered_at TIMESTAMPTZ
);

-- Digital products
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT,
    platform TEXT,
    price_usd NUMERIC,
    url TEXT,
    sales_count INTEGER DEFAULT 0,
    revenue_usd NUMERIC DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Affiliate click tracking
CREATE TABLE affiliate_clicks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    program TEXT,
    link_url TEXT,
    referral_code TEXT,
    clicks INTEGER DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    revenue_usd NUMERIC DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Strategy performance metrics
CREATE TABLE strategy_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_name TEXT NOT NULL,
    period TEXT,
    win_rate NUMERIC,
    avg_return NUMERIC,
    sharpe_ratio NUMERIC,
    max_drawdown NUMERIC,
    trades_count INTEGER,
    total_pnl NUMERIC,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Agent activity log
CREATE TABLE agent_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    details JSONB,
    cost_usd NUMERIC DEFAULT 0,
    revenue_usd NUMERIC DEFAULT 0,
    status TEXT DEFAULT 'success',
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Vector similarity search function
CREATE OR REPLACE FUNCTION match_memories(
    query_embedding VECTOR(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5,
    filter_category TEXT DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    content TEXT,
    category TEXT,
    importance_score NUMERIC,
    metadata JSONB,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        am.id,
        am.content,
        am.category,
        am.importance_score,
        am.metadata,
        1 - (am.embedding <=> query_embedding) AS similarity
    FROM agent_memory am
    WHERE 1 - (am.embedding <=> query_embedding) > match_threshold
    AND (filter_category IS NULL OR am.category = filter_category)
    ORDER BY am.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Insert initial portfolio state
INSERT INTO portfolio (total_value, cash_available, strategy_allocations)
VALUES (1000, 1000, '{"polymarket_arb": 300, "solana_swing": 250, "polymarket_conviction": 200, "near_resolution": 150, "cash_reserve": 100}');
