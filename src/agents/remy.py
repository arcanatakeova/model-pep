"""ARCANA AI — Remy: Sales Sub-Agent.

Handles:
- Inbound lead follow-up
- Sales conversations via email/DM
- Proposal generation for Arcana Operations services
- Lead nurturing and pipeline management

Reports to ARCANA nightly. Hot leads escalated to Ian/Tan immediately.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.remy")


class Remy:
    """Sales and lead management sub-agent."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    async def follow_up(self, handle: str, context: str) -> dict[str, Any]:
        """Generate a follow-up message for a qualified lead."""
        lead_info = self.memory.get_knowledge("projects", f"lead-{handle}")

        result = await self.llm.ask_json(
            f"You are Remy, the sales agent for Arcana Operations.\n"
            f"Generate a follow-up message for this lead.\n\n"
            f"Lead: @{handle}\n"
            f"Context: {context}\n"
            f"Lead file: {lead_info[:500] if lead_info else 'New lead'}\n\n"
            f"Arcana Operations services:\n"
            f"- AI agent development ($3-10K/mo)\n"
            f"- Business strategy ($2-8K/mo)\n"
            f"- SEO ($1.5-5K/mo)\n"
            f"- Marketing ($2-6K/mo)\n"
            f"- Fulfillment automation ($1-4K/mo)\n\n"
            f"Rules:\n"
            f"- Professional but not corporate. ARCANA personality.\n"
            f"- Focus on their specific problem, not a generic pitch.\n"
            f"- Suggest a specific next step (call, audit, proposal).\n"
            f"- Under 280 chars if for X reply, longer if for email.\n\n"
            f"Return JSON: {{"
            f'"message_x": str (under 280 chars, for X reply), '
            f'"message_email": str (2-3 paragraphs, for email follow-up), '
            f'"suggested_service": str, '
            f'"suggested_price_range": str, '
            f'"next_step": str}}',
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[Remy] Follow-up: @{handle} — {result.get('suggested_service', 'TBD')}",
            "Sales",
        )

        return result

    async def generate_proposal(self, handle: str, service: str, scope: str) -> str:
        """Generate a service proposal for a qualified lead."""
        lead_info = self.memory.get_knowledge("projects", f"lead-{handle}")

        proposal = await self.llm.ask(
            f"You are Remy, sales agent for Arcana Operations.\n"
            f"Generate a brief service proposal.\n\n"
            f"Client: @{handle}\n"
            f"Service: {service}\n"
            f"Scope: {scope}\n"
            f"Lead info: {lead_info[:500] if lead_info else 'N/A'}\n\n"
            f"Format:\n"
            f"- Problem statement (2-3 sentences)\n"
            f"- Proposed solution (3-4 bullet points)\n"
            f"- Timeline (1-2 sentences)\n"
            f"- Investment (price range)\n"
            f"- Next step\n\n"
            f"Keep it concise. Show expertise through specificity, not length.",
            tier=Tier.SONNET,
        )

        self.memory.log(f"[Remy] Proposal generated for @{handle}: {service}", "Sales")
        self.memory.save_knowledge(
            "projects",
            f"proposal-{handle}",
            f"# Proposal for @{handle}\n\nService: {service}\nScope: {scope}\n\n{proposal}",
        )

        return proposal.strip()

    async def nightly_report(self) -> str:
        """Generate nightly sales pipeline report for ARCANA to review."""
        today = self.memory.get_today()

        sales_lines = [
            line for line in today.splitlines()
            if "[Remy]" in line or "Sales" in line or "Lead" in line
        ]

        # Get open leads
        leads = [
            name for name in self.memory.list_knowledge("projects")
            if name.startswith("lead-")
        ]

        report = await self.llm.ask(
            f"Summarize today's sales activity:\n\n"
            f"Activity log:\n{chr(10).join(sales_lines[:20]) if sales_lines else 'No sales activity today.'}\n\n"
            f"Open leads in pipeline: {', '.join(leads) if leads else 'None'}\n\n"
            f"Include: leads contacted, proposals sent, pipeline status, "
            f"and recommendations for tomorrow.",
            tier=Tier.HAIKU,
            max_tokens=300,
        )
        return report.strip()
