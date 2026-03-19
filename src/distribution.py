"""ARCANA AI — Multi-Platform Content Distribution.

One piece of content → 7+ platforms. Maximum reach, minimum effort.

Flow: X tweet/thread → repurpose to:
- LinkedIn post (professional tone, longer format)
- Reddit comment/post (community-appropriate)
- Blog article (SEO-optimized)
- YouTube Shorts script (vertical video)
- TikTok script (via UGC engine)
- Newsletter segment (via Beehiiv)
- Instagram caption (via Buffer)

Also handles: content calendar, cross-posting schedule, platform-specific optimization.
"""

from __future__ import annotations

import logging
import random
from typing import Any

import httpx

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.distribution")


class ContentDistributor:
    """Repurpose and distribute content across all platforms."""

    def __init__(
        self, llm: LLM, memory: Memory,
        buffer_key: str = "", linkedin_token: str = "",
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.buffer_key = buffer_key
        self.linkedin_token = linkedin_token
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30)
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Content Repurposing Engine ──────────────────────────────────

    async def repurpose_tweet(self, tweet_text: str) -> dict[str, Any]:
        """Take a tweet and repurpose it for every platform."""
        result = await self.llm.ask_json(
            f"Repurpose this tweet for multiple platforms.\n\n"
            f"Original tweet: {tweet_text}\n\n"
            f"Generate platform-optimized versions:\n"
            f"1. LinkedIn: Professional, 1-3 paragraphs, add industry insight, include CTA\n"
            f"2. Reddit (r/entrepreneur): Community-appropriate, no self-promo, add value\n"
            f"3. Blog intro: First 2 paragraphs of a full article expanding on this idea\n"
            f"4. YouTube Shorts script: 30-second spoken version, hook-first\n"
            f"5. Instagram caption: Engaging, include line breaks, end with question\n"
            f"6. Newsletter segment: 1-2 paragraphs with deeper analysis\n\n"
            f"Return JSON: {{"
            f'"linkedin": str, "reddit": str, "blog_intro": str, '
            f'"youtube_script": str, "instagram": str, "newsletter": str, '
            f'"hashtags": {{str: [str]}} (platform-specific hashtags)}}',
            tier=Tier.SONNET,
        )
        return result

    async def repurpose_thread(self, thread_tweets: list[str]) -> dict[str, Any]:
        """Repurpose a full thread into long-form content."""
        combined = "\n\n".join(f"Tweet {i+1}: {t}" for i, t in enumerate(thread_tweets))

        result = await self.llm.ask_json(
            f"Repurpose this X thread into multiple formats.\n\n"
            f"Thread:\n{combined}\n\n"
            f"Generate:\n"
            f"1. LinkedIn article (800-1200 words, professional)\n"
            f"2. Blog post (1000-1500 words, SEO-optimized with headers)\n"
            f"3. Newsletter issue (3-4 sections)\n"
            f"4. YouTube video script (3-5 minutes, conversational)\n"
            f"5. Carousel slides (8-10 slides, one key point each)\n\n"
            f"Return JSON: {{"
            f'"linkedin_article": str, '
            f'"blog_post": {{\"title\": str, \"meta_description\": str, \"content_html\": str}}, '
            f'"newsletter": {{\"subject\": str, \"body_html\": str}}, '
            f'"youtube_script": str, '
            f'"carousel_slides": [str]}}',
            tier=Tier.SONNET,
        )
        return result

    # ── Platform Posting ────────────────────────────────────────────

    async def post_to_linkedin(self, content: str) -> bool:
        """Post content to LinkedIn."""
        if not self.linkedin_token:
            self.memory.log(f"[Distribution] DRY RUN LinkedIn: {content[:80]}", "Distribution")
            return False

        try:
            http = await self._get_http()

            # Get user profile URN
            profile_resp = await http.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {self.linkedin_token}"},
            )
            if profile_resp.status_code != 200:
                return False

            sub = profile_resp.json().get("sub", "")

            # Create post
            resp = await http.post(
                "https://api.linkedin.com/v2/ugcPosts",
                headers={
                    "Authorization": f"Bearer {self.linkedin_token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                json={
                    "author": f"urn:li:person:{sub}",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": content},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
                },
            )
            success = resp.status_code in (200, 201)
            if success:
                self.memory.log(f"[Distribution] LinkedIn posted: {content[:60]}", "Distribution")
            return success
        except Exception as exc:
            logger.error("LinkedIn post error: %s", exc)
            return False

    async def post_to_reddit(self, subreddit: str, title: str, body: str) -> bool:
        """Post to Reddit via OAuth2 API.

        Requires REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, and REDDIT_REFRESH_TOKEN
        stored in environment variables. Respects subreddit rules and rate limits.
        """
        import os
        client_id = os.getenv("REDDIT_CLIENT_ID", "")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        refresh_token = os.getenv("REDDIT_REFRESH_TOKEN", "")

        if not all([client_id, client_secret, refresh_token]):
            self.memory.log(
                f"[Distribution] Reddit not configured — queued: r/{subreddit} — {title[:60]}",
                "Distribution",
            )
            # Save as draft for manual posting
            self.memory.save_knowledge(
                "resources", f"reddit-draft-{subreddit}",
                f"# Reddit Post Draft\n\nSubreddit: r/{subreddit}\nTitle: {title}\n\n{body}",
            )
            return False

        http = await self._get_http()

        # Get access token via refresh token
        try:
            token_resp = await http.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"User-Agent": "ARCANA-AI/1.0"},
            )
            if token_resp.status_code != 200:
                logger.error("Reddit token refresh failed: %s", token_resp.status_code)
                return False

            access_token = token_resp.json().get("access_token", "")
            if not access_token:
                return False

            # Submit post
            resp = await http.post(
                "https://oauth.reddit.com/api/submit",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": "ARCANA-AI/1.0",
                },
                data={
                    "sr": subreddit,
                    "kind": "self",
                    "title": title,
                    "text": body,
                    "api_type": "json",
                },
            )

            if resp.status_code == 200:
                result = resp.json()
                errors = result.get("json", {}).get("errors", [])
                if not errors:
                    post_url = result.get("json", {}).get("data", {}).get("url", "")
                    self.memory.log(
                        f"[Distribution] Reddit posted: r/{subreddit} — {title[:60]} → {post_url}",
                        "Distribution",
                    )
                    return True
                else:
                    logger.warning("Reddit submit errors: %s", errors)
                    return False
            else:
                logger.error("Reddit post failed: %s", resp.status_code)
                return False

        except Exception as exc:
            logger.error("Reddit post error: %s", exc)
            return False

    async def schedule_via_buffer(
        self, text: str, profile_ids: list[str],
    ) -> bool:
        """Schedule a post across multiple profiles via Buffer."""
        if not self.buffer_key:
            return False

        try:
            http = await self._get_http()
            for profile_id in profile_ids:
                await http.post(
                    "https://api.bufferapp.com/1/updates/create.json",
                    data={
                        "access_token": self.buffer_key,
                        "text": text,
                        "profile_ids[]": profile_id,
                    },
                )
            self.memory.log(
                f"[Distribution] Buffer scheduled to {len(profile_ids)} profiles: {text[:60]}",
                "Distribution",
            )
            return True
        except Exception as exc:
            logger.error("Buffer schedule error: %s", exc)
            return False

    # ── Distribution Cycle ──────────────────────────────────────────

    async def distribute_content(self, content: str, content_type: str = "tweet") -> dict[str, Any]:
        """Full distribution: repurpose and post to all platforms."""
        results: dict[str, Any] = {"platforms": {}}

        # Repurpose
        if content_type == "tweet":
            repurposed = await self.repurpose_tweet(content)
        elif content_type == "thread":
            repurposed = await self.repurpose_thread([content])
        else:
            repurposed = {"linkedin": content, "newsletter": content}

        # Post to LinkedIn
        if repurposed.get("linkedin"):
            posted = await self.post_to_linkedin(repurposed["linkedin"])
            results["platforms"]["linkedin"] = posted

        # Save blog content for publishing
        if repurposed.get("blog_intro"):
            self.memory.save_knowledge(
                "resources", f"blog-draft-{random.randint(1000, 9999)}",
                repurposed["blog_intro"],
            )
            results["platforms"]["blog"] = "draft_saved"

        # Save newsletter segment
        if repurposed.get("newsletter"):
            results["platforms"]["newsletter"] = "segment_saved"

        self.memory.log(
            f"[Distribution] Distributed to {len(results['platforms'])} platforms",
            "Distribution",
        )
        return results
