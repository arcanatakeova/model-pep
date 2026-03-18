"""ARCANA AI — Lead qualification pipeline.

X engagement → qualify with LLM → score → route to Ian/Tan → Discord/Telegram alert.
Consulting leads ALWAYS highest priority. A $5K contract in 15 min beats everything.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory
from src.notify import Notifier

logger = logging.getLogger("arcana.leads")


class LeadPipeline:
    """Qualify and route consulting leads from X and other sources."""

    def __init__(self, llm: LLM, memory: Memory, notifier: Notifier) -> None:
        self.llm = llm
        self.memory = memory
        self.notifier = notifier

    async def qualify(self, handle: str, text: str, source: str = "x_mention") -> dict[str, Any]:
        """Score and qualify a potential consulting lead."""
        result = await self.llm.ask_json(
            f"Qualify this potential consulting lead for Arcana Operations.\n\n"
            f"Handle: @{handle}\n"
            f"Source: {source}\n"
            f"What they said: {text}\n\n"
            f"Arcana Operations services:\n"
            f"- AI agent development ($3-10K/mo)\n"
            f"- Business strategy ($2-8K/mo)\n"
            f"- SEO ($1.5-5K/mo)\n"
            f"- Marketing ($2-6K/mo)\n"
            f"- Fulfillment automation ($1-4K/mo)\n"
            f"- Operational management ($2-6K/mo)\n\n"
            f"Score 0-100 based on: budget signals, urgency, service fit, conversion likelihood.\n"
            f"Return JSON: {{"
            f'"score": int, '
            f'"service_fit": str, '
            f'"estimated_value_monthly": int, '
            f'"priority": "hot"|"warm"|"cold", '
            f'"suggested_reply": str, '
            f'"route_to": "ian"|"tan"|"none", '
            f'"reasoning": str}}',
            tier=Tier.SONNET,
        )

        score = result.get("score", 0)
        priority = result.get("priority", "cold")

        # Log to memory
        self.memory.log(
            f"Lead: @{handle} (score: {score}, {priority}) — {text[:100]}\n"
            f"Service fit: {result.get('service_fit', 'N/A')}\n"
            f"Est. value: ${result.get('estimated_value_monthly', 0)}/mo\n"
            f"Route: {result.get('route_to', 'none')}",
            "Leads",
        )

        # Save to knowledge graph if qualified
        if score >= 40:
            self.memory.save_knowledge(
                "projects" if score >= 60 else "resources",
                f"lead-{handle}",
                f"# Lead: @{handle}\n\n"
                f"- Source: {source}\n"
                f"- Score: {score}/100 ({priority})\n"
                f"- Service: {result.get('service_fit', 'TBD')}\n"
                f"- Est. value: ${result.get('estimated_value_monthly', 0)}/mo\n"
                f"- Original message: {text[:300]}\n"
                f"- Suggested reply: {result.get('suggested_reply', '')}\n"
                f"- Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n",
            )

        # Alert Ian/Tan immediately for hot/warm leads
        if score >= 40:
            await self.notifier.lead_alert(handle, text[:100], score)

        return result

    async def check_mention_for_lead(self, mention_text: str) -> bool:
        """Quick check: does this mention look like a potential lead?"""
        result = await self.llm.ask(
            f"Does this X mention suggest the person might need AI consulting, "
            f"business automation, marketing help, or related services?\n\n"
            f'Mention: "{mention_text}"\n\n'
            f"Respond with ONLY 'yes' or 'no'.",
            tier=Tier.HAIKU,
            temperature=0.1,
            max_tokens=5,
        )
        return result.strip().lower() == "yes"

    async def process_mentions(self, mentions: list[dict[str, Any]]) -> dict[str, Any]:
        """Process a batch of X mentions for leads and engagement."""
        leads_found = 0
        qualified = []

        for mention in mentions:
            text = mention.get("text", "")
            author = mention.get("author_id", "unknown")

            is_lead = await self.check_mention_for_lead(text)
            if is_lead:
                leads_found += 1
                result = await self.qualify(author, text, "x_mention")
                if result.get("score", 0) >= 40:
                    qualified.append({
                        "handle": author,
                        "score": result["score"],
                        "priority": result.get("priority"),
                        "suggested_reply": result.get("suggested_reply"),
                    })

        return {
            "mentions_processed": len(mentions),
            "leads_found": leads_found,
            "qualified": qualified,
        }

    def get_open_leads(self) -> list[str]:
        """Get all open leads from knowledge graph."""
        return self.memory.list_knowledge("projects")

    def get_lead_details(self, handle: str) -> str:
        """Get details for a specific lead."""
        return self.memory.get_knowledge("projects", f"lead-{handle}")
