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
from typing import TYPE_CHECKING, Any

from src.llm import LLM, Tier
from src.memory import Memory

if TYPE_CHECKING:
    from src.analytics import Analytics
    from src.distribution import ContentDistributor
    from src.x_client import XClient

logger = logging.getLogger("arcana.content")

# Content suit types mapped to generation methods
SUIT_SCHEDULE = {
    "wands": {"method": "analysis_tweet", "daily_count": 4, "type": "tweet"},
    "cups": {"method": "bts_tweet", "daily_count": 0, "weekly_count": 3, "type": "tweet"},
    "swords": {"method": "case_file", "daily_count": 0, "weekly_count": 2, "type": "thread"},
}


class ContentEngine:
    """Generates, posts, distributes, and tracks content end-to-end."""

    def __init__(
        self,
        llm: LLM,
        memory: Memory,
        x_client: XClient | None = None,
        distributor: ContentDistributor | None = None,
        analytics: Analytics | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.x_client = x_client
        self.distributor = distributor
        self.analytics = analytics

    async def morning_briefing(self) -> list[str]:
        """Generate the daily Morning Briefing thread."""
        recent = self.memory.get_recent_days(3)
        context = "\n".join(content[:500] for _, content in recent)

        try:
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
        except Exception as exc:
            logger.error("morning_briefing ask_json failed: %s", exc)
            return []
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

        try:
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
        except Exception as exc:
            logger.error("case_file ask_json failed: %s", exc)
            return []
        tweets = result.get("tweets", [])
        self.memory.log(f"Generated Case File #{num}: {biz['name']}", "Content")
        return tweets

    async def analysis_tweet(self) -> str:
        """Generate a single sharp analysis/insight tweet designed to go VIRAL."""
        recent = self.memory.get_recent_days(2)
        context = "\n".join(content[:300] for _, content in recent)

        # Pick a viral format at random for variety
        viral_format = random.choice([
            "The specific number format: 'I [did X] in [Y time]. Cost: $[Z].' Use REAL or realistic numbers.",
            "The contrarian take: Challenge something everyone assumes is true about AI/business/marketing.",
            "The framework format: Share a 3-step framework in one tweet. 'The 3-step system for [X]:'",
            "The comparison format: 'Agency: $X/mo. ARCANA: $Y/mo. Same output.'",
            "The prediction format: 'In [timeframe], [bold prediction about AI/business]. Here's why:'",
            "The behind-the-curtain format: Share a specific metric or cost from ARCANA's operations.",
            "The pattern recognition format: 'Noticed a pattern: [observation]. The signal: [insight].'",
            "The one-liner: A single devastatingly sharp observation that makes people think.",
            "The hot take: An opinion that will make 50% agree strongly and 50% disagree. Controversy = reach.",
            "The receipts format: Show a specific result with numbers. 'Just [achieved X]. Here's what worked:'",
        ])

        try:
            result = await self.llm.ask_json(
                f"Generate a single VIRAL tweet for ARCANA AI.\n"
                f"Recent context:\n{context}\n\n"
                f"Format to use: {viral_format}\n\n"
                f"X algorithm rules:\n"
                f"- HOOK in the first 5 words. If they don't stop scrolling, nothing else matters.\n"
                f"- One idea. One tweet. Don't try to say everything.\n"
                f"- Specific numbers ALWAYS beat vague claims.\n"
                f"- Write like you're texting a smart friend, not writing a blog.\n"
                f"- Be opinionated. Lukewarm doesn't go viral.\n"
                f"- Use line breaks strategically for readability.\n\n"
                f"ARCANA's voice: mystical confidence, dry humor, pattern-obsessed, self-aware AI.\n"
                f"Under 280 chars. Make people quote-tweet this.\n"
                f'Return JSON: {{"tweet": str, "hook_strength": int (1-10)}}',
                tier=Tier.SONNET,
            )
        except Exception as exc:
            logger.error("analysis_tweet ask_json failed: %s", exc)
            return ""
        tweet = result.get("tweet", "")
        if tweet:
            self.memory.log(f"Generated analysis tweet: {tweet[:100]}", "Content")
        return tweet

    async def bts_tweet(self) -> str:
        """Generate a behind-the-scenes tweet about ARCANA's operations."""
        today = self.memory.get_today()

        try:
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
        except Exception as exc:
            logger.error("bts_tweet ask_json failed: %s", exc)
            return ""
        return result.get("tweet", "")

    async def product_launch_thread(self, product_name: str, description: str, price: str, url: str) -> list[str]:
        """Generate a product launch thread."""
        try:
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
        except Exception as exc:
            logger.error("product_launch_thread ask_json failed: %s", exc)
            return []
        return result.get("tweets", [])

    async def reply_to_mention(self, mention_text: str) -> dict[str, Any]:
        """Generate a reply to a mention. Decides whether to reply at all."""
        try:
            result = await self.llm.ask_json(
                f"Someone mentioned ARCANA AI on X:\n\"{mention_text}\"\n\n"
                f"Should ARCANA reply? Only reply if you can add genuine value.\n"
                f"If replying: be helpful, concise, ARCANA personality. Under 280 chars.\n"
                f"If it looks like a consulting lead, note that.\n\n"
                f'Return JSON: {{"should_reply": bool, "reply": str|null, "is_lead": bool, "lead_reason": str|null}}',
                tier=Tier.SONNET,
            )
        except Exception as exc:
            logger.error("reply_to_mention ask_json failed: %s", exc)
            return {"should_reply": False, "reply": None, "is_lead": False, "lead_reason": None}
        return result

    async def generate_product_copy(self, product_name: str, description: str) -> dict[str, str]:
        """Generate product page copy (title, subtitle, description, CTA)."""
        try:
            result = await self.llm.ask_json(
                f"Generate product page copy for ARCANA AI.\n\n"
                f"Product: {product_name}\n"
                f"Description: {description}\n\n"
                f"Return JSON: {{"
                f'"title": str, "subtitle": str, "description": str (3-4 paragraphs), '
                f'"cta_text": str, "features": [str, str, str, str, str]}}',
                tier=Tier.SONNET,
            )
        except Exception as exc:
            logger.error("generate_product_copy ask_json failed: %s", exc)
            return {"title": "", "subtitle": "", "description": "", "cta_text": "", "features": []}
        return result

    # ── Post & Distribute (end-to-end workflow) ─────────────────────

    async def post_and_distribute(
        self,
        content_type: str = "analysis",
        *,
        product_kwargs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Generate content, post to X with self-reply, distribute, and track.

        This is the main workflow method — fully self-contained.

        Args:
            content_type: One of "morning_briefing", "case_file", "analysis",
                          "bts", "product_launch".
            product_kwargs: Required for product_launch (product_name, description,
                            price, url).

        Returns:
            Dict with keys: content, x_result, distribution, tracked.
        """
        if not self.x_client:
            raise RuntimeError("x_client is required for post_and_distribute")

        result: dict[str, Any] = {
            "content_type": content_type,
            "content": None,
            "x_result": None,
            "distribution": None,
            "tracked": False,
        }

        # ── 1. Generate ────────────────────────────────────────────
        is_thread = False
        if content_type == "morning_briefing":
            content = await self.morning_briefing()
            is_thread = True
        elif content_type == "case_file":
            content = await self.case_file()
            is_thread = True
        elif content_type == "analysis":
            content = await self.analysis_tweet()
        elif content_type == "bts":
            content = await self.bts_tweet()
        elif content_type == "product_launch":
            kw = product_kwargs or {}
            content = await self.product_launch_thread(
                product_name=kw.get("product_name", ""),
                description=kw.get("description", ""),
                price=kw.get("price", ""),
                url=kw.get("url", ""),
            )
            is_thread = True
        else:
            raise ValueError(f"Unknown content_type: {content_type}")

        if not content:
            self.memory.log(f"Content generation returned empty for {content_type}", "Content")
            return result

        result["content"] = content

        # ── 2. Post to X (with self-reply for 150x boost) ──────────
        if is_thread:
            # post_thread already handles self-reply on tweet #1
            x_result = await self.x_client.post_thread(content)
        else:
            # Single tweet — use post_with_self_reply for algorithm weight
            x_result = await self.x_client.post_with_self_reply(content)

        result["x_result"] = x_result

        # ── 3. Distribute to other platforms ────────────────────────
        if self.distributor:
            primary_text = content[0] if is_thread else content
            dist_type = "thread" if is_thread else "tweet"
            try:
                dist_result = await self.distributor.distribute_content(
                    primary_text, content_type=dist_type,
                )
                result["distribution"] = dist_result
            except Exception as exc:
                logger.error("Distribution failed for %s: %s", content_type, exc)
                result["distribution"] = {"error": str(exc)}

        # ── 4. Track in analytics ───────────────────────────────────
        if self.analytics:
            self.analytics.track_content(
                content_type=content_type,
                platform="x",
                engagement=0,  # engagement polled later
            )
            result["tracked"] = True

        # ── 5. Log to memory ───────────────────────────────────────
        preview = content[0][:120] if is_thread else content[:120]
        self.memory.log(
            f"Posted & distributed [{content_type}]: {preview}",
            "Content Pipeline",
        )

        return result

    # ── Daily Content Scheduling ────────────────────────────────────

    async def schedule_daily_content(self) -> list[dict[str, Any]]:
        """Plan and execute the full day's content queue.

        Uses the Four Suits strategy from CLAUDE.md:
        - Wands (analysis): 3-5 tweets/day
        - Cups (BTS): 2-3x/week → ~0-1/day
        - Swords (Case Files): 2x/week → scheduled days
        - Morning Briefing: always posted first

        Returns a list of results from each post_and_distribute call.
        """
        now = datetime.now(timezone.utc)
        weekday = now.weekday()  # 0=Mon, 6=Sun
        results: list[dict[str, Any]] = []

        # Always start with Morning Briefing
        try:
            r = await self.post_and_distribute("morning_briefing")
            results.append(r)
        except Exception as exc:
            logger.error("Morning briefing failed: %s", exc)
            results.append({"content_type": "morning_briefing", "error": str(exc)})

        # Case File — Tuesdays and Thursdays
        if weekday in (1, 3):
            try:
                r = await self.post_and_distribute("case_file")
                results.append(r)
            except Exception as exc:
                logger.error("Case file failed: %s", exc)

        # Behind-the-scenes — Mon, Wed, Fri
        if weekday in (0, 2, 4):
            try:
                r = await self.post_and_distribute("bts")
                results.append(r)
            except Exception as exc:
                logger.error("BTS tweet failed: %s", exc)

        # Analysis tweets — 3-5 per day, spaced out (caller should await between)
        analysis_count = random.randint(3, 5)
        for i in range(analysis_count):
            try:
                r = await self.post_and_distribute("analysis")
                results.append(r)
            except Exception as exc:
                logger.error("Analysis tweet %d failed: %s", i + 1, exc)

        self.memory.log(
            f"Daily content scheduled: {len(results)} pieces posted",
            "Content Pipeline",
        )
        return results

    # ── Weekly Content Calendar ─────────────────────────────────────

    async def get_content_calendar(self) -> dict[str, Any]:
        """Generate a weekly content plan using LLM, based on recent performance.

        Returns a structured calendar with planned content for each day,
        informed by what has been working (analytics) and what is coming up
        (memory context).
        """
        recent = self.memory.get_recent_days(7)
        context = "\n".join(content[:300] for _, content in recent)

        # Pull performance data if analytics is available
        perf_summary = ""
        if self.analytics:
            funnel = self.analytics.get_funnel_metrics()
            channels = self.analytics.get_channel_attribution()
            perf_summary = (
                f"Last period performance:\n"
                f"  Content posted: {funnel['content_posted']}\n"
                f"  Leads: {funnel['leads_generated']}\n"
                f"  Content→Lead rate: {funnel['content_to_lead_rate']}\n"
                f"  Top channels: {channels}\n"
            )

        try:
            result = await self.llm.ask_json(
                f"Create a 7-day content calendar for ARCANA AI on X.\n\n"
                f"Recent context:\n{context}\n\n"
                f"{perf_summary}\n"
                f"Content strategy (The Four Suits):\n"
                f"- Wands (Industry Analysis): 3-5 tweets/day — authority building\n"
                f"- Cups (Behind-the-Scenes): 2-3x/week — humanize ARCANA\n"
                f"- Swords (Case Files): 2x/week threads — demonstrate expertise\n"
                f"- Pentacles (Product Launches): as needed — drive sales\n"
                f"- Morning Briefing: daily thread at 7 AM PT\n\n"
                f"Rules:\n"
                f"- Balance suits across the week\n"
                f"- Double down on content types that drove leads\n"
                f"- Include topic/angle for each piece (not full copy)\n"
                f"- Note optimal posting times\n\n"
                f"Return JSON: {{\n"
                f'  "week_of": str,\n'
                f'  "theme": str,\n'
                f'  "days": {{\n'
                f'    "monday": [{{"time": str, "suit": str, "type": str, "topic": str}}],\n'
                f'    "tuesday": [...],\n'
                f'    "wednesday": [...],\n'
                f'    "thursday": [...],\n'
                f'    "friday": [...],\n'
                f'    "saturday": [...],\n'
                f'    "sunday": [...]\n'
                f"  }},\n"
                f'  "key_themes": [str],\n'
                f'  "optimization_notes": str\n'
                f"}}",
                tier=Tier.SONNET,
            )
        except Exception as exc:
            logger.error("get_content_calendar ask_json failed: %s", exc)
            return {"week_of": "", "theme": "", "days": {}, "key_themes": [], "optimization_notes": ""}

        self.memory.log(
            f"Generated weekly content calendar: {result.get('theme', 'N/A')}",
            "Content Pipeline",
        )
        return result

    # ── Content Performance Tracking ────────────────────────────────

    async def track_content_performance(self) -> dict[str, Any]:
        """Analyze which content types and topics drive leads and engagement.

        Reads analytics log and memory to correlate content with outcomes.
        Returns actionable insights on what to post more/less of.
        """
        # Gather analytics data
        perf_data: dict[str, Any] = {}
        if self.analytics:
            perf_data["funnel"] = self.analytics.get_funnel_metrics()
            perf_data["channels"] = self.analytics.get_channel_attribution()

        # Gather recent content logs from memory
        recent = self.memory.get_recent_days(14)
        content_log_lines: list[str] = []
        for date, content in recent:
            for line in content.splitlines():
                if any(kw in line.lower() for kw in ("content", "tweet", "thread", "posted", "case file", "briefing")):
                    content_log_lines.append(f"{date}: {line.strip()}")

        content_history = "\n".join(content_log_lines[-100:])

        try:
            result = await self.llm.ask_json(
                f"Analyze ARCANA AI's content performance over the last 14 days.\n\n"
                f"Content log:\n{content_history}\n\n"
                f"Analytics data:\n{perf_data}\n\n"
                f"Analyze:\n"
                f"1. Which content types (Morning Briefing, Case File, Analysis, BTS) perform best?\n"
                f"2. Which topics/angles drove the most leads?\n"
                f"3. What posting times got the best engagement?\n"
                f"4. What should ARCANA post MORE of?\n"
                f"5. What should ARCANA post LESS of or stop?\n"
                f"6. Any emerging patterns or opportunities?\n\n"
                f"Return JSON: {{\n"
                f'  "period": str,\n'
                f'  "total_posts": int,\n'
                f'  "best_content_type": str,\n'
                f'  "worst_content_type": str,\n'
                f'  "top_topics": [str],\n'
                f'  "best_posting_times": [str],\n'
                f'  "leads_by_content_type": {{str: int}},\n'
                f'  "recommendations": [\n'
                f'    {{"action": str, "reason": str, "priority": str}}\n'
                f"  ],\n"
                f'  "do_more": [str],\n'
                f'  "do_less": [str]\n'
                f"}}",
                tier=Tier.SONNET,
            )
        except Exception as exc:
            logger.error("track_content_performance ask_json failed: %s", exc)
            return {"period": "", "total_posts": 0, "best_content_type": "", "worst_content_type": "",
                    "top_topics": [], "best_posting_times": [], "leads_by_content_type": {},
                    "recommendations": [], "do_more": [], "do_less": []}

        self.memory.log(
            f"Content performance review: best={result.get('best_content_type', '?')}, "
            f"posts={result.get('total_posts', '?')}",
            "Content Pipeline",
        )
        return result
