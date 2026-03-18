"""ARCANA AI — Newsletter Engine (Beehiiv).

Builds email audience from X followers. Free tier grows the list,
premium tier ($9-19/mo) for advanced content. Sponsors at 5K+ subs.

Revenue: $200-2K per sponsor placement. At 10K subs, $2-5K/placement.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.llm import LLM, Tier
from src.memory import Memory

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

    async def get_stats(self) -> dict[str, Any]:
        """Get newsletter subscriber stats."""
        if not self.api_key or not self.publication_id:
            return {"subscribers": 0, "active": False}
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/publications/{self.publication_id}"
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                stats = data.get("stats", {})
                return {
                    "subscribers": stats.get("active_subscriptions", 0),
                    "total": stats.get("total_subscriptions", 0),
                    "open_rate": stats.get("average_open_rate", 0),
                    "click_rate": stats.get("average_click_rate", 0),
                    "active": True,
                }
        except Exception as exc:
            logger.error("Beehiiv stats error: %s", exc)
        return {"subscribers": 0, "active": False}

    async def create_post(self, subject: str, content_html: str, status: str = "draft") -> dict[str, Any] | None:
        """Create a newsletter post."""
        if not self.api_key or not self.publication_id:
            logger.warning("Beehiiv not configured, skipping post creation")
            return None
        try:
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
            if resp.status_code in (200, 201):
                post = resp.json().get("data", {})
                self.memory.log(f"Newsletter post created: {subject} ({status})", "Newsletter")
                return post
            logger.error("Beehiiv create post failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("Beehiiv create post error: %s", exc)
        return None

    async def generate_weekly_issue(self) -> dict[str, Any]:
        """Generate a weekly newsletter issue from the week's content."""
        # Get recent daily notes for content
        recent = self.memory.get_recent_days(7)
        content_summary = "\n".join(
            f"### {date}\n{notes[:300]}" for date, notes in recent[:7]
        )

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

        # Assemble HTML
        html_parts = []
        for section in result.get("sections", []):
            html_parts.append(f"<h2>{section['title']}</h2>\n{section['content_html']}")

        if result.get("cta_text"):
            html_parts.append(
                f'<hr><p><strong>{result["cta_text"]}</strong></p>'
                f'<p><a href="{result.get("cta_url", "https://arcanaoperations.com")}">'
                f"Book a consultation →</a></p>"
            )

        full_html = "\n\n".join(html_parts)

        # Create draft in Beehiiv
        post = await self.create_post(result.get("subject", "ARCANA Weekly"), full_html, "draft")

        self.memory.log(
            f"Weekly newsletter generated: {result.get('subject', 'N/A')}",
            "Newsletter",
        )

        return {
            "subject": result.get("subject"),
            "preview": result.get("preview_text"),
            "sections": len(result.get("sections", [])),
            "post_id": post.get("id") if post else None,
        }

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
