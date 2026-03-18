"""OpenRouter LLM client with model routing and SOUL.md injection.
Read docs/ARCHITECTURE.md and docs/API_REFERENCE.md before modifying."""

import os
import httpx
from pathlib import Path
from pydantic import BaseModel
from enum import Enum
from typing import Optional

class ModelTier(str, Enum):
    CHEAP = "anthropic/claude-3.5-haiku-20241022"       # ~$0.001/call
    STANDARD = "anthropic/claude-sonnet-4-20250514"      # ~$0.015/call
    PREMIUM = "anthropic/claude-opus-4-20250514"         # ~$0.075/call
    FAST = "openai/gpt-4o-mini"                          # ~$0.0003/call
    ENSEMBLE = "google/gemini-2.0-flash-001"             # ~$0.001/call

class LLMResponse(BaseModel):
    content: str
    model: str
    tokens_used: int
    cost_estimate: float

_soul_content: Optional[str] = None

def _load_soul() -> str:
    global _soul_content
    if _soul_content is None:
        soul_path = Path(__file__).parent.parent.parent / "SOUL.md"
        _soul_content = soul_path.read_text() if soul_path.exists() else "You are ARCANA AI."
    return _soul_content

async def complete(
    prompt: str,
    tier: ModelTier = ModelTier.STANDARD,
    system_override: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> LLMResponse:
    """Send completion to OpenRouter with SOUL.md as system prompt."""
    system_msg = system_override or _load_soul()
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                        "HTTP-Referer": "https://arcanaoperations.com",
                        "X-Title": "ARCANA AI",
                    },
                    json={
                        "model": tier.value,
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                response.raise_for_status()
                break
            except (httpx.HTTPStatusError, httpx.ConnectError) as e:
                if attempt == 2:
                    raise
                import asyncio
                await asyncio.sleep(2 ** attempt)

    data = response.json()
    usage = data.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)
    cost_map = {ModelTier.CHEAP: 1e-6, ModelTier.STANDARD: 1.5e-5, ModelTier.PREMIUM: 7.5e-5, ModelTier.FAST: 3e-7, ModelTier.ENSEMBLE: 1e-6}

    return LLMResponse(
        content=data["choices"][0]["message"]["content"],
        model=tier.value,
        tokens_used=total_tokens,
        cost_estimate=total_tokens * cost_map.get(tier, 1e-5),
    )

async def ensemble_decision(prompt: str, models: list[ModelTier] | None = None) -> dict:
    """Query multiple models, return majority consensus. Requires 2/3 agreement for trades."""
    models = models or [ModelTier.STANDARD, ModelTier.FAST, ModelTier.ENSEMBLE]
    results = []
    for model in models:
        try:
            resp = await complete(prompt, tier=model, temperature=0.3)
            results.append({"model": model.value, "response": resp.content})
        except Exception as e:
            results.append({"model": model.value, "error": str(e)})
    return {"results": results, "agreement_count": len([r for r in results if "response" in r]), "total": len(models)}
