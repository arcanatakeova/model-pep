"""
Probability Engine
==================
LLM-based probability estimation for prediction markets.
Uses Claude (Anthropic) as primary, OpenAI as fallback.
"""
from __future__ import annotations
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import PolyMarket, ProbabilityEstimate

logger = logging.getLogger(__name__)


class ProbabilityEngine:
    """Estimates true probabilities for prediction markets using LLMs."""

    def __init__(self, news_client=None, cross_platform=None):
        self._anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._openai_key = os.getenv("OPENAI_API_KEY", "")
        self._news = news_client
        self._cross_platform = cross_platform
        self._session = requests.Session()
        self._cache: dict[str, tuple[ProbabilityEstimate, float]] = {}
        self._cache_ttl = 600  # 10 min
        self._calls_this_cycle = 0
        self._max_calls_per_cycle = 20

    def reset_cycle_counter(self):
        """Reset LLM call counter at the start of each scan cycle."""
        self._calls_this_cycle = 0

    def estimate_probability(self, market: PolyMarket) -> Optional[ProbabilityEstimate]:
        """
        Estimate true probability for a market using LLM analysis.
        Returns None if the estimation fails or is rate-limited.
        """
        # Check cache
        cache_key = market.condition_id
        if cache_key in self._cache:
            est, ts = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return est

        # Rate limit
        if self._calls_this_cycle >= self._max_calls_per_cycle:
            logger.debug("LLM rate limit reached (%d calls this cycle)", self._calls_this_cycle)
            return None

        # Gather context
        context = self._gather_context(market)
        prompt = self._build_prompt(market, context)

        # Try Claude first, then OpenAI
        raw_response = None
        model_used = ""

        if self._anthropic_key:
            raw_response = self._call_claude(prompt)
            model_used = "claude-sonnet-4-20250514"

        if not raw_response and self._openai_key:
            raw_response = self._call_openai(prompt)
            model_used = "gpt-4o"

        if not raw_response:
            return None

        self._calls_this_cycle += 1

        # Parse response
        prob, confidence, reasoning = self._parse_llm_response(raw_response)
        if prob < 0:
            return None

        estimate = ProbabilityEstimate(
            market_question=market.question,
            estimated_prob=prob,
            confidence=confidence,
            reasoning=reasoning,
            sources_used=context.get("sources", []),
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_used=model_used,
        )

        self._cache[cache_key] = (estimate, time.time())
        return estimate

    def batch_estimate(self, markets: list[PolyMarket]) -> list[ProbabilityEstimate]:
        """Estimate probabilities for multiple markets, respecting rate limits."""
        results = []
        for mkt in markets:
            est = self.estimate_probability(mkt)
            if est:
                results.append(est)
            if self._calls_this_cycle >= self._max_calls_per_cycle:
                break
        return results

    def _build_prompt(self, market: PolyMarket, context: dict) -> str:
        """Build LLM prompt for probability estimation."""
        news_section = ""
        if context.get("news"):
            headlines = "\n".join(f"- {h}" for h in context["news"][:5])
            news_section = f"\n\nRecent related news:\n{headlines}"

        cross_section = ""
        if context.get("cross_platform_prob") is not None:
            cross_section = (
                f"\n\nOther prediction markets estimate: "
                f"{context['cross_platform_prob']:.1%} "
                f"(from {context.get('cross_platform_source', 'unknown')})"
            )

        return f"""You are an expert prediction market analyst. Estimate the probability that the following event will occur.

Market question: {market.question}
{f"Description: {market.description}" if market.description else ""}
Current market price (YES): {market.yes_price:.2%}
End date: {market.end_date}
Tags: {", ".join(market.tags) if market.tags else "none"}
{news_section}{cross_section}

Instructions:
1. Consider all available information including the market question, current odds, news, and cross-platform data.
2. Estimate the TRUE probability of the event occurring.
3. Rate your confidence in your estimate (0.0 = no confidence, 1.0 = very confident).
4. Provide brief reasoning.

Respond ONLY with valid JSON in this exact format:
{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "brief explanation"}}"""

    def _call_claude(self, prompt: str) -> Optional[str]:
        """Call Anthropic Claude API."""
        try:
            resp = self._session.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=15,
            )
            if resp.ok:
                data = resp.json()
                content = data.get("content", [])
                if content:
                    return content[0].get("text", "")
        except Exception as e:
            logger.debug("Claude API error: %s", e)
        return None

    def _call_openai(self, prompt: str) -> Optional[str]:
        """Call OpenAI API."""
        try:
            resp = self._session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=15,
            )
            if resp.ok:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.debug("OpenAI API error: %s", e)
        return None

    def _parse_llm_response(self, raw: str) -> tuple[float, float, str]:
        """Parse LLM JSON response into (probability, confidence, reasoning)."""
        try:
            # Extract JSON from response (handle markdown code blocks)
            text = raw.strip()
            if "```" in text:
                # Extract content between code fences
                parts = text.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        text = part
                        break

            data = json.loads(text)
            prob = float(data.get("probability", -1))
            conf = float(data.get("confidence", 0.5))
            reason = str(data.get("reasoning", ""))

            if not (0.0 <= prob <= 1.0):
                return -1.0, 0.0, ""
            conf = max(0.0, min(1.0, conf))

            return prob, conf, reason
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.debug("LLM response parse error: %s (raw: %s)", e, raw[:100])
            return -1.0, 0.0, ""

    def _gather_context(self, market: PolyMarket) -> dict:
        """Gather context from news and cross-platform sources."""
        context: dict = {"sources": [], "news": [], "cross_platform_prob": None}

        # News
        if self._news:
            try:
                articles = self._news.get_relevant_news(market, limit=5)
                context["news"] = [a.get("title", "") for a in articles]
                if articles:
                    context["sources"].append("news")
            except Exception:
                pass

        # Cross-platform prices
        if self._cross_platform:
            try:
                consensus = self._cross_platform.get_consensus(market)
                if consensus:
                    context["cross_platform_prob"] = consensus.probability
                    context["cross_platform_source"] = consensus.platform
                    context["sources"].append(consensus.platform)
            except Exception:
                pass

        return context
