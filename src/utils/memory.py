"""Supabase pgvector memory system — ARCANA AI's long-term learning.
Read docs/ARCHITECTURE.md before modifying."""

import os
import httpx
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

EMBED_MODEL = "text-embedding-ada-002"
EMBED_DIM = 1536

class Memory(BaseModel):
    id: str
    content: str
    category: str
    importance_score: float
    similarity: Optional[float] = None

async def embed(text: str) -> list[float]:
    """Convert text to 1536-dim vector via OpenAI ada-002 (NOT OpenRouter — it doesn't proxy embeddings)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": EMBED_MODEL, "input": text},
        )
        resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]

async def store(content: str, category: str, importance: float = 0.5, metadata: dict | None = None) -> str:
    """Embed content and store in Supabase agent_memory table."""
    vector = await embed(content)
    payload = {
        "content": content,
        "embedding": vector,
        "category": category,
        "importance_score": importance,
        "metadata": metadata or {},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{os.environ['SUPABASE_URL']}/rest/v1/agent_memory",
            headers={
                "apikey": os.environ["SUPABASE_ANON_KEY"],
                "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_KEY']}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json=payload,
        )
        resp.raise_for_status()
    return resp.json()[0]["id"]

async def recall(query: str, category: str | None = None, threshold: float = 0.7, limit: int = 5) -> list[Memory]:
    """Search memory using cosine similarity via match_memories Supabase function."""
    vector = await embed(query)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{os.environ['SUPABASE_URL']}/rest/v1/rpc/match_memories",
            headers={
                "apikey": os.environ["SUPABASE_ANON_KEY"],
                "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "query_embedding": vector,
                "match_threshold": threshold,
                "match_count": limit,
                "filter_category": category,
            },
        )
        resp.raise_for_status()
    return [Memory(**row) for row in resp.json()]
