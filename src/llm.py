"""ARCANA AI — OpenRouter LLM client with SOUL.md injection.

Model routing:
- Haiku ($0.001/call): Quick classification, yes/no decisions, triage
- Sonnet ($0.015/call): Content generation, lead qualification, analysis
- Opus ($0.075/call): Strategy reviews, nightly self-improvement, complex decisions
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import Any

import httpx

from src.config import Config, get_config

logger = logging.getLogger("arcana.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class Tier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class LLM:
    """Async OpenRouter client. SOUL.md injected as system prompt on every call."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()
        self._soul = self.config.load_soul()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {self.config.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://arcanaoperations.com",
                "X-Title": "ARCANA AI",
            },
        )

    def _model(self, tier: Tier) -> str:
        return {
            Tier.HAIKU: self.config.haiku_model,
            Tier.SONNET: self.config.sonnet_model,
            Tier.OPUS: self.config.opus_model,
        }[tier]

    async def ask(
        self,
        prompt: str,
        tier: Tier = Tier.SONNET,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Single-turn completion with SOUL.md as system prompt."""
        messages = [
            {"role": "system", "content": system or self._soul},
            {"role": "user", "content": prompt},
        ]
        return await self._call(messages, tier, temperature, max_tokens, json_mode)

    async def ask_json(self, prompt: str, tier: Tier = Tier.SONNET) -> dict[str, Any]:
        """Ask and parse response as JSON."""
        raw = await self.ask(prompt, tier=tier, json_mode=True, temperature=0.3)
        return json.loads(raw)

    async def _call(
        self,
        messages: list[dict],
        tier: Tier,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model(tier),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.post(OPENROUTER_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                logger.info("LLM [%s] tokens=%s", tier.value, data.get("usage", {}).get("total_tokens", "?"))
                return content
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError) as exc:
                last_err = exc
                logger.warning("LLM attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(2 ** (attempt + 1))

        raise RuntimeError(f"LLM failed after 3 retries: {last_err}")

    async def close(self) -> None:
        await self._client.aclose()
