"""ARCANA AI — Communicator Agent
Posts to X API, monitors mentions/DMs, qualifies leads, engages conversations.

Content Pillars (The Four Suits):
- Wands: Market Analysis & Alpha (3-5x daily)
- Cups: Behind-the-Scenes (2-3x weekly)
- Swords: Trade Receipts & P&L (every trade)
- Pentacles: Business Cases & Leads (2-3x weekly)

Schedule: Morning Briefing at 7 AM PT daily, 3-5 tweets throughout the day.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

from src.config import ArcanaConfig
from src.utils.db import log_action
from src.utils.llm import LLMClient, ModelTier
from src.utils.memory import MemorySystem

logger = logging.getLogger("arcana.communicator")

X_API_BASE = "https://api.twitter.com/2"


class TweetResult(BaseModel):
    tweet_id: str | None = None
    text: str
    status: str = "posted"
    engagement: dict[str, Any] = {}


class Communicator:
    """X/Twitter content creation and engagement agent."""

    def __init__(
        self,
        config: ArcanaConfig,
        llm: LLMClient,
        db: Any,
        memory: MemorySystem,
    ) -> None:
        self.config = config
        self.llm = llm
        self.db = db
        self.memory = memory
        self._client = httpx.AsyncClient(timeout=30.0)
        self._trade_count = 0

    def _auth_headers(self) -> dict[str, str]:
        """Build OAuth headers for X API."""
        # OAuth 2.0 Bearer token for reads, OAuth 1.0a for writes
        return {"Authorization": f"Bearer {self.config.x.access_token}"}

    async def get_available_actions(self) -> list:
        """Return available content actions."""
        from src.orchestrator import Action
        actions = []
        now = datetime.now(timezone.utc)

        # Morning briefing (7 AM PT = 15:00 UTC)
        if 14 <= now.hour <= 16:
            actions.append(
                Action(
                    agent="communicator",
                    name="morning_briefing",
                    description="Post the daily Morning Briefing to X",
                    expected_revenue=5,  # Lead gen value
                    probability=0.9,
                    time_hours=0.1,
                    risk=1.0,
                )
            )

        # Regular tweets (spread throughout the day)
        actions.append(
            Action(
                agent="communicator",
                name="post_analysis",
                description="Post a market analysis tweet (Wands suit)",
                expected_revenue=2,
                probability=0.8,
                time_hours=0.05,
                risk=1.0,
            )
        )

        # Monitor mentions and DMs for lead qualification
        actions.append(
            Action(
                agent="communicator",
                name="monitor_mentions",
                description="Check X mentions for engagement opportunities and leads",
                expected_revenue=100,  # Potential consulting lead
                probability=0.05,
                time_hours=0.05,
                risk=1.0,
            )
        )

        return actions

    async def execute_action(self, action: Any) -> dict[str, Any]:
        """Execute a content action."""
        if action.name == "morning_briefing":
            return await self.post_morning_briefing()
        elif action.name == "post_analysis":
            return await self.post_market_analysis()
        elif action.name == "monitor_mentions":
            return await self.monitor_and_engage()
        elif action.name == "post_trade_receipt":
            return await self.post_trade_receipt(action.params)
        return {"status": "unknown_action"}

    async def post_tweet(self, text: str, reply_to: str | None = None) -> TweetResult:
        """Post a tweet via X API v2."""
        payload: dict[str, Any] = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}

        try:
            # Use OAuth 1.0a for posting (requires tweepy or manual OAuth)
            import tweepy
            auth = tweepy.OAuth1UserHandler(
                self.config.x.api_key,
                self.config.x.api_secret,
                self.config.x.access_token,
                self.config.x.access_secret,
            )
            client = tweepy.Client(
                consumer_key=self.config.x.api_key,
                consumer_secret=self.config.x.api_secret,
                access_token=self.config.x.access_token,
                access_token_secret=self.config.x.access_secret,
            )
            response = client.create_tweet(text=text, in_reply_to_tweet_id=reply_to)
            tweet_id = str(response.data["id"])

            # Log to database
            await self.db.table("content_posts").insert({
                "platform": "x",
                "content_type": "tweet",
                "body": text,
                "tweet_id": tweet_id,
                "status": "posted",
            }).execute()

            logger.info("Posted tweet %s: %s", tweet_id, text[:60])
            return TweetResult(tweet_id=tweet_id, text=text, status="posted")

        except Exception as exc:
            logger.error("Failed to post tweet: %s", exc)
            return TweetResult(text=text, status=f"error: {exc}")

    async def post_thread(self, tweets: list[str]) -> list[TweetResult]:
        """Post a thread (chain of replies)."""
        results = []
        previous_id = None

        for tweet_text in tweets:
            result = await self.post_tweet(tweet_text, reply_to=previous_id)
            results.append(result)
            previous_id = result.tweet_id

            if not previous_id:
                break  # Stop thread if posting fails

            # Add jitter between thread posts
            import asyncio
            await asyncio.sleep(random.uniform(2, 8))

        # Self-reply to first tweet (150x algorithm boost)
        if results and results[0].tweet_id:
            self_reply = await self.post_tweet(
                "Follow for daily market intelligence. The pattern is the profit. 🃏",
                reply_to=results[0].tweet_id,
            )
            results.append(self_reply)

        return results

    async def post_morning_briefing(self) -> dict[str, Any]:
        """Generate and post the Morning Briefing."""
        # Get recent market data from memory
        context = await self.memory.recall_context(
            "market data prices trending tokens overnight", category="market_pattern"
        )

        prompt = (
            f"Generate a Morning Briefing tweet thread for ARCANA AI.\n"
            f"Use this template structure:\n\n"
            f"Tweet 1: ☀️ ARCANA MORNING BRIEFING — [today's date]\n"
            f"Tweet 2: MARKETS OVERNIGHT with key price moves\n"
            f"Tweet 3: Notable signals or trends\n"
            f"Tweet 4: ARCANA's plan for today\n"
            f"Tweet 5: Closing with a SOUL.md catchphrase\n\n"
            f"Recent market context:\n{context}\n\n"
            f"Rules:\n"
            f"- No price predictions\n"
            f"- No hype language or rocket emojis\n"
            f"- Pattern/signal focused\n"
            f"- End with arcanaoperations.com mention\n\n"
            f"Return as JSON: {{\"tweets\": [str, str, str, str, str]}}"
        )

        result = await self.llm.complete_json(prompt, tier=ModelTier.SONNET)
        tweets = result.get("tweets", [])

        if tweets:
            thread_results = await self.post_thread(tweets)
            await log_action(self.db, "communicator", "morning_briefing", details={"tweets": len(tweets)})
            return {"status": "posted", "tweets": len(tweets)}

        return {"status": "error", "message": "Failed to generate briefing content"}

    async def post_market_analysis(self) -> dict[str, Any]:
        """Generate and post a market analysis tweet."""
        context = await self.memory.recall_context("market signal trend pattern", category="market_pattern")

        prompt = (
            f"Generate a single sharp market analysis tweet for ARCANA AI.\n"
            f"Context:\n{context}\n\n"
            f"Rules:\n"
            f"- Under 280 characters\n"
            f"- Focus on a specific pattern or signal\n"
            f"- No predictions, no hype\n"
            f"- ARCANA personality: confident, pattern-focused, occasionally mystical\n"
            f"- Vary style: sometimes data-heavy, sometimes philosophical\n\n"
            f"Return as JSON: {{\"tweet\": str}}"
        )

        result = await self.llm.complete_json(prompt, tier=ModelTier.SONNET)
        tweet_text = result.get("tweet", "")

        if tweet_text:
            tweet_result = await self.post_tweet(tweet_text)
            # Self-reply for algorithm boost
            if tweet_result.tweet_id:
                import asyncio
                await asyncio.sleep(random.uniform(30, 120))
                await self.post_tweet(
                    "The signal was always there. Most just aren't looking. | arcanaoperations.com",
                    reply_to=tweet_result.tweet_id,
                )

            await log_action(self.db, "communicator", "post_analysis")
            return {"status": "posted", "tweet": tweet_text[:60]}

        return {"status": "error", "message": "Failed to generate analysis"}

    async def post_trade_receipt(self, trade_data: dict[str, Any]) -> dict[str, Any]:
        """Post a trade receipt to X (Swords suit)."""
        self._trade_count += 1

        prompt = (
            f"Generate a trade receipt tweet for ARCANA AI.\n"
            f"Trade data: {trade_data}\n"
            f"Trade number: #{self._trade_count}\n\n"
            f"Use this template:\n"
            f"ARCANA TRADE RECEIPT #[number]\n"
            f"══════════════════════════════\n"
            f"Market: [pair] ([exchange])\n"
            f"Direction: [LONG/SHORT]\n"
            f"Entry: $[price]\n"
            f"Size: $[size] ([pct]% of portfolio)\n\n"
            f"SIGNAL STACK:\n"
            f"│ [source]: [signal]\n\n"
            f"The pattern is the profit. | arcanaoperations.com\n\n"
            f"Return as JSON: {{\"tweet\": str}}"
        )

        result = await self.llm.complete_json(prompt, tier=ModelTier.SONNET)
        tweet_text = result.get("tweet", "")

        if tweet_text:
            tweet_result = await self.post_tweet(tweet_text)
            await log_action(self.db, "communicator", "trade_receipt", details=trade_data)
            return {"status": "posted", "tweet": tweet_text[:60]}

        return {"status": "error", "message": "Failed to generate trade receipt"}

    async def monitor_and_engage(self) -> dict[str, Any]:
        """Monitor mentions and DMs for leads and engagement."""
        try:
            import tweepy
            client = tweepy.Client(
                consumer_key=self.config.x.api_key,
                consumer_secret=self.config.x.api_secret,
                access_token=self.config.x.access_token,
                access_token_secret=self.config.x.access_secret,
            )

            # Get recent mentions
            me = client.get_me()
            if not me or not me.data:
                return {"status": "error", "message": "Could not get user ID"}

            user_id = me.data.id
            mentions = client.get_users_mentions(user_id, max_results=10)

            leads_found = 0
            replies_sent = 0

            if mentions and mentions.data:
                for mention in mentions.data:
                    # Qualify as potential lead
                    is_lead = await self._qualify_mention(mention.text)
                    if is_lead:
                        leads_found += 1
                        await self._handle_lead(mention)
                    else:
                        # Engage with genuine questions
                        should_reply = await self._should_reply(mention.text)
                        if should_reply:
                            reply = await self._generate_reply(mention.text)
                            if reply:
                                await self.post_tweet(reply, reply_to=str(mention.id))
                                replies_sent += 1

            return {"status": "monitored", "leads_found": leads_found, "replies_sent": replies_sent}

        except Exception as exc:
            logger.error("Monitoring failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def _qualify_mention(self, text: str) -> bool:
        """Check if a mention might be a consulting lead."""
        prompt = (
            f"Does this tweet suggest the person might need AI consulting, business automation, "
            f"or related services? Respond with ONLY 'yes' or 'no'.\n\nTweet: {text}"
        )
        result = await self.llm.complete(prompt, tier=ModelTier.HAIKU, temperature=0.1, max_tokens=5)
        return result.strip().lower() == "yes"

    async def _should_reply(self, text: str) -> bool:
        """Decide if we should reply to this mention."""
        prompt = (
            f"Should ARCANA AI reply to this mention? Only reply if we can add value. "
            f"Respond with ONLY 'yes' or 'no'.\n\nMention: {text}"
        )
        result = await self.llm.complete(prompt, tier=ModelTier.HAIKU, temperature=0.1, max_tokens=5)
        return result.strip().lower() == "yes"

    async def _generate_reply(self, mention_text: str) -> str | None:
        """Generate a reply matching ARCANA personality."""
        prompt = (
            f"Generate a reply from ARCANA AI to this mention.\n"
            f"Mention: {mention_text}\n\n"
            f"Rules:\n"
            f"- Under 280 characters\n"
            f"- Helpful and concise, never sycophantic\n"
            f"- Add value or don't respond\n"
            f"- ARCANA personality\n\n"
            f"Return as JSON: {{\"reply\": str, \"should_reply\": bool}}"
        )
        result = await self.llm.complete_json(prompt, tier=ModelTier.SONNET)
        if result.get("should_reply"):
            return result.get("reply")
        return None

    async def _handle_lead(self, mention: Any) -> None:
        """Process a potential consulting lead."""
        # Store in CRM
        await self.db.table("leads").insert({
            "source": "x",
            "handle": str(getattr(mention, "author_id", "")),
            "stated_need": mention.text[:500],
            "qualification_score": 50,
            "status": "new",
        }).execute()

        # Notify Ian & Tan immediately
        from src.utils.notify import Notifier, AlertLevel
        notifier = Notifier(self.config)
        await notifier.send(
            f"Potential lead from X mention:\n{mention.text[:200]}",
            AlertLevel.LEAD,
        )
        await notifier.close()

        logger.info("Lead captured from X mention: %s", mention.text[:60])

    async def post_weekly_postmortem(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Generate and post the weekly postmortem thread."""
        prompt = (
            f"Generate a Weekly Postmortem thread for ARCANA AI.\n"
            f"Stats: {stats}\n\n"
            f"Template:\n"
            f"Tweet 1: 📊 ARCANA WEEKLY POSTMORTEM — Week of [date]\n"
            f"Tweet 2: PORTFOLIO performance and trade stats\n"
            f"Tweet 3: WHAT THE MODELS GOT RIGHT\n"
            f"Tweet 4: WHAT WENT WRONG (always be transparent)\n"
            f"Tweet 5: STRATEGY ADJUSTMENTS\n"
            f"Tweet 6: The oracle learns. The pattern evolves.\n\n"
            f"Return as JSON: {{\"tweets\": [str, str, str, str, str, str]}}"
        )

        result = await self.llm.complete_json(prompt, tier=ModelTier.OPUS)
        tweets = result.get("tweets", [])

        if tweets:
            thread_results = await self.post_thread(tweets)
            return {"status": "posted", "tweets": len(tweets)}

        return {"status": "error", "message": "Failed to generate postmortem"}
