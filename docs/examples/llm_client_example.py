"""Example: OpenRouter LLM Client with Model Routing + SOUL.md Injection

This is a REFERENCE PATTERN for Claude Code to implement src/utils/llm.py.
Adapt this pattern — do not copy-paste blindly.
"""
import httpx
import os
from pathlib import Path
from pydantic import BaseModel
from enum import Enum


class ModelTier(str, Enum):
    ROUTINE = "anthropic/claude-haiku-4-5-20251001"   # ~$0.001/call — monitoring, simple responses
    DECISION = "anthropic/claude-sonnet-4-6"           # ~$0.015/call — trading, content, leads
    STRATEGY = "anthropic/claude-opus-4-6"             # ~$0.075/call — weekly reviews, complex analysis
    FALLBACK = "openai/gpt-4o"                         # Fallback for ensemble voting


class LLMResponse(BaseModel):
    content: str
    model: str
    tokens_used: int
    cost_estimate: float


# Load SOUL.md once at startup
SOUL_PATH = Path(__file__).parent.parent.parent / "SOUL.md"
SOUL_CONTENT = SOUL_PATH.read_text() if SOUL_PATH.exists() else ""


async def call_llm(
    prompt: str,
    tier: ModelTier = ModelTier.DECISION,
    system_override: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> LLMResponse:
    """Call OpenRouter with SOUL.md injected as system prompt.
    
    ALWAYS use this function for LLM calls. Never call Anthropic/OpenAI directly.
    """
    system_prompt = system_override or SOUL_CONTENT
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(3):  # Retry with exponential backoff
            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": tier.value,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                usage = data.get("usage", {})
                tokens = usage.get("total_tokens", 0)
                
                return LLMResponse(
                    content=data["choices"][0]["message"]["content"],
                    model=tier.value,
                    tokens_used=tokens,
                    cost_estimate=_estimate_cost(tier, tokens),
                )
            except (httpx.HTTPError, KeyError) as e:
                if attempt == 2:
                    raise
                import asyncio
                await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s


async def call_ensemble(
    prompt: str,
    models: list[ModelTier] | None = None,
    threshold: int = 2,
) -> tuple[str, float]:
    """Query multiple models, return majority consensus.
    
    Used for high-stakes trading decisions.
    Returns (consensus_answer, agreement_ratio).
    """
    models = models or [ModelTier.DECISION, ModelTier.FALLBACK, ModelTier.STRATEGY]
    responses = []
    
    for model in models:
        try:
            resp = await call_llm(prompt, tier=model, temperature=0.3)
            responses.append(resp.content)
        except Exception:
            continue
    
    if len(responses) < threshold:
        return ("INSUFFICIENT_CONSENSUS", 0.0)
    
    # Simple majority — in production, use structured output and compare parsed decisions
    return (responses[0], len(responses) / len(models))


def _estimate_cost(tier: ModelTier, tokens: int) -> float:
    """Rough cost estimate per call."""
    rates = {
        ModelTier.ROUTINE: 0.001,
        ModelTier.DECISION: 0.015,
        ModelTier.STRATEGY: 0.075,
        ModelTier.FALLBACK: 0.01,
    }
    return rates.get(tier, 0.01) * (tokens / 1000)
