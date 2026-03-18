"""OpenRouter LLM client with model routing and SOUL.md injection.

Routes calls to the appropriate Claude model based on task complexity:
- Haiku ($0.001/call): Routine monitoring, signal aggregation, simple responses
- Sonnet ($0.015/call): Trading decisions, content generation, lead qualification
- Opus ($0.075/call): Strategy reviews, complex analysis, weekly postmortems
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

import httpx

from src.config import ArcanaConfig, get_config

logger = logging.getLogger("arcana.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
EMBEDDING_URL = "https://openrouter.ai/api/v1/embeddings"


class ModelTier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


def _model_id(tier: ModelTier, config: ArcanaConfig) -> str:
    return {
        ModelTier.HAIKU: config.llm.haiku_model,
        ModelTier.SONNET: config.llm.sonnet_model,
        ModelTier.OPUS: config.llm.opus_model,
    }[tier]


class LLMClient:
    """Async OpenRouter client with SOUL.md injection and model routing."""

    def __init__(self, config: ArcanaConfig | None = None) -> None:
        self.config = config or get_config()
        self._soul_prompt = self.config.load_soul()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {self.config.llm.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://arcanaoperations.com",
                "X-Title": "ARCANA AI",
            },
        )

    async def complete(
        self,
        prompt: str,
        tier: ModelTier = ModelTier.SONNET,
        system_override: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Send a completion request to OpenRouter with SOUL.md injected."""
        system_msg = system_override or self._soul_prompt
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ]
        return await self._chat(messages, tier, temperature, max_tokens, json_mode)

    async def chat(
        self,
        messages: list[dict[str, str]],
        tier: ModelTier = ModelTier.SONNET,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        inject_soul: bool = True,
    ) -> str:
        """Send a multi-turn chat request."""
        if inject_soul and (not messages or messages[0].get("role") != "system"):
            messages = [{"role": "system", "content": self._soul_prompt}] + messages
        return await self._chat(messages, tier, temperature, max_tokens, json_mode)

    async def _chat(
        self,
        messages: list[dict[str, str]],
        tier: ModelTier,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Internal chat method with retry logic."""
        model = _model_id(tier, self.config)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.post(OPENROUTER_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                logger.info(
                    "LLM call: model=%s tokens=%s",
                    model,
                    data.get("usage", {}).get("total_tokens", "?"),
                )
                return content
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError) as exc:
                last_error = exc
                wait = 2 ** (attempt + 1)
                logger.warning("LLM call failed (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
                import asyncio
                await asyncio.sleep(wait)

        raise RuntimeError(f"LLM call failed after 3 retries: {last_error}")

    async def embed(self, text: str) -> list[float]:
        """Generate a 1536-dim embedding via OpenRouter (ada-002)."""
        payload = {
            "model": "openai/text-embedding-ada-002",
            "input": text,
        }
        for attempt in range(3):
            try:
                resp = await self._client.post(EMBEDDING_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError) as exc:
                wait = 2 ** (attempt + 1)
                logger.warning("Embed call failed (attempt %d): %s", attempt + 1, exc)
                import asyncio
                await asyncio.sleep(wait)

        raise RuntimeError("Embedding call failed after 3 retries")

    async def complete_json(
        self,
        prompt: str,
        tier: ModelTier = ModelTier.SONNET,
    ) -> dict[str, Any]:
        """Complete and parse the response as JSON."""
        raw = await self.complete(prompt, tier=tier, json_mode=True, temperature=0.3)
        return json.loads(raw)

    async def close(self) -> None:
        await self._client.aclose()
