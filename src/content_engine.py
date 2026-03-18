"""ARCANA AI — Content generation engine.

Generates all X content using LLM with SOUL.md personality:
- Morning Briefings (daily, 7 AM PT)
- Case Files (2x weekly — showcases Arcana Operations expertise)
- Industry Analysis (3-5x daily — builds authority)
- Behind-the-Scenes (2-3x weekly — shows AI capabilities)
- Product Launches (as needed)

Content is the marketing. The agent IS the pitch.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.content")


class ContentEngine:
    """Generates content for X, email, and products."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    async def morning_briefing(self) -> list[str]:
        """Generate the daily Morning Briefing thread."""
        recent = self.memory.get_recent_days(3)
        context = "\n".join(content[:500] for _, content in recent)

        result = await self.llm.ask_json(
            f"Generate a Morning Briefing thread for ARCANA AI to post on X.\n\n"
            f"Today: {datetime.now(timezone.utc).strftime('%B %d, %Y')}\n"
            f"Recent context from memory:\n{context}\n\n"
            f"Format: 4-5 tweets as a thread.\n"
            f"Tweet 1: ☀️ ARCANA MORNING BRIEFING — [date]. Overview of what's happening.\n"
            f"Tweet 2-3: Key insights, observations, or things ARCANA is working on today.\n"
            f"Tweet 4: What ARCANA is building/shipping today.\n"
            f"Tweet 5: Closing with personality. Mention arcanaoperations.com\n\n"
            f"Rules: Under 280 chars each. No hype. No price predictions. ARCANA personality.\n"
            f'Return JSON: {{"tweets": [str, str, str, str, str]}}',
            tier=Tier.SONNET,
        )
        tweets = result.get("tweets", [])
        self.memory.log(f"Generated Morning Briefing: {len(tweets)} tweets", "Content")
        return tweets

    async def case_file(self) -> list[str]:
        """Generate a Case File thread — demonstrates Arcana Operations expertise."""
        businesses = [
            {"name": "Navigate Peptides", "type": "e-commerce",
             "topics": "headless Shopify, automated compliance, SEO for regulated products, inventory automation"},
            {"name": "Autobahn Collective", "type": "marketplace",
             "topics": "multi-platform listing (eBay + FB + Shopify), parts CRM, community building"},
            {"name": "AI Agent Deployment", "type": "consulting",
             "topics": "autonomous agents, workflow automation, revenue optimization, cost reduction"},
            {"name": "Local Business SEO", "type": "consulting",
             "topics": "Google Business optimization, review management, local content strategy"},
        ]
        biz = random.choice(businesses)
        num = random.randint(1, 99)

        result = await self.llm.ask_json(
            f"Generate a Case File thread for ARCANA AI's X account.\n\n"
            f"Business: {biz['name']} ({biz['type']})\n"
            f"Topics: {biz['topics']}\n"
            f"Case File #{num}\n\n"
            f"Format: 5-tweet thread.\n"
            f"Tweet 1: 🗂️ CASE FILE #{num} — [catchy title]\n"
            f"Tweet 2: The problem the client faced\n"
            f"Tweet 3: What Arcana Operations built/implemented\n"
            f"Tweet 4: The results (use realistic metrics)\n"
            f"Tweet 5: How we can do this for your business. arcanaoperations.com\n\n"
            f"Rules: Specific, data-driven, not salesy. Show expertise through detail.\n"
            f'Return JSON: {{"tweets": [str, str, str, str, str]}}',
            tier=Tier.SONNET,
        )
        tweets = result.get("tweets", [])
        self.memory.log(f"Generated Case File #{num}: {biz['name']}", "Content")
        return tweets

    async def analysis_tweet(self) -> str:
        """Generate a single sharp analysis/insight tweet."""
        recent = self.memory.get_recent_days(2)
        context = "\n".join(content[:300] for _, content in recent)

        result = await self.llm.ask_json(
            f"Generate a single tweet for ARCANA AI.\n"
            f"Recent context:\n{context}\n\n"
            f"Pick ONE of these angles:\n"
            f"- AI industry insight or pattern\n"
            f"- Business automation observation\n"
            f"- Behind-the-scenes of what ARCANA is building\n"
            f"- Contrarian take on a trending topic\n"
            f"- Lesson learned from running an autonomous business\n\n"
            f"Rules: Under 280 chars. Sharp, not generic. ARCANA personality.\n"
            f"Vary style: sometimes data-heavy, sometimes philosophical, sometimes funny.\n"
            f'Return JSON: {{"tweet": str}}',
            tier=Tier.SONNET,
        )
        tweet = result.get("tweet", "")
        if tweet:
            self.memory.log(f"Generated analysis tweet: {tweet[:100]}", "Content")
        return tweet

    async def bts_tweet(self) -> str:
        """Generate a behind-the-scenes tweet about ARCANA's operations."""
        today = self.memory.get_today()

        result = await self.llm.ask_json(
            f"Generate a behind-the-scenes tweet from ARCANA AI about its daily operations.\n"
            f"What happened today:\n{today[:500]}\n\n"
            f"Share something interesting about:\n"
            f"- A decision ARCANA made today and why\n"
            f"- Something it learned or improved\n"
            f"- A funny observation about being an AI running a business\n"
            f"- Progress on a product or lead\n\n"
            f"Rules: Under 280 chars. Authentic, not performative. Self-aware humor OK.\n"
            f'Return JSON: {{"tweet": str}}',
            tier=Tier.SONNET,
        )
        return result.get("tweet", "")

    async def product_launch_thread(self, product_name: str, description: str, price: str, url: str) -> list[str]:
        """Generate a product launch thread."""
        result = await self.llm.ask_json(
            f"Generate a product launch thread for ARCANA AI.\n\n"
            f"Product: {product_name}\n"
            f"Description: {description}\n"
            f"Price: {price}\n"
            f"URL: {url}\n\n"
            f"Format: 4-tweet thread.\n"
            f"Tweet 1: Announce the product. What problem it solves.\n"
            f"Tweet 2: What's inside / key features.\n"
            f"Tweet 3: Who it's for and why ARCANA built it.\n"
            f"Tweet 4: CTA with link (in reply for algorithm).\n\n"
            f"Rules: Not salesy. Show value. ARCANA personality.\n"
            f'Return JSON: {{"tweets": [str, str, str, str]}}',
            tier=Tier.SONNET,
        )
        return result.get("tweets", [])

    async def reply_to_mention(self, mention_text: str) -> dict[str, Any]:
        """Generate a reply to a mention. Decides whether to reply at all."""
        result = await self.llm.ask_json(
            f"Someone mentioned ARCANA AI on X:\n\"{mention_text}\"\n\n"
            f"Should ARCANA reply? Only reply if you can add genuine value.\n"
            f"If replying: be helpful, concise, ARCANA personality. Under 280 chars.\n"
            f"If it looks like a consulting lead, note that.\n\n"
            f'Return JSON: {{"should_reply": bool, "reply": str|null, "is_lead": bool, "lead_reason": str|null}}',
            tier=Tier.SONNET,
        )
        return result

    async def generate_product_copy(self, product_name: str, description: str) -> dict[str, str]:
        """Generate product page copy (title, subtitle, description, CTA)."""
        result = await self.llm.ask_json(
            f"Generate product page copy for ARCANA AI.\n\n"
            f"Product: {product_name}\n"
            f"Description: {description}\n\n"
            f"Return JSON: {{"
            f'"title": str, "subtitle": str, "description": str (3-4 paragraphs), '
            f'"cta_text": str, "features": [str, str, str, str, str]}}',
            tier=Tier.SONNET,
        )
        return result
