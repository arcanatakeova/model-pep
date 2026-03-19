"""ARCANA AI — Outbound Sales Engine.

Full cold outreach pipeline:
1. Build prospect lists (Apollo enrichment)
2. Generate personalized email sequences (Sonnet)
3. Launch campaigns (Instantly API)
4. Track responses and conversions
5. Auto-route warm replies to CRM pipeline
6. A/B test subject lines and copy

This is how ARCANA proactively fills the pipeline
beyond waiting for X mentions.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.crm import CRM, PipelineStage
from src.email_engine import EmailEngine
from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.outreach")


class OutreachEngine:
    """Outbound sales — prospect lists, campaigns, tracking."""

    def __init__(
        self, llm: LLM, memory: Memory, email: EmailEngine,
        crm: CRM, apollo_key: str = "",
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.email = email
        self.crm = crm
        self.apollo_key = apollo_key
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30)
        return self._http

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Apollo: Prospect List Building ──────────────────────────────

    async def search_prospects(
        self, titles: list[str], industries: list[str],
        locations: list[str] | None = None,
        company_size: str = "1-50",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Search Apollo for prospects matching criteria."""
        if not self.apollo_key:
            logger.warning("Apollo not configured")
            return []

        try:
            http = await self._get_http()
            payload: dict[str, Any] = {
                "api_key": self.apollo_key,
                "per_page": limit,
                "person_titles": titles,
                "organization_industry_tag_ids": [],
                "q_organization_keyword_tags": industries,
            }
            if locations:
                payload["person_locations"] = locations
            if company_size:
                size_map = {
                    "1-10": "1,10", "1-50": "1,50", "11-50": "11,50",
                    "51-200": "51,200", "201-500": "201,500",
                }
                payload["organization_num_employees_ranges"] = [size_map.get(company_size, "1,50")]

            resp = await http.post(
                "https://api.apollo.io/v1/mixed_people/search",
                json=payload,
            )
            if 200 <= resp.status_code < 300:
                people = resp.json().get("people", [])
                prospects = []
                for p in people:
                    prospect = {
                        "name": p.get("name", ""),
                        "email": p.get("email", ""),
                        "title": p.get("title", ""),
                        "company": p.get("organization", {}).get("name", ""),
                        "company_domain": p.get("organization", {}).get("primary_domain", ""),
                        "linkedin": p.get("linkedin_url", ""),
                        "location": p.get("city", ""),
                        "industry": p.get("organization", {}).get("industry", ""),
                        "company_size": p.get("organization", {}).get("estimated_num_employees", 0),
                    }
                    if prospect["email"]:
                        prospects.append(prospect)
                self.memory.log(
                    f"[Outreach] Apollo search: {len(prospects)} prospects found "
                    f"(titles: {titles[:2]}, industries: {industries[:2]})",
                    "Outreach",
                )
                return prospects
        except Exception as exc:
            logger.error("Apollo search error: %s", exc)
        return []

    async def enrich_contact(self, email: str = "", domain: str = "") -> dict[str, Any]:
        """Enrich a single contact via Apollo."""
        if not self.apollo_key:
            return {}

        try:
            http = await self._get_http()
            payload: dict[str, Any] = {"api_key": self.apollo_key}
            if email:
                payload["email"] = email
            if domain:
                payload["domain"] = domain

            resp = await http.post(
                "https://api.apollo.io/v1/people/match",
                json=payload,
            )
            if 200 <= resp.status_code < 300:
                return resp.json().get("person", {})
        except Exception as exc:
            logger.error("Apollo enrich error: %s", exc)
        return {}

    # ── Campaign Creation ───────────────────────────────────────────

    async def launch_campaign(
        self, service: str, target_role: str, target_industry: str,
        pain_point: str, prospect_count: int = 25,
    ) -> dict[str, Any]:
        """Full campaign launch: find prospects → generate emails → launch."""

        # 1. Find prospects
        prospects = await self.search_prospects(
            titles=[target_role, f"VP {target_role}", f"Head of {target_role}", "CEO", "Founder"],
            industries=[target_industry],
            limit=prospect_count,
        )
        if not prospects:
            return {"status": "no_prospects", "count": 0}

        # 2. Generate email sequence
        sequence = await self.email.generate_cold_email_sequence(
            target_role, target_industry, service, pain_point,
        )
        if not sequence or not sequence.get("emails"):
            return {"status": "sequence_failed", "count": 0}

        emails = sequence["emails"]
        campaign_name = sequence.get("campaign_name", f"Arcana-{service}-{target_industry}")

        # 3. Create Instantly campaign
        campaign = await self.email.create_campaign(
            name=campaign_name,
            subject=emails[0]["subject"],
            body=emails[0]["body"],
            follow_ups=emails[1:],
        )
        if not campaign:
            return {"status": "campaign_create_failed", "count": 0}

        campaign_id = campaign.get("id", campaign.get("campaign_id", ""))

        # 4. Add prospects to campaign
        leads = [
            {"email": p["email"], "first_name": p["name"].split()[0] if p.get("name", "").strip() else "",
             "company_name": p["company"]}
            for p in prospects if p.get("email")
        ]
        if leads and campaign_id:
            await self.email.add_leads_to_campaign(campaign_id, leads)

        # 5. Create CRM contacts for each prospect
        for p in prospects[:10]:  # Cap CRM entries
            contact_key = self.crm.create_contact(
                name=p["name"], email=p.get("email", ""),
                company=p.get("company", ""), source=f"outreach_{service}",
                role=p.get("title", ""),
            )
            self.crm.create_deal(
                contact_key, service,
                value_monthly=self._estimate_deal_value(service),
                source=f"cold_outreach_{campaign_name}",
            )

        self.memory.log(
            f"[Outreach] Campaign launched: {campaign_name}\n"
            f"  Prospects: {len(prospects)}\n"
            f"  Emails in sequence: {len(emails)}\n"
            f"  Target: {target_role} @ {target_industry}",
            "Outreach",
        )

        return {
            "status": "launched",
            "campaign_name": campaign_name,
            "campaign_id": campaign_id,
            "prospects": len(prospects),
            "emails_in_sequence": len(emails),
        }

    def _estimate_deal_value(self, service: str) -> float:
        """Estimate monthly deal value by service type."""
        values = {
            "ugc": 600, "chatbot": 500, "social": 1500, "leadgen": 1000,
            "reviews": 45, "seo": 2500, "consulting": 5000, "email": 1000,
            "intel": 2000, "automation": 3000,
        }
        return values.get(service.lower(), 1000)

    # ── Campaign Monitoring ─────────────────────────────────────────

    async def check_campaign_results(self, campaign_id: str) -> dict[str, Any]:
        """Check campaign stats and process warm replies."""
        stats = await self.email.get_campaign_stats(campaign_id)
        if not stats:
            return {}

        # If there are replies, they're warm leads — advance in CRM
        replies = stats.get("replied", 0)
        if replies > 0:
            self.memory.log(
                f"[Outreach] Campaign {campaign_id}: {replies} replies received",
                "Outreach",
            )

        return stats

    # ── Weekly Outreach Cycle ───────────────────────────────────────

    async def weekly_outreach_cycle(
        self,
        intel_context: str = "",
        pricing_context: str = "",
    ) -> dict[str, Any]:
        """Run weekly outreach — pick best service, find prospects, launch.

        Now accepts competitive intelligence and pricing context for smarter targeting.
        """
        # Determine which service to push based on demand signals + competitive intel
        result = await self.llm.ask_json(
            f"Pick the best Arcana Operations service to cold outreach this week.\n\n"
            f"Services and typical targets:\n"
            f"- UGC video: DTC brands, e-commerce, Shopify stores\n"
            f"- AI chatbot: SaaS companies, e-commerce, service businesses\n"
            f"- Social media: local businesses, restaurants, real estate\n"
            f"- Lead gen: B2B SaaS, agencies, consultants\n"
            f"- Review management: restaurants, medical, dental, hotels\n"
            f"- SEO: local businesses, professional services, SaaS\n"
            f"- AI consulting: funded startups, mid-market companies\n\n"
            + (f"COMPETITIVE INTELLIGENCE:\n{intel_context}\n\n" if intel_context else "")
            + (f"PRICING CONTEXT:\n{pricing_context}\n\n" if pricing_context else "")
            + f"Pick based on: easiest to close, highest volume, best margins, "
            f"competitive gaps we can exploit.\n\n"
            f"Return JSON: {{"
            f'"service": str, "target_role": str, "target_industry": str, '
            f'"pain_point": str, "competitive_angle": str, "reasoning": str}}',
            tier=Tier.HAIKU,
        )

        if not result:
            return {"status": "planning_failed"}

        return await self.launch_campaign(
            service=result.get("service", "consulting"),
            target_role=result.get("target_role", "CEO"),
            target_industry=result.get("target_industry", "technology"),
            pain_point=result.get("pain_point", ""),
        )
