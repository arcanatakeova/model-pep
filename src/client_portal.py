"""ARCANA AI — Client Portal: Reports, Dashboards, and Account Management.

Generates client-facing reports and dashboards for active service clients.
Tracks deliverables, ROI, satisfaction, invoicing, upsells, renewals,
and case study generation from completed engagements.

All client data lives in memory/life/projects/<client-key>.md as markdown.
Report history is logged to memory/daily/ notes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.email_engine import EmailEngine
from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.client_portal")


# ── Data Structures ──────────────────────────────────────────────────

# Client profile expected in memory/life/projects/<client-key>.md as YAML-ish
# front matter parsed by _load_client.  Minimal expected fields:
#   name, email, service, monthly_rate, start_date, contract_end,
#   deliverables (list), sla (dict), baseline_metrics (dict)


class ClientPortal:
    """Client-facing reports, SLA tracking, invoicing, and account management."""

    def __init__(self, llm: LLM, memory: Memory, email: EmailEngine) -> None:
        self.llm = llm
        self.memory = memory
        self.email = email

    # ── Client Data Helpers ──────────────────────────────────────────

    def _load_client(self, client_key: str) -> dict[str, Any]:
        """Load client profile from memory/life/projects/<client_key>.md.

        The file is expected to contain a JSON block fenced with ```json ... ```
        at the top, followed by free-form notes.  Falls back to returning
        the raw markdown under a 'raw' key if no JSON block is found.
        """
        raw = self.memory.get_knowledge("projects", client_key)
        if not raw:
            raise ValueError(f"No client profile found for '{client_key}'")

        # Try to extract JSON block
        if "```json" in raw:
            start = raw.index("```json") + 7
            end = raw.index("```", start)
            data: dict[str, Any] = json.loads(raw[start:end].strip())
            data["_raw"] = raw
            data.setdefault("client_key", client_key)
            return data

        # Fallback: return raw content keyed for downstream prompts
        return {"client_key": client_key, "_raw": raw}

    def _save_client(self, client_key: str, data: dict[str, Any]) -> None:
        """Persist updated client data back to memory."""
        raw_notes = data.pop("_raw", "")
        client_json = json.dumps(
            {k: v for k, v in data.items() if not k.startswith("_")},
            indent=2, default=str,
        )
        content = f"```json\n{client_json}\n```\n\n{raw_notes}"
        self.memory.save_knowledge("projects", client_key, content)

    def _get_client_history(self, client_key: str, days: int = 7) -> str:
        """Search recent daily logs for entries mentioning this client."""
        results = self.memory.search(client_key, scope="daily")
        lines = [line for _, line in results[:50]]
        return "\n".join(lines) if lines else "No recent activity found."

    # ── 1. Weekly Performance Report ─────────────────────────────────

    async def generate_weekly_report(self, client_key: str) -> str:
        """Generate an HTML weekly performance report for a client.

        Returns the HTML string.  Also logs the generation to daily notes.
        """
        client = self._load_client(client_key)
        history = self._get_client_history(client_key, days=7)
        deliverables = await self.track_deliverables(client_key)
        roi = await self.calculate_roi(client_key)

        prompt = (
            "Generate a professional weekly performance report in clean HTML for a "
            "service client of Arcana Operations.  Use inline CSS, no external assets.\n\n"
            f"Client data:\n{json.dumps(client, indent=2, default=str)}\n\n"
            f"Activity this week:\n{history}\n\n"
            f"Deliverable tracking:\n{json.dumps(deliverables, indent=2, default=str)}\n\n"
            f"ROI metrics:\n{json.dumps(roi, indent=2, default=str)}\n\n"
            "Include sections:\n"
            "1. Executive Summary (3 bullet points)\n"
            "2. Deliverables Completed vs SLA\n"
            "3. Key Metrics & Trends (table)\n"
            "4. ROI Snapshot\n"
            "5. Next Week's Priorities\n\n"
            "Brand: Arcana Operations. Voice: professional, data-driven, concise.\n"
            "Return ONLY the HTML body — no markdown fences, no preamble."
        )

        html = await self.llm.ask(prompt, tier=Tier.SONNET, max_tokens=4096)
        self.memory.log(
            f"[ClientPortal] Weekly report generated for {client.get('name', client_key)}",
            "Reports",
        )
        return html.strip()

    # ── 2. Monthly Performance Report ────────────────────────────────

    async def generate_monthly_report(self, client_key: str) -> str:
        """Generate an HTML monthly performance report with invoice summary.

        Returns the HTML string.
        """
        client = self._load_client(client_key)
        history = self._get_client_history(client_key, days=30)
        deliverables = await self.track_deliverables(client_key)
        roi = await self.calculate_roi(client_key)
        satisfaction = self._get_satisfaction_history(client_key)

        prompt = (
            "Generate a comprehensive monthly performance report in clean HTML for a "
            "service client of Arcana Operations.  Use inline CSS.\n\n"
            f"Client data:\n{json.dumps(client, indent=2, default=str)}\n\n"
            f"Activity this month:\n{history}\n\n"
            f"Deliverable tracking:\n{json.dumps(deliverables, indent=2, default=str)}\n\n"
            f"ROI metrics:\n{json.dumps(roi, indent=2, default=str)}\n\n"
            f"Satisfaction history:\n{json.dumps(satisfaction, indent=2, default=str)}\n\n"
            "Include sections:\n"
            "1. Monthly Executive Summary\n"
            "2. Deliverables: Completed vs SLA (table with status icons)\n"
            "3. Performance Metrics & Month-over-Month Trends\n"
            "4. ROI Analysis (before vs after)\n"
            "5. Client Satisfaction Score & Trend\n"
            "6. Invoice Summary (service, amount, status)\n"
            "7. Recommendations & Next Month Priorities\n\n"
            "Brand: Arcana Operations.  Professional, data-forward.\n"
            "Return ONLY the HTML body."
        )

        html = await self.llm.ask(prompt, tier=Tier.SONNET, max_tokens=6000)
        self.memory.log(
            f"[ClientPortal] Monthly report generated for {client.get('name', client_key)}",
            "Reports",
        )
        return html.strip()

    # ── 3. Deliverable Tracking ──────────────────────────────────────

    async def track_deliverables(self, client_key: str) -> dict[str, Any]:
        """Track deliverables against SLA for a client.

        Returns a dict with each deliverable, its SLA target, actual count,
        and compliance status.
        """
        client = self._load_client(client_key)
        history = self._get_client_history(client_key, days=30)

        sla = client.get("sla", {})
        deliverables = client.get("deliverables", [])

        if not sla and not deliverables:
            return {"status": "no_sla_defined", "client_key": client_key}

        prompt = (
            "Analyze the following client activity log and extract deliverable counts.\n\n"
            f"Client: {client.get('name', client_key)}\n"
            f"Service: {client.get('service', 'unknown')}\n"
            f"SLA targets: {json.dumps(sla, default=str)}\n"
            f"Expected deliverables: {json.dumps(deliverables, default=str)}\n"
            f"Activity log:\n{history}\n\n"
            "Return a JSON object with:\n"
            '  "period": "YYYY-MM-DD to YYYY-MM-DD",\n'
            '  "deliverables": [\n'
            '    {"name": str, "sla_target": int, "actual": int, '
            '"compliant": bool, "notes": str}\n'
            "  ],\n"
            '  "overall_compliance_pct": float,\n'
            '  "flags": [str]  // any SLA breaches or concerns\n\n'
            "Return ONLY valid JSON."
        )

        result = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        result["client_key"] = client_key
        return result

    # ── 4. ROI Calculation ───────────────────────────────────────────

    async def calculate_roi(self, client_key: str) -> dict[str, Any]:
        """Calculate ROI metrics comparing baseline (before ARCANA) vs current.

        Uses baseline_metrics from client profile and recent activity data.
        """
        client = self._load_client(client_key)
        baseline = client.get("baseline_metrics", {})
        history = self._get_client_history(client_key, days=30)

        if not baseline:
            return {
                "client_key": client_key,
                "status": "no_baseline",
                "note": "Set baseline_metrics in client profile to enable ROI tracking.",
            }

        prompt = (
            "Calculate ROI metrics for an Arcana Operations client.\n\n"
            f"Client: {client.get('name', client_key)}\n"
            f"Service: {client.get('service', 'unknown')}\n"
            f"Monthly rate: ${client.get('monthly_rate', 0)}\n"
            f"Baseline metrics (before ARCANA):\n{json.dumps(baseline, indent=2, default=str)}\n"
            f"Recent activity log:\n{history}\n\n"
            "Return JSON with:\n"
            '  "period": str,\n'
            '  "investment": float,  // what client pays\n'
            '  "metrics": [\n'
            '    {"name": str, "baseline": float, "current": float, '
            '"change_pct": float, "direction": "up"|"down"}\n'
            "  ],\n"
            '  "estimated_value_generated": float,\n'
            '  "roi_multiple": float,  // value / investment\n'
            '  "summary": str  // one-sentence ROI summary\n\n'
            "Return ONLY valid JSON."
        )

        result = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        result["client_key"] = client_key
        return result

    # ── 5. Monthly Invoice with Delivery Receipts ────────────────────

    async def generate_invoice(self, client_key: str) -> dict[str, Any]:
        """Generate a monthly invoice with itemized delivery receipts.

        Returns invoice data dict and sends via email.
        """
        client = self._load_client(client_key)
        deliverables = await self.track_deliverables(client_key)
        now = datetime.now(timezone.utc)

        invoice = {
            "invoice_number": f"ARC-{client_key.upper()[:6]}-{now.strftime('%Y%m')}",
            "date": now.strftime("%Y-%m-%d"),
            "due_date": (now + timedelta(days=15)).strftime("%Y-%m-%d"),
            "client_name": client.get("name", client_key),
            "client_email": client.get("email", ""),
            "service": client.get("service", ""),
            "monthly_rate": client.get("monthly_rate", 0),
            "deliverables_summary": deliverables,
            "period": f"{(now - timedelta(days=30)).strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
        }

        # Generate invoice HTML
        prompt = (
            "Generate a professional HTML invoice for Arcana Operations.\n"
            "Use inline CSS, clean layout.\n\n"
            f"Invoice data:\n{json.dumps(invoice, indent=2, default=str)}\n\n"
            "Include:\n"
            "- Invoice number, date, due date\n"
            "- Client name and service\n"
            "- Itemized deliverables with completion status\n"
            "- Total amount due\n"
            "- Payment instructions (Stripe link placeholder)\n"
            "- 'Thank you for choosing Arcana Operations' footer\n\n"
            "Return ONLY the HTML body."
        )

        invoice_html = await self.llm.ask(prompt, tier=Tier.HAIKU, max_tokens=2000)
        invoice["html"] = invoice_html.strip()

        # Send via email if client has an email
        if client.get("email"):
            await self.email.send_invoice(
                to_email=client["email"],
                client_name=client.get("name", client_key),
                service=client.get("service", "AI Services"),
                amount=float(client.get("monthly_rate", 0)),
                due_date=invoice["due_date"],
                payment_link=client.get("payment_link", "https://arcanaoperations.com/pay"),
            )

        self.memory.log(
            f"[ClientPortal] Invoice {invoice['invoice_number']} generated — "
            f"${invoice['monthly_rate']:,.2f} for {invoice['client_name']}",
            "Billing",
        )

        return invoice

    # ── 6. Client Feedback / Request Intake ──────────────────────────

    async def handle_feedback(
        self, client_key: str, feedback: str, feedback_type: str = "general",
    ) -> dict[str, Any]:
        """Process client feedback or service request.

        Args:
            client_key: Client identifier.
            feedback: Raw feedback text from client.
            feedback_type: One of 'general', 'bug', 'feature_request', 'complaint', 'praise'.

        Returns dict with classification, priority, and action items.
        """
        client = self._load_client(client_key)

        prompt = (
            "Classify and respond to client feedback for Arcana Operations.\n\n"
            f"Client: {client.get('name', client_key)}\n"
            f"Service: {client.get('service', 'unknown')}\n"
            f"Feedback type: {feedback_type}\n"
            f"Feedback:\n{feedback}\n\n"
            "Return JSON with:\n"
            '  "classification": str,  // bug, feature_request, complaint, praise, question\n'
            '  "priority": "low"|"medium"|"high"|"urgent",\n'
            '  "sentiment_score": float,  // -1.0 to 1.0\n'
            '  "action_items": [str],\n'
            '  "suggested_response": str,\n'
            '  "escalate_to_human": bool,\n'
            '  "escalation_reason": str  // empty if no escalation\n\n'
            "Return ONLY valid JSON."
        )

        result = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        result["client_key"] = client_key
        result["received_at"] = datetime.now(timezone.utc).isoformat()

        # Update satisfaction score
        if "sentiment_score" in result:
            self._record_satisfaction(client_key, result["sentiment_score"])

        self.memory.log(
            f"[ClientPortal] Feedback from {client.get('name', client_key)}: "
            f"{result.get('classification', 'unknown')} / {result.get('priority', '?')}",
            "Feedback",
        )

        return result

    # ── 7. Satisfaction Score Tracking ────────────────────────────────

    def _get_satisfaction_history(self, client_key: str) -> dict[str, Any]:
        """Read satisfaction score history from client knowledge."""
        raw = self.memory.get_knowledge("areas", f"satisfaction-{client_key}")
        if not raw:
            return {"client_key": client_key, "scores": [], "average": 0.0}

        try:
            if "```json" in raw:
                start = raw.index("```json") + 7
                end = raw.index("```", start)
                return json.loads(raw[start:end].strip())
        except (json.JSONDecodeError, ValueError):
            pass

        return {"client_key": client_key, "scores": [], "average": 0.0}

    def _record_satisfaction(self, client_key: str, score: float) -> None:
        """Append a satisfaction data point and update the running average."""
        history = self._get_satisfaction_history(client_key)
        entry = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "score": round(score, 2),
        }
        history.setdefault("scores", []).append(entry)

        scores = [s["score"] for s in history["scores"]]
        history["average"] = round(sum(scores) / len(scores), 2) if scores else 0.0
        history["client_key"] = client_key
        history["last_updated"] = datetime.now(timezone.utc).isoformat()

        content = f"```json\n{json.dumps(history, indent=2)}\n```"
        self.memory.save_knowledge("areas", f"satisfaction-{client_key}", content)

    # ── 8. Upsell Recommendations ────────────────────────────────────

    async def recommend_upsell(self, client_key: str) -> dict[str, Any]:
        """Analyze client data and recommend upsell opportunities.

        Returns ranked list of upsell suggestions with estimated value.
        """
        client = self._load_client(client_key)
        roi = await self.calculate_roi(client_key)
        satisfaction = self._get_satisfaction_history(client_key)
        deliverables = await self.track_deliverables(client_key)

        prompt = (
            "Analyze this Arcana Operations client and recommend upsell opportunities.\n\n"
            f"Client data:\n{json.dumps(client, indent=2, default=str)}\n\n"
            f"ROI metrics:\n{json.dumps(roi, indent=2, default=str)}\n\n"
            f"Satisfaction:\n{json.dumps(satisfaction, indent=2, default=str)}\n\n"
            f"Deliverable performance:\n{json.dumps(deliverables, indent=2, default=str)}\n\n"
            "Available Arcana Operations services:\n"
            "- AI Agent Setup ($2K + $500/mo)\n"
            "- SEO Audit & Strategy ($1.5K)\n"
            "- Marketing Strategy ($2K)\n"
            "- Custom AI Agent Build ($2-5K + $500/mo)\n"
            "- Content Marketing Package ($1.5K/mo)\n"
            "- Premium Discord Access ($49/mo)\n"
            "- AI Newsletter Sponsorship (varies)\n\n"
            "Return JSON with:\n"
            '  "recommendations": [\n'
            '    {"service": str, "reason": str, "estimated_value": float, '
            '"confidence": float, "timing": str}\n'
            "  ],\n"
            '  "overall_upsell_readiness": "low"|"medium"|"high",\n'
            '  "best_approach": str  // how to pitch\n\n'
            "Rank by confidence.  Only recommend services they don't already have.\n"
            "Return ONLY valid JSON."
        )

        result = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        result["client_key"] = client_key
        self.memory.log(
            f"[ClientPortal] Upsell analysis for {client.get('name', client_key)}: "
            f"{result.get('overall_upsell_readiness', '?')} readiness",
            "Sales",
        )
        return result

    # ── 9. Renewal Proposal Generation ───────────────────────────────

    async def generate_renewal_proposal(self, client_key: str) -> dict[str, Any]:
        """Generate a contract renewal proposal before contract end.

        Returns proposal data and optionally sends via email.
        """
        client = self._load_client(client_key)
        roi = await self.calculate_roi(client_key)
        satisfaction = self._get_satisfaction_history(client_key)
        upsells = await self.recommend_upsell(client_key)

        prompt = (
            "Generate a contract renewal proposal for an Arcana Operations client.\n\n"
            f"Client data:\n{json.dumps(client, indent=2, default=str)}\n\n"
            f"ROI achieved:\n{json.dumps(roi, indent=2, default=str)}\n\n"
            f"Satisfaction:\n{json.dumps(satisfaction, indent=2, default=str)}\n\n"
            f"Upsell opportunities:\n{json.dumps(upsells, indent=2, default=str)}\n\n"
            "Generate JSON with:\n"
            '  "proposal_type": "renewal"|"renewal_with_upgrade",\n'
            '  "current_rate": float,\n'
            '  "proposed_rate": float,\n'
            '  "proposed_term_months": int,\n'
            '  "included_services": [str],\n'
            '  "new_additions": [str],  // upsells bundled in\n'
            '  "discount_pct": float,  // loyalty discount if any\n'
            '  "key_wins": [str],  // top 3 results to highlight\n'
            '  "proposal_text": str,  // full proposal narrative (professional, persuasive)\n'
            '  "urgency_note": str  // why renew now\n\n'
            "Return ONLY valid JSON."
        )

        result = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        result["client_key"] = client_key
        result["generated_at"] = datetime.now(timezone.utc).isoformat()

        # Send proposal email if client has email
        if client.get("email") and result.get("proposal_text"):
            await self.email.send_proposal(
                to_email=client["email"],
                client_name=client.get("name", client_key),
                proposal_text=result["proposal_text"],
                payment_link=client.get("payment_link", ""),
            )

        self.memory.log(
            f"[ClientPortal] Renewal proposal generated for {client.get('name', client_key)} — "
            f"${result.get('proposed_rate', 0):,.2f}/mo x {result.get('proposed_term_months', 0)}mo",
            "Sales",
        )

        return result

    # ── 10. Case Study Generation ────────────────────────────────────

    async def create_case_study(self, client_key: str) -> dict[str, Any]:
        """Create a marketing case study from a completed or mature engagement.

        Returns case study content (HTML + summary) for website and X posts.
        """
        client = self._load_client(client_key)
        roi = await self.calculate_roi(client_key)
        satisfaction = self._get_satisfaction_history(client_key)
        deliverables = await self.track_deliverables(client_key)

        prompt = (
            "Create a marketing case study from this Arcana Operations client engagement.\n\n"
            f"Client data:\n{json.dumps(client, indent=2, default=str)}\n\n"
            f"ROI achieved:\n{json.dumps(roi, indent=2, default=str)}\n\n"
            f"Satisfaction:\n{json.dumps(satisfaction, indent=2, default=str)}\n\n"
            f"Deliverables:\n{json.dumps(deliverables, indent=2, default=str)}\n\n"
            "Generate JSON with:\n"
            '  "title": str,  // compelling case study title\n'
            '  "subtitle": str,\n'
            '  "industry": str,\n'
            '  "challenge": str,  // what the client struggled with\n'
            '  "solution": str,  // what ARCANA / Arcana Operations did\n'
            '  "results": [{"metric": str, "before": str, "after": str, "change": str}],\n'
            '  "testimonial_prompt": str,  // suggested quote to request from client\n'
            '  "html_body": str,  // full case study as HTML with inline CSS\n'
            '  "x_thread": [str],  // 4-6 tweet thread summarizing the case study\n'
            '  "one_liner": str  // single sentence for bios/pitches\n\n'
            "Rules:\n"
            "- Anonymize company name (use industry + size descriptor) unless client approved\n"
            "- Focus on measurable outcomes\n"
            "- ARCANA voice: data-driven, confident, no hype\n"
            "Return ONLY valid JSON."
        )

        result = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        result["client_key"] = client_key
        result["generated_at"] = datetime.now(timezone.utc).isoformat()

        # Save case study to memory for reuse
        self.memory.save_knowledge(
            "resources",
            f"case-study-{client_key}",
            f"```json\n{json.dumps(result, indent=2, default=str)}\n```",
        )

        self.memory.log(
            f"[ClientPortal] Case study created for {client.get('name', client_key)}: "
            f"\"{result.get('title', 'Untitled')}\"",
            "Marketing",
        )

        return result

    # ── Report Sending ───────────────────────────────────────────────

    async def send_report(self, client_key: str, report_html: str) -> bool:
        """Send a generated report to a client via email.

        Args:
            client_key: Client identifier.
            report_html: Full HTML body of the report.

        Returns True if sent successfully.
        """
        client = self._load_client(client_key)
        email_addr = client.get("email")
        if not email_addr:
            logger.warning("No email on file for client %s — report not sent", client_key)
            return False

        client_name = client.get("name", client_key)
        now = datetime.now(timezone.utc)
        subject = f"Performance Report — {client_name} — {now.strftime('%b %d, %Y')}"

        success = await self.email.send(
            to_email=email_addr,
            subject=subject,
            html_body=report_html,
        )

        if success:
            self.memory.log(
                f"[ClientPortal] Report sent to {client_name} ({email_addr})", "Reports"
            )
        else:
            self.memory.log(
                f"[ClientPortal] FAILED to send report to {client_name}", "Reports"
            )

        return success

    # ── Scheduled Report Runner ──────────────────────────────────────

    async def run_scheduled_reports(self) -> list[dict[str, Any]]:
        """Run weekly/monthly reports for all active clients.

        Called by the orchestrator on a schedule.  Determines which reports
        are due based on the current date and sends them.
        """
        now = datetime.now(timezone.utc)
        is_month_end = (now + timedelta(days=3)).month != now.month
        is_monday = now.weekday() == 0

        results: list[dict[str, Any]] = []
        clients = self.memory.list_knowledge("projects")

        for client_key in clients:
            try:
                client = self._load_client(client_key)
            except (ValueError, json.JSONDecodeError):
                continue

            # Skip non-client projects
            if not client.get("email") or not client.get("service"):
                continue

            report_type = None
            html = ""

            if is_month_end:
                report_type = "monthly"
                html = await self.generate_monthly_report(client_key)
            elif is_monday:
                report_type = "weekly"
                html = await self.generate_weekly_report(client_key)

            if report_type and html:
                sent = await self.send_report(client_key, html)
                results.append({
                    "client_key": client_key,
                    "report_type": report_type,
                    "sent": sent,
                })

            # Check if renewal is coming up
            contract_end = client.get("contract_end", "")
            if contract_end:
                try:
                    end_date = datetime.fromisoformat(contract_end)
                    days_left = (end_date - now).days
                    if 25 <= days_left <= 35:
                        await self.generate_renewal_proposal(client_key)
                        results.append({
                            "client_key": client_key,
                            "action": "renewal_proposal_sent",
                            "days_until_end": days_left,
                        })
                except (ValueError, TypeError):
                    pass

        self.memory.log(
            f"[ClientPortal] Scheduled reports run: {len(results)} actions taken", "Reports"
        )

        return results
