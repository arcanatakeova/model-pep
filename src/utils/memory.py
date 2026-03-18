"""Supabase pgvector memory system.

Every trade outcome, content metric, market observation is embedded as a 1536-dim
vector and stored for similarity-based recall before every major decision.

Categories: trade_outcome, market_pattern, content_performance, lead_interaction, strategy_adjustment
Importance scoring: 0-1, outcomes deviating from prediction score highest.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from supabase import AsyncClient as SupabaseClient

from src.utils.llm import LLMClient

logger = logging.getLogger("arcana.memory")


class Memory(BaseModel):
    id: str | None = None
    content: str
    category: str
    importance_score: float = 0.5
    metadata: dict[str, Any] = {}
    similarity: float | None = None
    created_at: datetime | None = None


class MemorySystem:
    """Semantic memory backed by Supabase pgvector."""

    VALID_CATEGORIES = {
        "trade_outcome",
        "market_pattern",
        "content_performance",
        "lead_interaction",
        "strategy_adjustment",
    }

    def __init__(self, supabase: SupabaseClient, llm: LLMClient) -> None:
        self.db = supabase
        self.llm = llm

    async def store(
        self,
        content: str,
        category: str,
        importance_score: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Embed and store a memory. Returns the new memory ID."""
        if category not in self.VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {category}. Must be one of {self.VALID_CATEGORIES}")

        embedding = await self.llm.embed(content)

        row = {
            "content": content,
            "embedding": embedding,
            "category": category,
            "importance_score": max(0.0, min(1.0, importance_score)),
            "metadata": metadata or {},
        }

        result = await self.db.table("agent_memory").insert(row).execute()
        memory_id = result.data[0]["id"]
        logger.info("Stored memory %s (category=%s, importance=%.2f)", memory_id, category, importance_score)
        return memory_id

    async def recall(
        self,
        query: str,
        category: str | None = None,
        threshold: float = 0.7,
        limit: int = 5,
    ) -> list[Memory]:
        """Find similar memories using pgvector cosine similarity."""
        embedding = await self.llm.embed(query)

        result = await self.db.rpc(
            "match_memories",
            {
                "query_embedding": embedding,
                "match_threshold": threshold,
                "match_count": limit,
                "filter_category": category,
            },
        ).execute()

        memories = [
            Memory(
                id=row["id"],
                content=row["content"],
                category=row["category"],
                importance_score=float(row["importance_score"]),
                metadata=row.get("metadata", {}),
                similarity=float(row["similarity"]),
            )
            for row in result.data
        ]

        logger.info("Recalled %d memories for query (category=%s)", len(memories), category)
        return memories

    async def recall_context(self, query: str, category: str | None = None) -> str:
        """Recall memories and format as context string for LLM prompts."""
        memories = await self.recall(query, category=category)
        if not memories:
            return "No relevant memories found."

        lines = ["Relevant memories from past experience:"]
        for m in memories:
            lines.append(
                f"- [{m.category}] (importance: {m.importance_score:.2f}, "
                f"similarity: {m.similarity:.2f}): {m.content}"
            )
        return "\n".join(lines)

    async def calculate_importance(self, content: str, predicted_outcome: str | None = None) -> float:
        """Calculate importance score — surprising deviations score highest."""
        if predicted_outcome is None:
            return 0.5

        prompt = (
            f"Rate how surprising this outcome is compared to the prediction on a scale of 0.0 to 1.0.\n"
            f"Prediction: {predicted_outcome}\n"
            f"Actual outcome: {content}\n"
            f"Respond with ONLY a number between 0.0 and 1.0."
        )
        from src.utils.llm import ModelTier
        raw = await self.llm.complete(prompt, tier=ModelTier.HAIKU, temperature=0.1, max_tokens=10)
        try:
            return max(0.0, min(1.0, float(raw.strip())))
        except ValueError:
            return 0.5
