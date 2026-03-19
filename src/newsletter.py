"""ARCANA AI — Newsletter Engine (Beehiiv).

Builds email audience from X followers. Free tier grows the list,
premium tier ($9-19/mo) for advanced content. Sponsors at 5K+ subs.

Revenue: $200-2K per sponsor placement. At 10K subs, $2-5K/placement.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.llm import LLM, Tier
from src.memory import Memory
from src.retry import retry

logger = logging.getLogger("arcana.newsletter")


class Newsletter:
    """Beehiiv newsletter management and automation."""

    def __init__(self, llm: LLM, memory: Memory, api_key: str, publication_id: str = "") -> None:
        self.llm = llm
        self.memory = memory
        self.api_key = api_key
        self.publication_id = publication_id
        self.base_url = "https://api.beehiiv.com/v2"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry()
    async def get_stats(self) -> dict[str, Any]:
        """Get newsletter subscriber stats."""
        if not self.api_key or not self.publication_id:
            return {"subscribers": 0, "active": False}
        client = await self._get_client()
        resp = await client.get(
            f"{self.base_url}/publications/{self.publication_id}",
        )
        resp.raise_for_status()
        if 200 <= resp.status_code < 300:
            data = resp.json().get("data", {})
            stats = data.get("stats", {})
            return {
                "subscribers": stats.get("active_subscriptions", 0),
                "total": stats.get("total_subscriptions", 0),
                "open_rate": stats.get("average_open_rate", 0),
                "click_rate": stats.get("average_click_rate", 0),
                "active": True,
            }
        return {"subscribers": 0, "active": False}

    @retry()
    async def create_post(self, subject: str, content_html: str, status: str = "draft") -> dict[str, Any] | None:
        """Create a newsletter post."""
        if not self.api_key or not self.publication_id:
            logger.warning("Beehiiv not configured, skipping post creation")
            return None
        client = await self._get_client()
        resp = await client.post(
            f"{self.base_url}/publications/{self.publication_id}/posts",
            json={
                "post": {
                    "title": subject,
                    "subtitle": "",
                    "status": status,  # draft, confirmed, archived
                    "content_html": content_html,
                }
            },
        )
        resp.raise_for_status()
        if 200 <= resp.status_code < 300:
            post = resp.json().get("data", {})
            self.memory.log(f"Newsletter post created: {subject} ({status})", "Newsletter")
            return post
        logger.error("Beehiiv create post failed: %s %s", resp.status_code, resp.text[:200])
        return None

    @retry()
    async def publish_post(self, post_id: str) -> dict[str, Any] | None:
        """Publish a draft post by transitioning its status to 'confirmed'.

        Beehiiv sends the newsletter to all subscribers once status is confirmed.
        """
        if not self.api_key or not self.publication_id:
            logger.warning("Beehiiv not configured, skipping publish")
            return None
        client = await self._get_client()
        resp = await client.put(
            f"{self.base_url}/publications/{self.publication_id}/posts/{post_id}",
            json={"post": {"status": "confirmed"}},
        )
        resp.raise_for_status()
        if 200 <= resp.status_code < 300:
            post = resp.json().get("data", {})
            self.memory.log(f"Newsletter published: {post_id}", "Newsletter")
            return post
        logger.error(
            "Beehiiv publish failed: %s %s", resp.status_code, resp.text[:200]
        )
        return None

    @retry()
    async def schedule_send(self, post_id: str, send_at: datetime) -> dict[str, Any] | None:
        """Schedule a draft post to be sent at a specific time.

        Args:
            post_id: The Beehiiv post ID to schedule.
            send_at: UTC datetime for when the newsletter should be sent.
        """
        if not self.api_key or not self.publication_id:
            logger.warning("Beehiiv not configured, skipping schedule")
            return None
        # Beehiiv expects ISO 8601 UTC timestamp for scheduled sends
        scheduled_at = send_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        client = await self._get_client()
        resp = await client.put(
            f"{self.base_url}/publications/{self.publication_id}/posts/{post_id}",
            json={
                "post": {
                    "status": "confirmed",
                    "scheduled_at": scheduled_at,
                }
            },
        )
        resp.raise_for_status()
        if 200 <= resp.status_code < 300:
            post = resp.json().get("data", {})
            self.memory.log(
                f"Newsletter scheduled: {post_id} for {scheduled_at}", "Newsletter"
            )
            return post
        logger.error(
            "Beehiiv schedule failed: %s %s", resp.status_code, resp.text[:200]
        )
        return None

    async def send_newsletter(self, subject: str, content_html: str) -> dict[str, Any]:
        """Full flow: generate a post, create it as draft, publish it, and track.

        Returns a summary dict with post_id, status, and subscriber count.
        """
        stats = await self.get_stats()
        sub_count = stats.get("subscribers", 0)

        # Create the draft
        post = await self.create_post(subject, content_html, status="draft")
        if not post or not post.get("id"):
            self.memory.log(
                f"Newsletter send FAILED at draft stage: {subject}", "Newsletter"
            )
            return {"status": "failed", "stage": "create", "subject": subject}

        post_id = post["id"]

        # Publish (sends to all subscribers)
        published = await self.publish_post(post_id)
        if not published:
            self.memory.log(
                f"Newsletter send FAILED at publish stage: {post_id}", "Newsletter"
            )
            return {
                "status": "failed",
                "stage": "publish",
                "post_id": post_id,
                "subject": subject,
            }

        self.memory.log(
            f"Newsletter SENT to {sub_count} subscribers: {subject}", "Newsletter"
        )
        return {
            "status": "sent",
            "post_id": post_id,
            "subject": subject,
            "subscribers": sub_count,
        }

    async def generate_weekly_issue(self) -> dict[str, Any]:
        """Generate a weekly newsletter issue from the week's content."""
        # Get recent daily notes for content
        recent = self.memory.get_recent_days(7)
        content_summary = "\n".join(
            f"### {date}\n{notes[:300]}" for date, notes in recent[:7]
        )

        try:
            result = await self.llm.ask_json(
                f"Generate a weekly newsletter issue for ARCANA AI's audience.\n\n"
                f"This week's activity:\n{content_summary}\n\n"
                f"The newsletter covers: AI business automation, autonomous agents, "
                f"real case studies from Arcana Operations, and actionable insights.\n\n"
                f"Format requirements:\n"
                f"- Subject line (compelling, under 60 chars)\n"
                f"- 3-4 sections with headers\n"
                f"- Each section: 2-3 paragraphs\n"
                f"- Include one CTA for Arcana Operations consulting\n"
                f"- Include one product mention (guide, template, etc.)\n"
                f"- ARCANA's voice: insightful, pattern-focused, no hype\n\n"
                f"Return JSON: {{"
                f'"subject": str, '
                f'"preview_text": str (under 100 chars), '
                f'"sections": [{{"title": str, "content_html": str}}], '
                f'"cta_text": str, '
                f'"cta_url": str}}',
                tier=Tier.SONNET,
            )
        except Exception as exc:
            logger.error("generate_weekly_issue ask_json failed: %s", exc)
            return {"subject": None, "preview": None, "sections": 0, "post_id": None, "published": False}

        # Assemble HTML
        html_parts = []
        import html as _html
        for section in result.get("sections", []):
            title = _html.escape(section.get('title', ''))
            content_html = section.get('content_html', '')
            html_parts.append(f"<h2>{title}</h2>\n{content_html}")

        if result.get("cta_text"):
            html_parts.append(
                f'<hr><p><strong>{result["cta_text"]}</strong></p>'
                f'<p><a href="{result.get("cta_url", "https://arcanaoperations.com")}">'
                f"Book a consultation →</a></p>"
            )

        full_html = "\n\n".join(html_parts)

        # Create draft in Beehiiv
        subject = result.get("subject", "ARCANA Weekly")
        post = await self.create_post(subject, full_html, "draft")

        post_id = post.get("id") if post else None
        published = False

        # Publish the draft so it actually sends
        if post_id:
            pub_result = await self.publish_post(post_id)
            published = pub_result is not None

        self.memory.log(
            f"Weekly newsletter {'sent' if published else 'generated (not sent)'}: "
            f"{result.get('subject', 'N/A')}",
            "Newsletter",
        )

        return {
            "subject": result.get("subject"),
            "preview": result.get("preview_text"),
            "sections": len(result.get("sections", [])),
            "post_id": post_id,
            "published": published,
        }

    @retry()
    async def get_subscriber_growth(self) -> list[dict[str, Any]]:
        """Track subscriber count over time.

        Fetches current subscriber data and logs a snapshot to memory
        for historical trend analysis.
        """
        if not self.api_key or not self.publication_id:
            return []
        # Fetch current subscription stats
        client = await self._get_client()
        resp = await client.get(
            f"{self.base_url}/publications/{self.publication_id}/subscriptions",
            params={"limit": 100, "status": "active"},
        )
        resp.raise_for_status()
        if not (200 <= resp.status_code < 300):
            logger.error("Beehiiv subscriber growth fetch failed: %s", resp.status_code)
            return []

        # Get current stats as a snapshot
        stats = await self.get_stats()
        snapshot = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "active_subscribers": stats.get("subscribers", 0),
            "total_subscribers": stats.get("total", 0),
        }

        # Log to memory for historical tracking
        self.memory.log(
            f"Subscriber snapshot: {snapshot['active_subscribers']} active, "
            f"{snapshot['total_subscribers']} total",
            "Newsletter",
        )
        return [snapshot]

    @retry()
    async def get_newsletter_performance(self, limit: int = 10) -> dict[str, Any]:
        """Get performance metrics for recent newsletters.

        Returns aggregate and per-issue stats including open rates,
        click rates, and delivery counts.

        Args:
            limit: Max number of recent issues to analyze.
        """
        if not self.api_key or not self.publication_id:
            return {"issues": [], "aggregate": {}}
        client = await self._get_client()
        resp = await client.get(
            f"{self.base_url}/publications/{self.publication_id}/posts",
            params={"limit": limit, "status": "confirmed"},
        )
        resp.raise_for_status()
        if not (200 <= resp.status_code < 300):
            logger.error("Beehiiv performance fetch failed: %s", resp.status_code)
            return {"issues": [], "aggregate": {}}

        posts = resp.json().get("data", [])
        issues: list[dict[str, Any]] = []
        total_opens = 0
        total_clicks = 0
        total_delivered = 0

        for post in posts:
            post_stats = post.get("stats", {})
            delivered = post_stats.get("email_total_delivered", 0)
            opens = post_stats.get("email_total_unique_opened", 0)
            clicks = post_stats.get("email_total_unique_clicked", 0)
            open_rate = (opens / delivered * 100) if delivered > 0 else 0
            click_rate = (clicks / delivered * 100) if delivered > 0 else 0

            issues.append({
                "id": post.get("id"),
                "title": post.get("title", ""),
                "published_at": post.get("published_at"),
                "delivered": delivered,
                "opens": opens,
                "clicks": clicks,
                "open_rate": round(open_rate, 1),
                "click_rate": round(click_rate, 1),
            })

            total_opens += opens
            total_clicks += clicks
            total_delivered += delivered

        avg_open_rate = (total_opens / total_delivered * 100) if total_delivered > 0 else 0
        avg_click_rate = (total_clicks / total_delivered * 100) if total_delivered > 0 else 0

        aggregate = {
            "total_issues": len(issues),
            "total_delivered": total_delivered,
            "avg_open_rate": round(avg_open_rate, 1),
            "avg_click_rate": round(avg_click_rate, 1),
        }

        self.memory.log(
            f"Newsletter performance: {len(issues)} issues, "
            f"{avg_open_rate:.1f}% open rate, {avg_click_rate:.1f}% click rate",
            "Newsletter",
        )
        return {"issues": issues, "aggregate": aggregate}

    async def generate_x_to_newsletter_cta(self) -> str:
        """Generate a tweet promoting newsletter signup."""
        stats = await self.get_stats()
        subs = stats.get("subscribers", 0)

        cta = await self.llm.ask(
            f"Write a tweet promoting the ARCANA AI newsletter.\n\n"
            f"Current subscribers: {subs}\n"
            f"Newsletter covers: AI business automation, agent architecture, case studies\n"
            f"Free to subscribe.\n\n"
            f"Rules: Under 280 chars, ARCANA voice, no hype. Include a subtle flex.\n"
            f"Don't include a URL (that goes in the reply).",
            tier=Tier.HAIKU,
            max_tokens=100,
        )
        return cta.strip()
