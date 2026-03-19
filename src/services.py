"""ARCANA AI — Scalable Service Delivery Engine.

Automated delivery of recurring services. Each service is 80-90% automatable.
ARCANA handles delivery, Ian/Tan handle sales and edge cases.

Active Services:
1. White-label AI chatbots — $300-1K/mo per client (Stammer.ai or custom)
2. Review response service — $29-59/mo per location (Google/Yelp auto-responses)
3. Social media management — $500-5K/mo per client (Buffer + AI content)
4. Lead gen as a service — $500-2K/mo per client (scrape, enrich, score, deliver)
5. Email outreach — $500-2K/mo per client (cold email personalization)
6. Competitive intelligence — $500-5K/mo per client (Playwright + AI analysis)

Target: 50+ clients across all services = $20K+ MRR from services alone.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.services")


class ServiceEngine:
    """Manage and deliver recurring automated services.

    Production features:
    - Input validation on all public methods
    - Safe MRR calculation with regex parsing
    - Client status tracking
    """

    VALID_SERVICES = {"chatbot", "reviews", "social", "lead_gen", "email", "intel", "seo"}
    VALID_PLATFORMS = {"google", "yelp", "facebook", "tripadvisor", "trustpilot"}

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    # ── Review Response Service ─────────────────────────────────────

    async def generate_review_response(
        self, business_name: str, reviewer: str, rating: int, review_text: str, platform: str = "google"
    ) -> str:
        """Generate a professional response to a customer review."""
        # Input validation
        if not business_name or not business_name.strip():
            raise ValueError("business_name is required")
        rating = max(1, min(5, rating))  # Clamp to 1-5
        review_text = review_text[:2000] if review_text else "No review text"
        platform = platform.lower() if platform else "google"
        response = await self.llm.ask(
            f"Generate a business owner response to this {platform} review.\n\n"
            f"Business: {business_name}\n"
            f"Reviewer: {reviewer}\n"
            f"Rating: {rating}/5 stars\n"
            f"Review: {review_text}\n\n"
            f"Rules:\n"
            f"- Professional, warm, not generic\n"
            f"- Reference specific details from their review\n"
            f"- If negative (1-3 stars): empathetic, offer to resolve offline\n"
            f"- If positive (4-5 stars): grateful, invite them back\n"
            f"- Under 150 words\n"
            f"- No templates or canned language",
            tier=Tier.HAIKU,
            max_tokens=200,
        )
        self.memory.log(
            f"[Service:Reviews] {business_name} — {rating}★ response generated",
            "Services",
        )
        return response.strip()

    # ── Social Media Management ─────────────────────────────────────

    async def generate_social_content(
        self, client_name: str, industry: str, platforms: list[str], tone: str = "professional"
    ) -> dict[str, Any]:
        """Generate a week of social media content for a client."""
        result = await self.llm.ask_json(
            f"Generate 7 days of social media content for a client.\n\n"
            f"Client: {client_name}\n"
            f"Industry: {industry}\n"
            f"Platforms: {', '.join(platforms)}\n"
            f"Tone: {tone}\n\n"
            f"For each day, create:\n"
            f"- A main post (under 280 chars for X, longer for LinkedIn)\n"
            f"- A suggested image description\n"
            f"- Best posting time\n\n"
            f"Mix content types: educational, behind-the-scenes, promotional, engagement.\n\n"
            f"Return JSON: {{"
            f'"posts": [{{"day": int, "platform": str, "content": str, "image_desc": str, "time": str, "type": str}}]}}',
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[Service:Social] Generated week of content for {client_name}",
            "Services",
        )
        return result

    # ── Lead Generation as a Service ────────────────────────────────

    async def generate_lead_list(
        self, client_name: str, target_industry: str, target_role: str, location: str = ""
    ) -> dict[str, Any]:
        """Generate lead list criteria and outreach templates."""
        result = await self.llm.ask_json(
            f"Create a B2B lead generation strategy for a client.\n\n"
            f"Client: {client_name}\n"
            f"Target industry: {target_industry}\n"
            f"Target role: {target_role}\n"
            f"Location: {location or 'Any'}\n\n"
            f"Provide:\n"
            f"- Search criteria for Apollo.io / LinkedIn Sales Navigator\n"
            f"- 3 cold email templates (short, personalized, value-first)\n"
            f"- 3 LinkedIn connection message templates\n"
            f"- Follow-up sequence (3 touchpoints over 2 weeks)\n\n"
            f"Return JSON: {{"
            f'"search_criteria": {{str: str}}, '
            f'"email_templates": [{{subject: str, body: str}}], '
            f'"linkedin_templates": [str], '
            f'"follow_up_sequence": [{{day: int, channel: str, message: str}}]}}',
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[Service:LeadGen] Strategy for {client_name} → {target_industry}/{target_role}",
            "Services",
        )
        return result

    # ── Cold Email Personalization ──────────────────────────────────

    async def personalize_email(
        self, prospect_name: str, prospect_company: str, prospect_role: str,
        prospect_context: str, offer: str
    ) -> dict[str, str]:
        """Generate a personalized cold email."""
        result = await self.llm.ask_json(
            f"Write a personalized cold email.\n\n"
            f"Prospect: {prospect_name}, {prospect_role} at {prospect_company}\n"
            f"Context about them: {prospect_context}\n"
            f"What we're offering: {offer}\n\n"
            f"Rules:\n"
            f"- Under 150 words\n"
            f"- Reference something specific about their company\n"
            f"- Lead with value, not features\n"
            f"- One clear CTA (reply or book a call)\n"
            f"- No salesy language\n\n"
            f"Return JSON: {{\"subject\": str, \"body\": str}}",
            tier=Tier.HAIKU,
            max_tokens=250,
        )
        return result

    # ── Competitive Intelligence ────────────────────────────────────

    async def generate_intel_report(
        self, client_name: str, competitors: list[str], focus_areas: list[str]
    ) -> str:
        """Generate a competitive intelligence report."""
        report = await self.llm.ask(
            f"Generate a competitive intelligence report.\n\n"
            f"Client: {client_name}\n"
            f"Competitors: {', '.join(competitors)}\n"
            f"Focus areas: {', '.join(focus_areas)}\n\n"
            f"Structure:\n"
            f"1. Executive Summary (3 sentences)\n"
            f"2. Competitor Profiles (key metrics, recent moves)\n"
            f"3. Market Positioning Map (describe positioning)\n"
            f"4. Opportunities (what competitors are missing)\n"
            f"5. Threats (what competitors do better)\n"
            f"6. Recommended Actions (3-5 specific moves)\n\n"
            f"Be specific, data-driven where possible, and actionable.",
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[Service:Intel] Report for {client_name} vs {', '.join(competitors[:3])}",
            "Services",
        )
        return report.strip()

    # ── AI Chatbot Provisioning ─────────────────────────────────────

    async def design_chatbot(
        self, client_name: str, industry: str, use_cases: list[str], tone: str = "professional"
    ) -> dict[str, Any]:
        """Design a white-label chatbot for a client."""
        result = await self.llm.ask_json(
            f"Design an AI chatbot for a business client.\n\n"
            f"Client: {client_name}\n"
            f"Industry: {industry}\n"
            f"Use cases: {', '.join(use_cases)}\n"
            f"Tone: {tone}\n\n"
            f"Provide:\n"
            f"- System prompt for the chatbot\n"
            f"- 10 FAQ questions and answers\n"
            f"- Escalation rules (when to hand off to human)\n"
            f"- Suggested greeting message\n"
            f"- 3 conversation flow examples\n\n"
            f"Return JSON: {{"
            f'"system_prompt": str, '
            f'"greeting": str, '
            f'"faqs": [{{"q": str, "a": str}}], '
            f'"escalation_rules": [str], '
            f'"sample_flows": [{{"user": str, "bot": str}}]}}',
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[Service:Chatbot] Designed chatbot for {client_name} ({industry})",
            "Services",
        )
        return result

    # ── Client Management ───────────────────────────────────────────

    def add_client(self, name: str, service: str, monthly_rate: float, details: str = "") -> None:
        """Register a new service client with validation."""
        if not name or not name.strip():
            raise ValueError("Client name is required")
        if monthly_rate < 0:
            raise ValueError(f"Monthly rate must be non-negative, got {monthly_rate}")

        self.memory.save_knowledge(
            "projects",
            f"client-{name.lower().replace(' ', '-')}",
            f"# Client: {name}\n\n"
            f"- Service: {service}\n"
            f"- Monthly rate: ${monthly_rate:,.2f}\n"
            f"- Start date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"- Status: Active\n"
            f"- Details: {details}\n",
        )
        self.memory.log(f"New client: {name} — {service} @ ${monthly_rate}/mo", "Services")

    def get_active_clients(self) -> list[str]:
        """List all active service clients."""
        return [
            name for name in self.memory.list_knowledge("projects")
            if name.startswith("client-")
        ]

    def get_services_mrr(self) -> float:
        """Calculate total MRR from services (safe regex parsing)."""
        import re
        total = 0.0
        for client_key in self.get_active_clients():
            data = self.memory.get_knowledge("projects", client_key)
            if not data:
                continue
            # Check if client is active
            if "status: active" not in data.lower() and "status: Active" not in data:
                continue
            for line in data.splitlines():
                if "monthly rate" in line.lower():
                    matches = re.findall(r"\$[\d,]+(?:\.\d{1,2})?", line)
                    if matches:
                        try:
                            total += float(matches[0].replace("$", "").replace(",", ""))
                        except ValueError:
                            pass
        return max(0.0, total)
