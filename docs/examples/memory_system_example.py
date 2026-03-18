"""Example: Supabase pgvector Memory System

This is a REFERENCE PATTERN for Claude Code to implement src/utils/memory.py.
"""
import os
import httpx
from supabase import create_client, Client
from pydantic import BaseModel
from datetime import datetime


supabase: Client = create_client(
    os.getenv("SUPABASE_URL", ""),
    os.getenv("SUPABASE_SERVICE_KEY", ""),
)


class Memory(BaseModel):
    content: str
    category: str  # trade_outcome, market_pattern, content_performance, lead_interaction, strategy_adjustment
    importance_score: float = 0.5
    metadata: dict = {}


async def embed(text: str) -> list[float]:
    """Convert text to 1536-dim vector via OpenAI ada-002 through OpenRouter."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
            json={
                "model": "openai/text-embedding-ada-002",
                "input": text,
            },
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]


async def store(memory: Memory) -> str:
    """Store a memory with its vector embedding."""
    embedding = await embed(memory.content)
    
    result = supabase.table("agent_memory").insert({
        "content": memory.content,
        "embedding": embedding,
        "category": memory.category,
        "importance_score": memory.importance_score,
        "metadata": memory.metadata,
    }).execute()
    
    return result.data[0]["id"]


async def recall(
    query: str,
    category: str | None = None,
    threshold: float = 0.7,
    limit: int = 5,
) -> list[dict]:
    """Find similar memories using pgvector cosine similarity.
    
    Call this BEFORE every major decision:
    "What happened last time I saw a signal like this?"
    """
    query_embedding = await embed(query)
    
    result = supabase.rpc("match_memories", {
        "query_embedding": query_embedding,
        "match_threshold": threshold,
        "match_count": limit,
        "filter_category": category,
    }).execute()
    
    return result.data


async def learn_from_outcome(
    prediction: str,
    actual_outcome: str,
    context: dict,
) -> str:
    """Store outcome and score importance based on surprise factor.
    
    Outcomes that DEVIATE from prediction score highest (learning from surprises).
    """
    # Use LLM to assess how surprising the outcome was
    from .llm import call_llm, ModelTier
    
    assessment = await call_llm(
        f"On a scale of 0.0 to 1.0, how surprising was this outcome?\n"
        f"Prediction: {prediction}\n"
        f"Actual: {actual_outcome}\n"
        f"Respond with ONLY a number between 0.0 and 1.0.",
        tier=ModelTier.ROUTINE,
        temperature=0.1,
    )
    
    try:
        importance = float(assessment.content.strip())
    except ValueError:
        importance = 0.5
    
    return await store(Memory(
        content=f"Predicted: {prediction}. Actual: {actual_outcome}",
        category="trade_outcome" if "trade" in context.get("type", "") else "strategy_adjustment",
        importance_score=min(1.0, max(0.0, importance)),
        metadata={**context, "timestamp": datetime.utcnow().isoformat()},
    ))
