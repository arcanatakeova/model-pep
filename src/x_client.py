"""ARCANA AI — X/Twitter client.

Posts tweets, threads, replies to mentions, monitors for leads.
Uses tweepy for OAuth 1.0a with X API v2.

Algorithm rules:
- Self-reply = 150x like weight (ALWAYS self-reply)
- No external links in main tweet (put in reply)
- Add random jitter to posting intervals (anti-bot)
- Replies are autonomous; original posts autonomous after training period
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from src.config import Config, get_config
from src.memory import Memory

logger = logging.getLogger("arcana.x")


class XClient:
    """X/Twitter posting and monitoring via tweepy."""

    def __init__(self, config: Config | None = None, memory: Memory | None = None) -> None:
        self.config = config or get_config()
        self.memory = memory or Memory()
        self._client = None
        self._me = None

    def _get_client(self):
        """Lazy-init tweepy client."""
        if self._client is None:
            import tweepy
            self._client = tweepy.Client(
                consumer_key=self.config.x_api_key,
                consumer_secret=self.config.x_api_secret,
                access_token=self.config.x_access_token,
                access_token_secret=self.config.x_access_secret,
            )
        return self._client

    async def post_tweet(self, text: str, reply_to: str | None = None) -> dict[str, Any]:
        """Post a tweet. Returns {"id": str, "text": str} or {"error": str}."""
        if self.config.dry_run:
            logger.info("DRY RUN tweet: %s", text[:80])
            self.memory.log(f"[DRY RUN] Tweet: {text[:200]}", "X Post")
            return {"id": "dry_run", "text": text}

        try:
            client = self._get_client()
            resp = client.create_tweet(text=text, in_reply_to_tweet_id=reply_to)
            tweet_id = str(resp.data["id"])
            logger.info("Posted tweet %s: %s", tweet_id, text[:60])
            self.memory.log(f"Posted tweet {tweet_id}: {text[:200]}", "X Post")
            return {"id": tweet_id, "text": text}
        except Exception as exc:
            logger.error("Tweet failed: %s", exc)
            return {"error": str(exc)}

    async def post_with_self_reply(self, text: str, follow_up: str | None = None) -> dict[str, Any]:
        """Post a tweet and self-reply for 150x algorithm boost."""
        result = await self.post_tweet(text)
        if "error" in result:
            return result

        # Wait natural delay before self-reply
        await asyncio.sleep(random.uniform(30, 120))

        reply_text = follow_up or "The pattern is the profit. | arcanaoperations.com"
        await self.post_tweet(reply_text, reply_to=result["id"])
        return result

    async def post_thread(self, tweets: list[str]) -> list[dict[str, Any]]:
        """Post a thread (chained replies). Self-reply to first tweet."""
        results = []
        prev_id = None

        for tweet in tweets:
            result = await self.post_tweet(tweet, reply_to=prev_id)
            results.append(result)
            prev_id = result.get("id")
            if not prev_id or "error" in result:
                break
            await asyncio.sleep(random.uniform(2, 8))

        # Self-reply to first tweet for algorithm boost
        if results and results[0].get("id") and results[0]["id"] != "dry_run":
            await asyncio.sleep(random.uniform(30, 90))
            await self.post_tweet(
                "Follow for daily AI business intelligence. The pattern is the profit. 🃏",
                reply_to=results[0]["id"],
            )

        return results

    async def get_mentions(self, since_id: str | None = None) -> list[dict[str, Any]]:
        """Get recent mentions."""
        try:
            client = self._get_client()
            if self._me is None:
                me = client.get_me()
                self._me = me.data.id if me and me.data else None

            if not self._me:
                return []

            params = {"max_results": 10}
            if since_id:
                params["since_id"] = since_id

            mentions = client.get_users_mentions(self._me, **params)
            if not mentions or not mentions.data:
                return []

            return [
                {"id": str(m.id), "text": m.text, "author_id": str(m.author_id)}
                for m in mentions.data
            ]
        except Exception as exc:
            logger.error("Get mentions failed: %s", exc)
            return []

    async def reply_to(self, tweet_id: str, text: str) -> dict[str, Any]:
        """Reply to a specific tweet."""
        return await self.post_tweet(text, reply_to=tweet_id)

    async def search_recent(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search recent tweets."""
        try:
            client = self._get_client()
            results = client.search_recent_tweets(query=query, max_results=max_results)
            if not results or not results.data:
                return []
            return [{"id": str(t.id), "text": t.text} for t in results.data]
        except Exception as exc:
            logger.error("Search failed: %s", exc)
            return []
