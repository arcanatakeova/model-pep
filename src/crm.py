"""ARCANA AI — CRM & Client Lifecycle Management.

Full client lifecycle from first contact to renewal:

Pipeline stages:
  PROSPECT → LEAD → QUALIFIED → PROPOSAL_SENT → NEGOTIATING →
  WON → ONBOARDING → ACTIVE → UPSELL → RENEWAL → CHURNED

Every client interaction is tracked. Every dollar of pipeline is visible.
Automated actions at each stage. No lead falls through the cracks.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.crm")


class PipelineStage(str, Enum):
    PROSPECT = "prospect"       # Just discovered, not yet contacted
    LEAD = "lead"               # Contacted, showed interest
    QUALIFIED = "qualified"     # Budget, authority, need, timeline confirmed
    PROPOSAL_SENT = "proposal_sent"
    NEGOTIATING = "negotiating"
    WON = "won"                 # Deal closed, payment expected
    ONBOARDING = "onboarding"   # Setting up service delivery
    ACTIVE = "active"           # Receiving services, paying monthly
    UPSELL = "upsell"           # Opportunity to sell more
    RENEWAL = "renewal"         # Coming up for renewal
    CHURNED = "churned"         # Lost the client
    LOST = "lost"               # Deal didn't close


class CRM:
    """Full CRM — pipeline, lifecycle, automation, reporting."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    # ── Contact Management ──────────────────────────────────────────

    def create_contact(
        self, name: str, email: str = "", company: str = "",
        source: str = "", phone: str = "", role: str = "",
        notes: str = "",
    ) -> str:
        """Create a new contact in the CRM."""
        key = f"contact-{name.lower().replace(' ', '-')[:30]}"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        self.memory.save_knowledge(
            "resources", key,
            f"# Contact: {name}\n\n"
            f"- Email: {email}\n"
            f"- Company: {company}\n"
            f"- Role: {role}\n"
            f"- Phone: {phone}\n"
            f"- Source: {source}\n"
            f"- Created: {ts}\n"
            f"- Last contact: {ts}\n"
            f"- Notes: {notes}\n"
            f"- Interactions: 0\n",
        )
        self.memory.log(f"[CRM] Contact created: {name} ({company})", "CRM")
        return key

    def update_contact(self, key: str, updates: dict[str, str]) -> None:
        """Update a contact's information."""
        data = self.memory.get_knowledge("resources", key) or ""
        for field, value in updates.items():
            # Replace existing field or append
            lines = data.splitlines()
            updated = False
            for i, line in enumerate(lines):
                if line.startswith(f"- {field}:"):
                    lines[i] = f"- {field}: {value}"
                    updated = True
                    break
            if not updated:
                lines.append(f"- {field}: {value}")
            data = "\n".join(lines)
        self.memory.save_knowledge("resources", key, data)

    def log_interaction(self, contact_key: str, interaction_type: str, summary: str) -> None:
        """Log an interaction with a contact."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        data = self.memory.get_knowledge("resources", contact_key) or ""
        data += f"\n\n### {ts} — {interaction_type}\n{summary}\n"
        self.memory.save_knowledge("resources", contact_key, data)
        self.update_contact(contact_key, {"Last contact": ts})

    # ── Deal / Pipeline Management ──────────────────────────────────

    def create_deal(
        self, contact_key: str, service: str, value_monthly: float,
        source: str = "", notes: str = "",
    ) -> str:
        """Create a new deal in the pipeline."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        deal_key = f"deal-{contact_key.replace('contact-', '')}-{ts}-{random.randint(100,999)}"

        self.memory.save_knowledge(
            "projects", deal_key,
            f"# Deal: {service}\n\n"
            f"- Contact: {contact_key}\n"
            f"- Service: {service}\n"
            f"- Monthly value: ${value_monthly:,.2f}\n"
            f"- Annual value: ${value_monthly * 12:,.2f}\n"
            f"- Stage: {PipelineStage.PROSPECT.value}\n"
            f"- Source: {source}\n"
            f"- Created: {ts}\n"
            f"- Last updated: {ts}\n"
            f"- Probability: 10%\n"
            f"- Next action: Qualify lead\n"
            f"- Notes: {notes}\n",
        )
        self.memory.log(
            f"[CRM] Deal created: {service} ${value_monthly:,.2f}/mo ({contact_key})", "CRM"
        )
        return deal_key

    def advance_deal(self, deal_key: str, new_stage: PipelineStage, notes: str = "") -> None:
        """Move a deal to the next pipeline stage."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        data = self.memory.get_knowledge("projects", deal_key)
        if not data:
            return

        # Update stage
        probability_map = {
            PipelineStage.PROSPECT: 10,
            PipelineStage.LEAD: 20,
            PipelineStage.QUALIFIED: 40,
            PipelineStage.PROPOSAL_SENT: 50,
            PipelineStage.NEGOTIATING: 70,
            PipelineStage.WON: 100,
            PipelineStage.ONBOARDING: 100,
            PipelineStage.ACTIVE: 100,
            PipelineStage.UPSELL: 60,
            PipelineStage.RENEWAL: 75,
            PipelineStage.LOST: 0,
            PipelineStage.CHURNED: 0,
        }

        next_actions = {
            PipelineStage.LEAD: "Send discovery email",
            PipelineStage.QUALIFIED: "Generate and send proposal",
            PipelineStage.PROPOSAL_SENT: "Follow up in 3 days",
            PipelineStage.NEGOTIATING: "Address objections, finalize terms",
            PipelineStage.WON: "Create invoice and onboard",
            PipelineStage.ONBOARDING: "Set up service delivery",
            PipelineStage.ACTIVE: "Deliver first results, schedule check-in",
            PipelineStage.UPSELL: "Identify expansion opportunity",
            PipelineStage.RENEWAL: "Send renewal proposal",
        }

        lines = data.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("- Stage:"):
                lines[i] = f"- Stage: {new_stage.value}"
            elif line.startswith("- Last updated:"):
                lines[i] = f"- Last updated: {ts}"
            elif line.startswith("- Probability:"):
                lines[i] = f"- Probability: {probability_map.get(new_stage, 50)}%"
            elif line.startswith("- Next action:"):
                lines[i] = f"- Next action: {next_actions.get(new_stage, 'Review')}"

        if notes:
            lines.append(f"\n### {ts} — Stage: {new_stage.value}\n{notes}")

        self.memory.save_knowledge("projects", deal_key, "\n".join(lines))
        self.memory.log(
            f"[CRM] Deal advanced: {deal_key} → {new_stage.value} | {notes[:80]}", "CRM"
        )

    # ── Pipeline Queries ────────────────────────────────────────────

    def get_pipeline(self, stage: PipelineStage | None = None) -> list[dict[str, Any]]:
        """Get all deals, optionally filtered by stage."""
        deals = []
        for key in self.memory.list_knowledge("projects"):
            if not key.startswith("deal-"):
                continue
            data = self.memory.get_knowledge("projects", key)
            if not data:
                continue

            deal: dict[str, Any] = {"key": key}
            for line in data.splitlines():
                if line.startswith("- "):
                    parts = line[2:].split(":", 1)
                    if len(parts) == 2:
                        field = parts[0].strip().lower().replace(" ", "_")
                        deal[field] = parts[1].strip()

            if stage and deal.get("stage") != stage.value:
                continue
            deals.append(deal)

        return deals

    def get_pipeline_value(self) -> dict[str, Any]:
        """Calculate total pipeline value by stage."""
        deals = self.get_pipeline()
        by_stage: dict[str, float] = {}
        weighted_total = 0.0

        for deal in deals:
            stage = deal.get("stage", "unknown")
            try:
                value = float(deal.get("monthly_value", "0").replace("$", "").replace(",", ""))
                prob = float(deal.get("probability", "0").replace("%", "")) / 100
            except ValueError:
                value = 0
                prob = 0

            by_stage[stage] = by_stage.get(stage, 0) + value
            weighted_total += value * prob

        return {
            "total_deals": len(deals),
            "by_stage": by_stage,
            "total_monthly_value": sum(by_stage.values()),
            "weighted_monthly_value": weighted_total,
            "weighted_annual_value": weighted_total * 12,
        }

    # ── Automated Actions ───────────────────────────────────────────

    async def get_overdue_actions(self) -> list[dict[str, Any]]:
        """Find deals that need attention — stale, overdue follow-ups."""
        deals = self.get_pipeline()
        overdue = []
        now = datetime.now(timezone.utc)

        for deal in deals:
            stage = deal.get("stage", "")
            if stage in ("won", "active", "lost", "churned"):
                continue

            last_updated = deal.get("last_updated", "")
            if not last_updated:
                continue

            try:
                updated_dt = datetime.strptime(last_updated[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_stale = (now - updated_dt).days
            except ValueError:
                continue

            # Flag if stale based on stage
            stale_thresholds = {
                "prospect": 2, "lead": 3, "qualified": 3,
                "proposal_sent": 3, "negotiating": 5,
                "onboarding": 2, "upsell": 7, "renewal": 7,
            }

            threshold = stale_thresholds.get(stage, 5)
            if days_stale >= threshold:
                overdue.append({
                    "key": deal["key"],
                    "stage": stage,
                    "days_stale": days_stale,
                    "next_action": deal.get("next_action", "Review"),
                    "monthly_value": deal.get("monthly_value", "N/A"),
                })

        return overdue

    async def auto_advance_pipeline(self) -> dict[str, Any]:
        """Automated pipeline actions — follow-ups, reminders, escalations."""
        overdue = await self.get_overdue_actions()
        actions_taken = 0

        for deal in overdue:
            # Generate follow-up action based on stage
            action = await self.llm.ask_json(
                f"This deal in the CRM is stale. What should ARCANA do?\n\n"
                f"Deal: {deal['key']}\n"
                f"Stage: {deal['stage']}\n"
                f"Days since update: {deal['days_stale']}\n"
                f"Next action was: {deal['next_action']}\n"
                f"Value: {deal['monthly_value']}/mo\n\n"
                f"Options:\n"
                f"- Send follow-up email\n"
                f"- Send follow-up X DM\n"
                f"- Escalate to Ian/Tan\n"
                f"- Close as lost\n"
                f"- Move to next stage\n\n"
                f"Return JSON: {{"
                f'"action": str, "message": str|null, '
                f'"new_stage": str|null, "escalate": bool}}',
                tier=Tier.HAIKU,
            )

            if action.get("new_stage"):
                try:
                    new_stage = PipelineStage(action["new_stage"])
                    self.advance_deal(deal["key"], new_stage, f"Auto-advanced: {action.get('action', '')}")
                    actions_taken += 1
                except ValueError:
                    pass

        return {"overdue_deals": len(overdue), "actions_taken": actions_taken}

    # ── Client Onboarding ───────────────────────────────────────────

    async def generate_onboarding_checklist(self, service: str, client_name: str) -> dict[str, Any]:
        """Generate a client onboarding checklist."""
        result = await self.llm.ask_json(
            f"Generate an onboarding checklist for a new Arcana Operations client.\n\n"
            f"Client: {client_name}\n"
            f"Service: {service}\n\n"
            f"Include steps for:\n"
            f"- Access/credential collection\n"
            f"- Initial setup and configuration\n"
            f"- First deliverable timeline\n"
            f"- Communication preferences\n"
            f"- Success metrics definition\n"
            f"- First check-in scheduling\n\n"
            f"Return JSON: {{"
            f'"checklist": [{{"step": str, "description": str, "owner": "arcana"|"client", "due_days": int}}], '
            f'"estimated_setup_days": int, '
            f'"first_deliverable": str}}',
            tier=Tier.HAIKU,
        )
        return result

    # ── Reporting ───────────────────────────────────────────────────

    def format_pipeline_report(self) -> str:
        """Format pipeline for morning/nightly report."""
        pv = self.get_pipeline_value()
        deals = self.get_pipeline()

        active_clients = [d for d in deals if d.get("stage") == "active"]

        lines = [
            f"**CRM Pipeline**",
            f"Total deals: {pv['total_deals']}",
            f"Active clients: {len(active_clients)}",
            f"Pipeline value: ${pv['total_monthly_value']:,.0f}/mo",
            f"Weighted value: ${pv['weighted_monthly_value']:,.0f}/mo",
            f"Annual forecast: ${pv['weighted_annual_value']:,.0f}",
        ]

        if pv["by_stage"]:
            lines.append("By stage:")
            for stage, value in sorted(pv["by_stage"].items()):
                count = sum(1 for d in deals if d.get("stage") == stage)
                lines.append(f"  {stage}: {count} deals (${value:,.0f}/mo)")

        return "\n".join(lines)

    async def generate_case_study(self, deal_key: str) -> dict[str, Any]:
        """Generate a case study from a completed client engagement."""
        data = self.memory.get_knowledge("projects", deal_key)
        if not data:
            return {}

        result = await self.llm.ask_json(
            f"Generate a case study from this completed client engagement.\n\n"
            f"Client data:\n{data}\n\n"
            f"Format:\n"
            f"- Client industry (anonymized if needed)\n"
            f"- Challenge they faced\n"
            f"- Solution ARCANA/Arcana Operations delivered\n"
            f"- Results (quantified where possible)\n"
            f"- Testimonial quote (generated, realistic)\n\n"
            f"Return JSON: {{"
            f'"title": str, "industry": str, "challenge": str, '
            f'"solution": str, "results": [str], "quote": str, '
            f'"metrics": {{str: str}}}}',
            tier=Tier.SONNET,
        )
        return result
