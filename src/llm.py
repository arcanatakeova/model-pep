"""ARCANA AI — Production-grade OpenRouter LLM client.

Features:
- SOUL.md injection as system prompt on every call
- Tiered model routing (Haiku/Sonnet/Opus) with cost awareness
- Rate limiting with sliding window (configurable per hour)
- Token and cost tracking per tier
- Automatic tier fallback on failure (Opus → Sonnet → Haiku)
- JSON parsing with repair for malformed responses
- Circuit breaker: stops calling after N consecutive failures
- Request timeout with per-tier tuning
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from enum import Enum
from typing import Any

import httpx

from src.config import Config, get_config

logger = logging.getLogger("arcana.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Cost per 1M tokens (approximate, for tracking)
TIER_COSTS = {
    "haiku": {"input": 0.25, "output": 1.25},
    "sonnet": {"input": 3.0, "output": 15.0},
    "opus": {"input": 15.0, "output": 75.0},
}

# Timeouts per tier (Opus gets more time)
TIER_TIMEOUTS = {
    "haiku": 30.0,
    "sonnet": 90.0,
    "opus": 180.0,
}

CIRCUIT_BREAKER_THRESHOLD = 5  # Consecutive failures before circuit opens
CIRCUIT_BREAKER_RESET = 120     # Seconds before retrying after circuit opens


class Tier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class LLM:
    """Production-grade async OpenRouter client with cost tracking and fallback."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()
        self._soul = self.config.load_soul()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {self.config.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://arcanaoperations.com",
                "X-Title": "ARCANA AI",
            },
        )
        # Response cache (avoid duplicate LLM calls)
        try:
            from cachetools import TTLCache
            self._response_cache: dict[str, str] = TTLCache(maxsize=100, ttl=300)  # 5-min TTL
        except ImportError:
            self._response_cache = {}

        # Rate limiting
        self._call_timestamps: deque[float] = deque(maxlen=1000)
        self._max_per_hour = self.config.max_llm_calls_per_hour

        # Cost tracking
        self._total_tokens: dict[str, int] = {"haiku": 0, "sonnet": 0, "opus": 0}
        self._total_calls: dict[str, int] = {"haiku": 0, "sonnet": 0, "opus": 0}
        self._total_cost = 0.0

        # Circuit breaker
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _model(self, tier: Tier) -> str:
        return {
            Tier.HAIKU: self.config.haiku_model,
            Tier.SONNET: self.config.sonnet_model,
            Tier.OPUS: self.config.opus_model,
        }[tier]

    async def _check_rate_limit(self) -> None:
        """Enforce per-hour rate limit with sliding window."""
        now = time.monotonic()
        # Remove timestamps older than 1 hour
        while self._call_timestamps and now - self._call_timestamps[0] > 3600:
            self._call_timestamps.popleft()

        if len(self._call_timestamps) >= self._max_per_hour:
            wait = 3600 - (now - self._call_timestamps[0]) + 0.5
            if wait > 0:
                logger.warning("LLM rate limit reached (%d/hr), waiting %.0fs",
                               self._max_per_hour, wait)
                await asyncio.sleep(wait)

        self._call_timestamps.append(time.monotonic())

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker is open."""
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            if time.monotonic() < self._circuit_open_until:
                raise RuntimeError(
                    f"LLM circuit breaker open — {self._consecutive_failures} consecutive "
                    f"failures. Resets in {self._circuit_open_until - time.monotonic():.0f}s"
                )
            # Reset and try again
            logger.info("Circuit breaker reset — retrying LLM calls")
            self._consecutive_failures = 0

    def _track_usage(self, tier: Tier, usage: dict[str, Any]) -> None:
        """Track token usage and estimated cost."""
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total = input_tokens + output_tokens

        self._total_tokens[tier.value] += total
        self._total_calls[tier.value] += 1

        # Estimate cost
        costs = TIER_COSTS.get(tier.value, {"input": 0, "output": 0})
        cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
        self._total_cost += cost

    # ── Core API ─────────────────────────────────────────────────

    async def ask(
        self,
        prompt: str,
        tier: Tier = Tier.SONNET,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Single-turn completion with SOUL.md as system prompt + response cache."""
        # Check cache for identical prompts (saves API cost)
        from src.toolkit import fast_hash
        cache_key = fast_hash(f"{tier.value}:{prompt}")
        cached = self._response_cache.get(cache_key)
        if cached and temperature <= 0.3:  # Only cache deterministic calls
            logger.debug("LLM cache hit for %s", cache_key[:8])
            return cached

        messages = [
            {"role": "system", "content": system or self._soul},
            {"role": "user", "content": prompt},
        ]
        result = await self._call(messages, tier, temperature, max_tokens, json_mode)

        # Cache low-temperature responses
        if temperature <= 0.3:
            try:
                self._response_cache[cache_key] = result
            except (ValueError, TypeError):
                pass  # Cache full or invalid

        return result

    async def ask_json(self, prompt: str, tier: Tier = Tier.SONNET) -> dict[str, Any]:
        """Ask and parse response as JSON with repair for malformed responses."""
        raw = await self.ask(prompt, tier=tier, json_mode=True, temperature=0.3)
        return self._parse_json(raw)

    def _parse_json(self, raw: str) -> dict[str, Any]:
        """Parse JSON with repair for common LLM output issues."""
        # Try direct parse first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code blocks
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { to last }
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass

        # Try fixing common issues: trailing commas, single quotes
        cleaned = raw.strip()
        if cleaned.startswith("{"):
            cleaned = re.sub(r",\s*}", "}", cleaned)
            cleaned = re.sub(r",\s*]", "]", cleaned)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

        logger.error("Failed to parse JSON response: %s", raw[:200])
        return {}

    async def _call(
        self,
        messages: list[dict],
        tier: Tier,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """Make API call with rate limiting, retries, circuit breaker, and fallback."""
        self._check_circuit_breaker()
        await self._check_rate_limit()

        timeout = TIER_TIMEOUTS.get(tier.value, 120.0)

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
                resp = await self._client.post(
                    OPENROUTER_URL, json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})

                self._track_usage(tier, usage)
                self._consecutive_failures = 0  # Reset on success

                logger.info(
                    "LLM [%s] tokens=%s cost=$%.4f",
                    tier.value,
                    usage.get("total_tokens", "?"),
                    (usage.get("prompt_tokens", 0) * TIER_COSTS.get(tier.value, {}).get("input", 0)
                     + usage.get("completion_tokens", 0) * TIER_COSTS.get(tier.value, {}).get("output", 0)) / 1_000_000,
                )
                return content

            except httpx.HTTPStatusError as exc:
                last_err = exc
                status = exc.response.status_code

                if status == 429:
                    # Rate limited — wait and retry
                    retry_after = float(exc.response.headers.get("retry-after", 5))
                    logger.warning("OpenRouter 429, waiting %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                elif status == 402:
                    logger.error("OpenRouter 402 — insufficient credits")
                    raise
                elif status >= 500:
                    logger.warning("OpenRouter %d, attempt %d/3", status, attempt + 1)
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                else:
                    raise

            except (httpx.RequestError, KeyError, IndexError) as exc:
                last_err = exc
                logger.warning("LLM attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(2 ** (attempt + 1))

        # All retries exhausted — try fallback tier
        self._consecutive_failures += 1
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open_until = time.monotonic() + CIRCUIT_BREAKER_RESET

        fallback = self._get_fallback_tier(tier)
        if fallback and fallback != tier:
            logger.warning("Falling back from %s to %s", tier.value, fallback.value)
            payload["model"] = self._model(fallback)
            try:
                resp = await self._client.post(
                    OPENROUTER_URL, json=payload,
                    timeout=TIER_TIMEOUTS.get(fallback.value, 120.0),
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                self._track_usage(fallback, data.get("usage", {}))
                self._consecutive_failures = 0
                return content
            except Exception as fallback_err:
                logger.error("Fallback to %s also failed: %s", fallback.value, fallback_err)

        raise RuntimeError(f"LLM failed after 3 retries + fallback: {last_err}")

    @staticmethod
    def _get_fallback_tier(tier: Tier) -> Tier | None:
        """Get the fallback tier for a given tier."""
        return {
            Tier.OPUS: Tier.SONNET,
            Tier.SONNET: Tier.HAIKU,
            Tier.HAIKU: None,
        }.get(tier)

    # ── Stats & Monitoring ───────────────────────────────────────

    def get_usage_stats(self) -> dict[str, Any]:
        """Get usage stats for monitoring."""
        return {
            "total_calls": sum(self._total_calls.values()),
            "calls_by_tier": dict(self._total_calls),
            "tokens_by_tier": dict(self._total_tokens),
            "estimated_cost": round(self._total_cost, 4),
            "calls_this_hour": len(self._call_timestamps),
            "rate_limit": self._max_per_hour,
            "circuit_breaker": "open" if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD else "closed",
        }

    def format_usage_report(self) -> str:
        """Format usage for morning report."""
        stats = self.get_usage_stats()
        return (
            f"**LLM Usage**: {stats['total_calls']} calls | "
            f"${stats['estimated_cost']:.2f} est. cost\n"
            f"  Haiku: {self._total_calls['haiku']} | "
            f"Sonnet: {self._total_calls['sonnet']} | "
            f"Opus: {self._total_calls['opus']}"
        )

    async def close(self) -> None:
        """Clean shutdown with stats logging."""
        stats = self.get_usage_stats()
        logger.info(
            "LLM shutdown — %d calls, $%.4f est. cost, %d tokens total",
            stats["total_calls"], stats["estimated_cost"],
            sum(self._total_tokens.values()),
        )
        await self._client.aclose()
