"""ARCANA AI — Production-grade X/Twitter client.

Features:
- OAuth 1.0a via tweepy with X API v2
- Daily post limit enforcement (configurable, default 50)
- Character count validation (280 chars)
- Rate limit awareness with automatic backoff
- Self-reply for 150x algorithm boost
- Thread posting with error recovery (partial thread handling)
- Mention polling with cursor-based pagination
- Anti-bot jitter on all posting operations
- Dry run mode for testing
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Any

from src.config import Config, get_config
from src.memory import Memory
from src.retry import retry

logger = logging.getLogger("arcana.x")

MAX_TWEET_LENGTH = 280
DAILY_POST_LIMIT = 50
MENTION_POLL_INTERVAL = 60  # Minimum seconds between mention polls


class XClient:
    """Production-grade X/Twitter posting and monitoring."""

    def __init__(self, config: Config | None = None, memory: Memory | None = None) -> None:
        self.config = config or get_config()
        self.memory = memory or Memory()
        self._client = None
        self._me = None
        # Rate limiting
        self._daily_posts = 0
        self._daily_reset_date = ""
        self._max_daily = self.config.max_x_posts_per_day
        self._last_mention_poll = 0.0
        # Rate limit tracking from API responses
        self._rate_limit_remaining = 100
        self._rate_limit_reset = 0.0

    def _get_client(self):
        """Lazy-init tweepy client."""
        if self._client is None:
            if not self.config.x_api_key:
                logger.warning("X API keys not configured")
                return None
            import tweepy
            self._client = tweepy.Client(
                consumer_key=self.config.x_api_key,
                consumer_secret=self.config.x_api_secret,
                access_token=self.config.x_access_token,
                access_token_secret=self.config.x_access_secret,
                wait_on_rate_limit=True,
            )
        return self._client

    def _check_daily_limit(self) -> bool:
        """Check if we've hit the daily post limit."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_posts = 0
            self._daily_reset_date = today
        return self._daily_posts < self._max_daily

    def _validate_tweet(self, text: str) -> str:
        """Validate and clean tweet text."""
        text = text.strip()
        if not text:
            raise ValueError("Tweet text cannot be empty")
        if len(text) > MAX_TWEET_LENGTH:
            logger.warning("Tweet exceeds %d chars (%d), truncating", MAX_TWEET_LENGTH, len(text))
            text = text[:MAX_TWEET_LENGTH - 3] + "..."
        return text

    @retry(max_retries=2)
    async def post_tweet(self, text: str, reply_to: str | None = None) -> dict[str, Any]:
        """Post a tweet with validation and rate limiting."""
        text = self._validate_tweet(text)

        if self.config.dry_run:
            logger.info("DRY RUN tweet: %s", text[:80])
            self.memory.log(f"[DRY RUN] Tweet: {text[:200]}", "X Post")
            return {"id": "dry_run", "text": text}

        if not self._check_daily_limit():
            logger.warning("Daily post limit reached (%d/%d)", self._daily_posts, self._max_daily)
            self.memory.log(
                f"[X] Daily limit reached — tweet not posted: {text[:80]}", "X Rate Limit",
            )
            return {"id": None, "text": text, "error": "daily_limit_reached"}

        client = self._get_client()
        if client is None:
            return {"id": None, "text": text, "error": "not_configured"}

        try:
            resp = await asyncio.to_thread(client.create_tweet, text=text, in_reply_to_tweet_id=reply_to)
            tweet_id = str(resp.data["id"])
            self._daily_posts += 1

            logger.info("Posted tweet %s (%d/%d today): %s",
                        tweet_id, self._daily_posts, self._max_daily, text[:60])
            self.memory.log(f"Posted tweet {tweet_id}: {text[:200]}", "X Post")
            return {"id": tweet_id, "text": text}

        except Exception as exc:
            error_msg = str(exc)
            # Handle specific Twitter API errors
            if "429" in error_msg or "Too Many" in error_msg:
                logger.warning("X rate limited — waiting 60s")
                await asyncio.sleep(60)
                raise  # Let retry decorator handle it
            elif "403" in error_msg or "Forbidden" in error_msg:
                logger.error("X 403 Forbidden — check API permissions: %s", error_msg[:100])
                return {"id": None, "text": text, "error": "forbidden"}
            elif "duplicate" in error_msg.lower():
                logger.warning("Duplicate tweet detected — skipping")
                return {"id": None, "text": text, "error": "duplicate"}
            else:
                raise

    async def post_with_self_reply(self, text: str, follow_up: str | None = None) -> dict[str, Any]:
        """Post a tweet and self-reply for 150x algorithm boost."""
        result = await self.post_tweet(text)
        tweet_id = result.get("id")
        if not tweet_id or tweet_id == "dry_run" or result.get("error"):
            return result

        # Wait natural delay before self-reply (anti-bot)
        await asyncio.sleep(random.uniform(30, 120))

        reply_text = follow_up or "The pattern is the profit. | arcanaoperations.com"
        await self.post_tweet(reply_text, reply_to=tweet_id)
        return result

    async def post_thread(self, tweets: list[str]) -> list[dict[str, Any]]:
        """Post a thread with error recovery for partial failures."""
        if not tweets:
            return []

        results: list[dict[str, Any]] = []
        prev_id = None

        for i, tweet in enumerate(tweets):
            result = await self.post_tweet(tweet, reply_to=prev_id)
            results.append(result)

            new_id = result.get("id")
            if not new_id or result.get("error"):
                logger.error("Thread broken at tweet %d/%d — stopping", i + 1, len(tweets))
                break
            prev_id = new_id

            # Anti-bot jitter between tweets
            if i < len(tweets) - 1:
                await asyncio.sleep(random.uniform(2, 8))

        # Self-reply to first tweet for algorithm boost
        first_id = results[0].get("id") if results else None
        if first_id and first_id not in ("dry_run", None):
            await asyncio.sleep(random.uniform(30, 90))
            await self.post_tweet(
                "Follow for daily AI business intelligence. The pattern is the profit.",
                reply_to=first_id,
            )

        return results

    async def get_mentions(self, since_id: str | None = None) -> list[dict[str, Any]]:
        """Get recent mentions with rate limit protection."""
        # Enforce minimum poll interval
        now = time.monotonic()
        if now - self._last_mention_poll < MENTION_POLL_INTERVAL:
            return []
        self._last_mention_poll = now

        client = self._get_client()
        if client is None:
            return []

        try:
            if self._me is None:
                me = await asyncio.to_thread(client.get_me)
                self._me = me.data.id if me and me.data else None

            if not self._me:
                return []

            params: dict[str, Any] = {"max_results": 10}
            if since_id:
                params["since_id"] = since_id

            mentions = await asyncio.to_thread(client.get_users_mentions, self._me, **params)
            if not mentions or not mentions.data:
                return []

            results = [
                {
                    "id": str(m.id),
                    "text": m.text,
                    "author_id": str(m.author_id),
                }
                for m in mentions.data
            ]

            logger.info("Fetched %d mentions", len(results))
            return results

        except Exception as exc:
            error_msg = str(exc)
            if "429" in error_msg:
                logger.warning("Mentions rate limited — backing off")
                self._last_mention_poll = now + 300  # Back off 5 minutes
            else:
                logger.error("Get mentions failed: %s", exc)
            return []

    @retry()
    async def reply_to(self, tweet_id: str, text: str) -> dict[str, Any]:
        """Reply to a specific tweet."""
        return await self.post_tweet(text, reply_to=tweet_id)

    @retry()
    async def search_recent(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search recent tweets with error handling."""
        client = self._get_client()
        if client is None:
            return []

        try:
            results = await asyncio.to_thread(client.search_recent_tweets, query=query, max_results=max_results)
            if not results or not results.data:
                return []
            return [{"id": str(t.id), "text": t.text} for t in results.data]
        except Exception as exc:
            if "429" in str(exc):
                logger.warning("Search rate limited")
                await asyncio.sleep(30)
            raise

    def get_stats(self) -> dict[str, Any]:
        """Get posting stats."""
        return {
            "posts_today": self._daily_posts,
            "daily_limit": self._max_daily,
            "remaining": self._max_daily - self._daily_posts,
        }
