"""ARCANA AI — Remy: Sales Sub-Agent.

Handles:
- Inbound lead follow-up
- Sales conversations via email/DM
- Proposal generation for Arcana Operations services
- Lead nurturing and pipeline management

Reports to ARCANA nightly. Hot leads escalated to Ian/Tan immediately.
"""

from __future__ import annotations

import html as _html
import logging
from typing import Any, TYPE_CHECKING

from src.llm import LLM, Tier
from src.memory import Memory

if TYPE_CHECKING:
    from src.email_engine import EmailEngine
    from src.notify import Notifier
    from src.x_client import XClient

logger = logging.getLogger("arcana.remy")


class Remy:
    """Sales and lead management sub-agent."""

    def __init__(
        self,
        llm: LLM,
        memory: Memory,
        email_engine: EmailEngine | None = None,
        x_client: XClient | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.email_engine = email_engine
        self.x_client = x_client
        self.notifier = notifier

    async def follow_up(self, handle: str, context: str) -> dict[str, Any]:
        """Generate a follow-up message for a qualified lead."""
        lead_info = self.memory.get_knowledge("projects", f"lead-{handle}")
        lead_info = lead_info or ""

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

        if not result or not isinstance(result, dict):
            return {"message_x": "", "message_email": "", "suggested_service": "", "next_step": "", "sent_via": []}

        self.memory.log(
            f"[Remy] Follow-up: @{handle} — {result.get('suggested_service', 'TBD')}",
            "Sales",
        )

        # ── Actually SEND the follow-up ──────────────────────────────
        sent_via: list[str] = []

        # Send email follow-up if email_engine is wired and lead has email
        if self.email_engine and result.get("message_email"):
            # Extract email from lead file if available
            email = self._extract_email(lead_info)
            if email:
                sent = await self.email_engine.send(
                    to_email=email,
                    subject=f"Following up — Arcana Operations",
                    html_body=f"<p>{_html.escape(result.get('message_email', ''))}</p>",
                )
                if sent:
                    sent_via.append("email")

        # Send X reply/DM if x_client is wired
        if self.x_client and result.get("message_x"):
            try:
                # Reply to their latest tweet mentioning us, or post a follow-up
                await self.x_client.post_tweet(
                    f"@{handle} {result['message_x']}"
                )
                sent_via.append("x_reply")
            except Exception as exc:
                logger.warning("X follow-up failed for @%s: %s", handle, exc)

        if sent_via:
            self.memory.log(
                f"[Remy] Follow-up SENT to @{handle} via {', '.join(sent_via)}",
                "Sales",
            )
        result["sent_via"] = sent_via
        return result

    @staticmethod
    def _extract_email(lead_info: str) -> str:
        """Extract an email address from lead info text."""
        import re
        match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", lead_info)
        return match.group(0) if match else ""

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

        # ── Send the proposal via email ──────────────────────────────
        if self.email_engine:
            lead_info_text = lead_info or ""
            email = self._extract_email(lead_info_text)
            if email:
                sent = await self.email_engine.send_proposal(
                    to_email=email,
                    client_name=handle,
                    proposal_text=proposal.strip(),
                )
                if sent:
                    self.memory.log(
                        f"[Remy] Proposal SENT to @{handle} via email", "Sales",
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

        report_text = report.strip()

        # ── Send nightly report to Discord via notifier ──────────────
        if self.notifier:
            await self.notifier.send(
                f"**[Remy Nightly Report]**\n\n{report_text}", level="info",
            )
            self.memory.log("[Remy] Nightly report sent to Discord", "Sales")

        return report_text

    async def auto_sequence(self, handle: str, context: str) -> dict[str, Any]:
        """Trigger a multi-step follow-up sequence for a qualified lead.

        Steps:
        1. Immediate follow-up (email + X reply)
        2. Generate a tailored proposal
        3. Send the proposal via email
        4. Log the full sequence to memory
        """
        results: dict[str, Any] = {"handle": handle, "steps": []}

        # Step 1: Initial follow-up
        follow_up = await self.follow_up(handle, context)
        results["follow_up"] = follow_up
        results["steps"].append("follow_up")

        # Step 2: If we identified a service fit, generate and send a proposal
        service = follow_up.get("suggested_service", "")
        if service:
            next_step = follow_up.get("next_step", "")
            proposal = await self.generate_proposal(
                handle, service, next_step or context,
            )
            results["proposal"] = proposal
            results["steps"].append("proposal_generated")

        # Step 3: Notify Ian/Tan about the sequence
        if self.notifier:
            await self.notifier.send(
                f"**[Remy]** Auto-sequence triggered for @{handle}\n"
                f"Service: {service or 'TBD'}\n"
                f"Steps completed: {', '.join(results['steps'])}",
                level="info",
            )
            results["steps"].append("team_notified")

        self.memory.log(
            f"[Remy] Auto-sequence for @{handle}: {', '.join(results['steps'])}",
            "Sales",
        )
        return results
