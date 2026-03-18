"""ARCANA AI — Email Engine.

Handles ALL email operations:
1. Transactional — Product delivery, welcome emails, receipts
2. Cold outreach — Personalized cold emails via Instantly API
3. Follow-ups — Automated nurture sequences for leads
4. Support — Reply to customer support emails
5. Newsletter — Trigger Beehiiv sends

Uses SendGrid for transactional, Instantly for cold outreach campaigns.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.email")


class EmailEngine:
    """Full email system — transactional + cold outreach + nurture."""

    def __init__(
        self, llm: LLM, memory: Memory,
        sendgrid_key: str = "", instantly_key: str = "",
        from_email: str = "arcana@arcanaoperations.com",
        from_name: str = "ARCANA AI",
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.sendgrid_key = sendgrid_key
        self.instantly_key = instantly_key
        self.from_email = from_email
        self.from_name = from_name
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Transactional Email (SendGrid) ──────────────────────────────

    async def send(
        self, to_email: str, subject: str, html_body: str,
        text_body: str = "", reply_to: str = "",
    ) -> bool:
        """Send a single transactional email via SendGrid."""
        if not self.sendgrid_key:
            logger.warning("SendGrid not configured — email not sent to %s", to_email)
            self.memory.log(f"[Email] DRY RUN to {to_email}: {subject}", "Email")
            return False

        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self.from_email, "name": self.from_name},
            "subject": subject,
            "content": [],
        }
        if text_body:
            payload["content"].append({"type": "text/plain", "value": text_body})
        if html_body:
            payload["content"].append({"type": "text/html", "value": html_body})
        if reply_to:
            payload["reply_to"] = {"email": reply_to}

        try:
            client = await self._get_client()
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.sendgrid_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            success = resp.status_code in (200, 201, 202)
            if success:
                self.memory.log(f"[Email] Sent to {to_email}: {subject}", "Email")
            else:
                logger.error("SendGrid failed: %s %s", resp.status_code, resp.text[:200])
            return success
        except Exception as exc:
            logger.error("Email send error: %s", exc)
            return False

    async def send_product_delivery(
        self, to_email: str, product_name: str, download_url: str,
    ) -> bool:
        """Send product delivery email after purchase."""
        html = await self.llm.ask(
            f"Write a product delivery email for ARCANA AI.\n\n"
            f"Product: {product_name}\n"
            f"Download link: {download_url}\n\n"
            f"Rules: Short, warm, ARCANA voice. Include download link prominently.\n"
            f"Add a subtle upsell to Arcana Operations consulting.\n"
            f"Return ONLY the HTML body (no subject line).",
            tier=Tier.HAIKU, max_tokens=300,
        )
        return await self.send(
            to_email,
            f"Your {product_name} is ready — ARCANA AI",
            html.strip(),
        )

    async def send_welcome(self, to_email: str, name: str = "") -> bool:
        """Send welcome email to new subscriber/customer."""
        html = await self.llm.ask(
            f"Write a welcome email from ARCANA AI to a new subscriber.\n\n"
            f"Name: {name or 'there'}\n"
            f"What they get: AI business insights, automation strategies, exclusive content.\n"
            f"ARCANA voice: mystical, confident, no hype.\n"
            f"Include link to arcanaoperations.com.\n"
            f"Return ONLY the HTML body.",
            tier=Tier.HAIKU, max_tokens=250,
        )
        return await self.send(to_email, "Welcome to the pattern — ARCANA AI", html.strip())

    async def send_invoice(
        self, to_email: str, client_name: str, service: str,
        amount: float, due_date: str, payment_link: str,
    ) -> bool:
        """Send invoice email to a service client."""
        html = (
            f"<h2>Invoice from Arcana Operations</h2>"
            f"<p>Hi {client_name},</p>"
            f"<p>Here's your invoice for <strong>{service}</strong>.</p>"
            f"<table style='border-collapse:collapse;width:100%'>"
            f"<tr><td>Service</td><td>{service}</td></tr>"
            f"<tr><td>Amount</td><td><strong>${amount:,.2f}</strong></td></tr>"
            f"<tr><td>Due Date</td><td>{due_date}</td></tr>"
            f"</table>"
            f"<p><a href='{payment_link}' style='background:#000;color:#fff;"
            f"padding:12px 24px;text-decoration:none;display:inline-block;"
            f"margin-top:16px'>Pay Now →</a></p>"
            f"<p>Questions? Reply to this email.</p>"
            f"<p>— ARCANA AI, Arcana Operations</p>"
        )
        success = await self.send(to_email, f"Invoice: {service} — ${amount:,.2f}", html)
        if success:
            self.memory.log(
                f"[Email] Invoice sent: {client_name} — {service} ${amount:,.2f}", "Billing"
            )
        return success

    async def send_proposal(
        self, to_email: str, client_name: str, proposal_text: str, payment_link: str = "",
    ) -> bool:
        """Send a service proposal via email."""
        html = await self.llm.ask(
            f"Format this proposal as a professional HTML email from ARCANA AI.\n\n"
            f"Client: {client_name}\n"
            f"Proposal:\n{proposal_text}\n"
            f"Payment link: {payment_link or 'Will be provided after approval'}\n\n"
            f"Rules: Clean formatting, professional, ARCANA voice.\n"
            f"Include a clear CTA button.\n"
            f"Return ONLY the HTML body.",
            tier=Tier.HAIKU, max_tokens=500,
        )
        success = await self.send(
            to_email, f"Proposal for {client_name} — Arcana Operations", html.strip(),
        )
        if success:
            self.memory.log(f"[Email] Proposal sent to {client_name}", "Sales")
        return success

    # ── Cold Outreach (Instantly API) ───────────────────────────────

    async def create_campaign(
        self, name: str, subject: str, body: str,
        follow_ups: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Create a cold email campaign in Instantly."""
        if not self.instantly_key:
            logger.warning("Instantly not configured")
            return None

        try:
            client = await self._get_client()
            payload = {
                "api_key": self.instantly_key,
                "campaign_name": name,
                "sequences": [
                    {"steps": [{"type": "email", "subject": subject, "body": body, "delay": 0}]}
                ],
            }

            # Add follow-ups
            if follow_ups:
                for i, fu in enumerate(follow_ups):
                    payload["sequences"][0]["steps"].append({
                        "type": "email",
                        "subject": fu.get("subject", f"Re: {subject}"),
                        "body": fu["body"],
                        "delay": fu.get("delay_days", (i + 1) * 3),
                    })

            resp = await client.post(
                "https://api.instantly.ai/api/v1/campaign/create",
                json=payload,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                self.memory.log(f"[Outreach] Campaign created: {name}", "Outreach")
                return data
            logger.error("Instantly create failed: %s", resp.text[:200])
        except Exception as exc:
            logger.error("Instantly error: %s", exc)
        return None

    async def add_leads_to_campaign(
        self, campaign_id: str, leads: list[dict[str, str]],
    ) -> bool:
        """Add leads to an Instantly campaign. Each lead: {email, first_name, company}."""
        if not self.instantly_key:
            return False

        try:
            client = await self._get_client()
            resp = await client.post(
                "https://api.instantly.ai/api/v1/lead/add",
                json={
                    "api_key": self.instantly_key,
                    "campaign_id": campaign_id,
                    "leads": leads,
                },
            )
            success = resp.status_code in (200, 201)
            if success:
                self.memory.log(
                    f"[Outreach] Added {len(leads)} leads to campaign {campaign_id}", "Outreach"
                )
            return success
        except Exception as exc:
            logger.error("Instantly add leads error: %s", exc)
            return False

    async def get_campaign_stats(self, campaign_id: str) -> dict[str, Any]:
        """Get campaign performance stats from Instantly."""
        if not self.instantly_key:
            return {}

        try:
            client = await self._get_client()
            resp = await client.get(
                "https://api.instantly.ai/api/v1/campaign/get",
                params={"api_key": self.instantly_key, "campaign_id": campaign_id},
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.error("Instantly stats error: %s", exc)
        return {}

    async def generate_cold_email_sequence(
        self, target_role: str, target_industry: str, service: str, pain_point: str,
    ) -> dict[str, Any]:
        """Generate a full cold email sequence (initial + 3 follow-ups)."""
        result = await self.llm.ask_json(
            f"Generate a cold email sequence for ARCANA AI / Arcana Operations.\n\n"
            f"Target: {target_role} at {target_industry} companies\n"
            f"Service: {service}\n"
            f"Pain point: {pain_point}\n\n"
            f"Create 4 emails:\n"
            f"1. Initial outreach (under 100 words, personalized opening, one CTA)\n"
            f"2. Follow-up day 3 (add social proof, case study reference)\n"
            f"3. Follow-up day 7 (different angle, address common objection)\n"
            f"4. Break-up email day 14 (last chance, create urgency)\n\n"
            f"Rules:\n"
            f"- Short paragraphs, mobile-friendly\n"
            f"- No attachments, no images\n"
            f"- Personalization tokens: {{{{first_name}}}}, {{{{company}}}}\n"
            f"- Each email under 100 words\n"
            f"- CTA: reply or book a call\n\n"
            f"Return JSON: {{"
            f'"campaign_name": str, '
            f'"emails": [{{"subject": str, "body": str, "delay_days": int}}]}}',
            tier=Tier.SONNET,
        )
        return result

    # ── Lead Enrichment (Apollo) ────────────────────────────────────

    async def enrich_lead(self, email: str = "", domain: str = "", name: str = "") -> dict[str, Any]:
        """Enrich a lead with Apollo.io data."""
        # Apollo enrichment handled in outreach_engine.py
        return {"email": email, "domain": domain, "name": name}
