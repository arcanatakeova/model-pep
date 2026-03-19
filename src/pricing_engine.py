"""ARCANA AI — Dynamic Pricing Engine.

Intelligent pricing for all of Arcana Operations' services and products.

Capabilities:
1. Dynamic pricing based on client size, industry, and scope
2. Competitive pricing analysis
3. Value-based pricing (ROI → price = % of ROI)
4. Package builder (Basic / Pro / Enterprise)
5. Discount logic (volume, annual, referral)
6. A/B price testing
7. Revenue optimization from conversion data
8. Instant quote generation
9. Pricing page content generation
10. Custom proposal pricing with scope-based estimates
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.pricing")


# ── Data Models ──────────────────────────────────────────────────────────


class ClientSize(str, Enum):
    SOLO = "solo"             # 1 person
    SMALL = "small"           # 2-10 employees
    MEDIUM = "medium"         # 11-50 employees
    LARGE = "large"           # 51-200 employees
    ENTERPRISE = "enterprise" # 200+ employees


class PackageTier(str, Enum):
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class ServicePricing:
    """Canonical pricing data for a single service."""
    service_name: str
    base_price: float                     # Monthly base price
    setup_fee: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    price_unit: str = "month"             # month | project | unit
    description: str = ""


@dataclass
class DiscountRule:
    """A single discount that can be applied to a quote."""
    name: str
    percent: float            # 0-100
    reason: str = ""


@dataclass
class Quote:
    """An itemised price quote for a prospect."""
    prospect_name: str
    prospect_company: str
    services: list[dict[str, Any]] = field(default_factory=list)
    subtotal: float = 0.0
    discounts: list[DiscountRule] = field(default_factory=list)
    total: float = 0.0
    currency: str = "USD"
    valid_until: str = ""
    notes: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prospect_name": self.prospect_name,
            "prospect_company": self.prospect_company,
            "services": self.services,
            "subtotal": self.subtotal,
            "discounts": [
                {"name": d.name, "percent": d.percent, "reason": d.reason}
                for d in self.discounts
            ],
            "total": self.total,
            "currency": self.currency,
            "valid_until": self.valid_until,
            "notes": self.notes,
            "created_at": self.created_at,
        }


# ── Pricing Catalogue ───────────────────────────────────────────────────

SERVICE_CATALOGUE: dict[str, ServicePricing] = {
    "ai_chatbot": ServicePricing(
        service_name="White-Label AI Chatbot",
        base_price=500.0,
        setup_fee=1000.0,
        min_price=300.0,
        max_price=1000.0,
        description="Custom AI chatbot deployed on client's website.",
    ),
    "review_response": ServicePricing(
        service_name="Review Response Service",
        base_price=49.0,
        min_price=29.0,
        max_price=59.0,
        description="Automated, AI-crafted responses to Google/Yelp reviews.",
    ),
    "social_media": ServicePricing(
        service_name="Social Media Management",
        base_price=1500.0,
        setup_fee=500.0,
        min_price=500.0,
        max_price=5000.0,
        description="AI-powered content creation and scheduling across platforms.",
    ),
    "lead_gen": ServicePricing(
        service_name="Lead Generation as a Service",
        base_price=1000.0,
        setup_fee=500.0,
        min_price=500.0,
        max_price=2000.0,
        description="Targeted B2B lead scraping, enrichment, scoring, and delivery.",
    ),
    "email_outreach": ServicePricing(
        service_name="Cold Email Outreach",
        base_price=1000.0,
        setup_fee=500.0,
        min_price=500.0,
        max_price=2000.0,
        description="AI-personalised cold email campaigns at scale.",
    ),
    "competitive_intel": ServicePricing(
        service_name="Competitive Intelligence",
        base_price=1500.0,
        setup_fee=500.0,
        min_price=500.0,
        max_price=5000.0,
        description="Automated monitoring and analysis of competitor activity.",
    ),
    "ai_agent_build": ServicePricing(
        service_name="Custom AI Agent Build",
        base_price=500.0,
        setup_fee=3000.0,
        min_price=500.0,
        max_price=5000.0,
        price_unit="project + month",
        description="Bespoke autonomous AI agent, built and maintained by ARCANA.",
    ),
    "seo_audit": ServicePricing(
        service_name="SEO Audit & Strategy",
        base_price=0.0,
        setup_fee=1500.0,
        min_price=1000.0,
        max_price=3000.0,
        price_unit="project",
        description="Full technical + content SEO audit with actionable roadmap.",
    ),
    "marketing_strategy": ServicePricing(
        service_name="AI Marketing Strategy",
        base_price=0.0,
        setup_fee=2000.0,
        min_price=1500.0,
        max_price=5000.0,
        price_unit="project",
        description="End-to-end AI-first marketing strategy for your business.",
    ),
    "playbook": ServicePricing(
        service_name="How to Work with AI Playbook",
        base_price=39.0,
        min_price=29.0,
        max_price=49.0,
        price_unit="unit",
        description="PDF guide on building and managing AI systems for your business.",
    ),
}


# ── Industry & Size Multipliers ─────────────────────────────────────────

INDUSTRY_MULTIPLIERS: dict[str, float] = {
    "finance": 1.40,
    "healthcare": 1.35,
    "legal": 1.30,
    "saas": 1.20,
    "ecommerce": 1.10,
    "real_estate": 1.15,
    "construction": 1.05,
    "restaurant": 0.90,
    "retail": 0.95,
    "nonprofit": 0.80,
    "education": 0.85,
    "default": 1.00,
}

SIZE_MULTIPLIERS: dict[ClientSize, float] = {
    ClientSize.SOLO: 0.70,
    ClientSize.SMALL: 0.85,
    ClientSize.MEDIUM: 1.00,
    ClientSize.LARGE: 1.30,
    ClientSize.ENTERPRISE: 1.80,
}

# Scope complexity: maps a 1-5 complexity rating to a multiplier
SCOPE_MULTIPLIERS: dict[int, float] = {
    1: 0.80,
    2: 0.90,
    3: 1.00,
    4: 1.25,
    5: 1.60,
}


# ── Pricing Engine ───────────────────────────────────────────────────────


class PricingEngine:
    """Dynamic, intelligent pricing for all Arcana Operations services."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    # ── 1. Dynamic Price Calculation ─────────────────────────────────

    def calculate_price(
        self,
        service: str,
        client_size: ClientSize = ClientSize.MEDIUM,
        industry: str = "default",
        scope: int = 3,
    ) -> dict[str, Any]:
        """Calculate a price for *service* adjusted by client size, industry, and scope.

        Args:
            service: Key from SERVICE_CATALOGUE.
            client_size: ClientSize enum value.
            industry: Industry key (see INDUSTRY_MULTIPLIERS).
            scope: Complexity from 1 (simple) to 5 (complex).

        Returns:
            Dict with monthly, setup, and effective prices plus multiplier breakdown.
        """
        if service not in SERVICE_CATALOGUE:
            return {"error": f"Unknown service: {service}"}

        svc = SERVICE_CATALOGUE[service]
        ind_mult = INDUSTRY_MULTIPLIERS.get(industry.lower(), INDUSTRY_MULTIPLIERS["default"])
        size_mult = SIZE_MULTIPLIERS.get(client_size, 1.0)
        scope_mult = SCOPE_MULTIPLIERS.get(scope, 1.0)
        combined = ind_mult * size_mult * scope_mult

        monthly = round(svc.base_price * combined, 2)
        setup = round(svc.setup_fee * combined, 2)

        # Clamp to catalogue min/max (scaled by combined multiplier)
        if svc.min_price and monthly < svc.min_price:
            monthly = svc.min_price
        if svc.max_price and monthly > svc.max_price * SIZE_MULTIPLIERS.get(client_size, 1.0):
            monthly = round(svc.max_price * SIZE_MULTIPLIERS.get(client_size, 1.0), 2)

        return {
            "service": svc.service_name,
            "monthly_price": monthly,
            "setup_fee": setup,
            "price_unit": svc.price_unit,
            "multipliers": {
                "industry": ind_mult,
                "size": size_mult,
                "scope": scope_mult,
                "combined": round(combined, 3),
            },
            "base_price": svc.base_price,
        }

    # ── 2. Competitive Pricing Analysis ──────────────────────────────

    async def compare_to_competitors(self, service: str) -> dict[str, Any]:
        """Use LLM to generate a competitive pricing analysis for *service*."""
        svc = SERVICE_CATALOGUE.get(service)
        if not svc:
            return {"error": f"Unknown service: {service}"}

        result = await self.llm.ask_json(
            f"Provide a competitive pricing analysis for the following service:\n\n"
            f"Service: {svc.service_name}\n"
            f"Our price range: ${svc.min_price}-${svc.max_price}/mo "
            f"(+ ${svc.setup_fee} setup)\n"
            f"Description: {svc.description}\n\n"
            f"Research the market and provide:\n"
            f"1. Average market price range (low, mid, high)\n"
            f"2. 3-5 named competitors with approximate prices\n"
            f"3. Our positioning (underpriced / competitive / premium)\n"
            f"4. Recommendation (raise, lower, or hold)\n\n"
            f"Return JSON: {{"
            f'"market_range": {{"low": float, "mid": float, "high": float}}, '
            f'"competitors": [{{"name": str, "price": float, "notes": str}}], '
            f'"positioning": str, '
            f'"recommendation": str, '
            f'"reasoning": str}}',
            tier=Tier.SONNET,
        )
        self.memory.log(
            f"[Pricing] Competitive analysis for {svc.service_name}: "
            f"positioning={result.get('positioning', '?')}",
            "Pricing",
        )
        return result

    # ── 3. Value-Based / ROI Pricing ─────────────────────────────────

    async def calculate_client_roi(
        self, service: str, client_metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """Estimate the ROI a client would get and derive a value-based price.

        client_metrics example:
            {"monthly_revenue": 50000, "customer_count": 200,
             "avg_deal_size": 5000, "current_lead_conversion": 0.02}
        """
        svc = SERVICE_CATALOGUE.get(service)
        if not svc:
            return {"error": f"Unknown service: {service}"}

        result = await self.llm.ask_json(
            f"Calculate the ROI a client would get from this service, then "
            f"recommend a value-based price (price = 10-20% of Year-1 ROI).\n\n"
            f"Service: {svc.service_name}\n"
            f"Description: {svc.description}\n"
            f"Client metrics: {client_metrics}\n\n"
            f"Estimate:\n"
            f"- Expected improvement (e.g. leads +30%, time saved, etc.)\n"
            f"- Monetary value of that improvement per year\n"
            f"- Recommended monthly price (10-20% of annual ROI / 12)\n\n"
            f"Return JSON: {{"
            f'"expected_improvement": str, '
            f'"annual_roi_estimate": float, '
            f'"roi_multiple": float, '
            f'"recommended_monthly_price": float, '
            f'"recommended_setup_fee": float, '
            f'"reasoning": str}}',
            tier=Tier.SONNET,
        )
        self.memory.log(
            f"[Pricing] ROI calc for {svc.service_name}: "
            f"annual_roi=${result.get('annual_roi_estimate', 0):,.0f} → "
            f"${result.get('recommended_monthly_price', 0):,.0f}/mo",
            "Pricing",
        )
        return result

    # ── 4. Package Builder ───────────────────────────────────────────

    async def generate_packages(
        self, services: list[str], industry: str = "default"
    ) -> dict[str, Any]:
        """Combine multiple services into Basic / Pro / Enterprise packages.

        Args:
            services: List of service keys from SERVICE_CATALOGUE.
            industry: Industry for multiplier context.
        """
        service_details = []
        for s in services:
            svc = SERVICE_CATALOGUE.get(s)
            if svc:
                price = self.calculate_price(s, ClientSize.MEDIUM, industry)
                service_details.append({
                    "key": s,
                    "name": svc.service_name,
                    "monthly": price["monthly_price"],
                    "setup": price["setup_fee"],
                    "description": svc.description,
                })

        if not service_details:
            return {"error": "No valid services provided."}

        result = await self.llm.ask_json(
            f"Create three service packages (Basic, Pro, Enterprise) from these "
            f"individual services.\n\n"
            f"Available services:\n{service_details}\n\n"
            f"Rules:\n"
            f"- Basic: 1-2 services, ~20% discount vs a-la-carte\n"
            f"- Pro: 3-4 services, ~25% discount, most popular badge\n"
            f"- Enterprise: All services, ~30% discount, priority support\n"
            f"- Each tier should feel like a natural upgrade\n"
            f"- Include a catchy name for each tier (e.g. 'Starter', 'Growth', 'Scale')\n\n"
            f"Return JSON: {{"
            f'"packages": [{{'
            f'"tier": str, "name": str, "tagline": str, '
            f'"included_services": [str], '
            f'"monthly_price": float, "setup_fee": float, '
            f'"savings_vs_ala_carte": float, '
            f'"highlights": [str]'
            f"}}]}}",
            tier=Tier.SONNET,
        )
        self.memory.log(
            f"[Pricing] Generated packages from {len(service_details)} services",
            "Pricing",
        )
        return result

    # ── 5. Discount Logic ────────────────────────────────────────────

    def apply_discounts(
        self,
        base_price: float,
        *,
        annual_payment: bool = False,
        volume_units: int = 1,
        referral_code: str | None = None,
        promo_code: str | None = None,
    ) -> dict[str, Any]:
        """Stack applicable discounts onto a base price.

        Discounts are applied sequentially (multiplicative, not additive) to
        prevent giving away margin when multiple discounts stack.
        """
        discounts: list[DiscountRule] = []
        price = base_price

        # Annual payment: 15% off
        if annual_payment:
            d = DiscountRule("Annual Payment", 15.0, "Pay yearly, save 15%")
            discounts.append(d)
            price *= 1 - d.percent / 100

        # Volume: 5% per additional unit, capped at 25%
        if volume_units > 1:
            pct = min((volume_units - 1) * 5, 25)
            d = DiscountRule("Volume", float(pct), f"{volume_units} units")
            discounts.append(d)
            price *= 1 - d.percent / 100

        # Referral: flat 10%
        if referral_code:
            d = DiscountRule("Referral", 10.0, f"Code: {referral_code}")
            discounts.append(d)
            price *= 1 - d.percent / 100

        # Promo code: flat 10%
        if promo_code:
            d = DiscountRule("Promo", 10.0, f"Code: {promo_code}")
            discounts.append(d)
            price *= 1 - d.percent / 100

        total_discount_pct = round((1 - price / base_price) * 100, 2) if base_price else 0

        return {
            "original_price": base_price,
            "final_price": round(price, 2),
            "total_discount_percent": total_discount_pct,
            "discounts_applied": [
                {"name": d.name, "percent": d.percent, "reason": d.reason}
                for d in discounts
            ],
        }

    # ── 6. A/B Price Testing ─────────────────────────────────────────

    def ab_test_price(
        self,
        service: str,
        price_a: float,
        price_b: float,
        prospect_id: str = "",
    ) -> dict[str, Any]:
        """Deterministically assign a prospect to price A or B.

        Uses a hash of (service + prospect_id) so the same prospect always
        sees the same price, but the split is ~50/50 across all prospects.
        """
        from src.toolkit import fast_hash
        seed = fast_hash(f"{service}:{prospect_id}")
        bucket = "A" if int(seed, 16) % 2 == 0 else "B"
        shown_price = price_a if bucket == "A" else price_b

        self.memory.log(
            f"[Pricing:AB] {service} prospect={prospect_id or 'anon'} "
            f"bucket={bucket} price=${shown_price}",
            "Pricing",
        )
        return {
            "service": service,
            "bucket": bucket,
            "price_a": price_a,
            "price_b": price_b,
            "shown_price": shown_price,
            "prospect_id": prospect_id,
        }

    # ── 7. Revenue Optimization ──────────────────────────────────────

    async def optimize_pricing(
        self,
        service: str,
        current_price: float,
        conversion_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Analyse conversion data and recommend the revenue-maximising price.

        conversion_data example:
            {"impressions": 5000, "trials": 200, "paid": 40,
             "churn_rate": 0.05, "avg_ltv_months": 8,
             "price_history": [{"price": 49, "conversions": 50},
                               {"price": 79, "conversions": 30}]}
        """
        svc_name = SERVICE_CATALOGUE[service].service_name if service in SERVICE_CATALOGUE else service

        result = await self.llm.ask_json(
            f"You are a pricing strategist. Analyse the following data and "
            f"recommend the optimal price to maximise total monthly revenue.\n\n"
            f"Service: {svc_name}\n"
            f"Current price: ${current_price}\n"
            f"Conversion data: {conversion_data}\n\n"
            f"Consider:\n"
            f"- Price elasticity (how conversions change with price)\n"
            f"- Revenue = price * conversions\n"
            f"- Customer lifetime value at each price point\n"
            f"- Market positioning\n\n"
            f"Return JSON: {{"
            f'"current_monthly_revenue": float, '
            f'"optimal_price": float, '
            f'"projected_monthly_revenue": float, '
            f'"projected_conversion_rate": float, '
            f'"revenue_uplift_percent": float, '
            f'"confidence": str, '
            f'"reasoning": str}}',
            tier=Tier.SONNET,
        )
        self.memory.log(
            f"[Pricing] Optimisation for {svc_name}: "
            f"${current_price} → ${result.get('optimal_price', '?')} "
            f"(+{result.get('revenue_uplift_percent', 0):.1f}%)",
            "Pricing",
        )
        return result

    # ── 8. Quote Generation ──────────────────────────────────────────

    async def generate_quote(
        self,
        prospect_data: dict[str, Any],
    ) -> Quote:
        """Generate an itemised quote for a prospect.

        prospect_data example:
            {"name": "Jane", "company": "Acme Corp",
             "industry": "saas", "size": "medium",
             "services": ["ai_chatbot", "lead_gen"],
             "scope": 3, "annual_payment": True,
             "referral_code": "IAN10"}
        """
        name = prospect_data.get("name", "Prospect")
        company = prospect_data.get("company", "")
        industry = prospect_data.get("industry", "default")
        size = ClientSize(prospect_data.get("size", "medium"))
        scope = prospect_data.get("scope", 3)
        requested = prospect_data.get("services", [])
        annual = prospect_data.get("annual_payment", False)
        referral = prospect_data.get("referral_code")
        promo = prospect_data.get("promo_code")

        line_items: list[dict[str, Any]] = []
        subtotal_monthly = 0.0
        total_setup = 0.0

        for svc_key in requested:
            pricing = self.calculate_price(svc_key, size, industry, scope)
            if "error" in pricing:
                continue
            monthly = pricing["monthly_price"]
            setup = pricing["setup_fee"]
            subtotal_monthly += monthly
            total_setup += setup
            line_items.append({
                "service": pricing["service"],
                "monthly_price": monthly,
                "setup_fee": setup,
                "price_unit": pricing["price_unit"],
            })

        # Apply discounts to the monthly subtotal
        disc = self.apply_discounts(
            subtotal_monthly,
            annual_payment=annual,
            volume_units=len(requested),
            referral_code=referral,
            promo_code=promo,
        )

        # Build validity date (30 days)
        from datetime import timedelta
        valid = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")

        quote = Quote(
            prospect_name=name,
            prospect_company=company,
            services=line_items,
            subtotal=subtotal_monthly,
            discounts=[
                DiscountRule(d["name"], d["percent"], d["reason"])
                for d in disc["discounts_applied"]
            ],
            total=disc["final_price"],
            valid_until=valid,
            notes=f"Setup fees total: ${total_setup:,.2f} (one-time). "
                  f"Monthly price shown after discounts.",
        )

        self.memory.log(
            f"[Pricing] Quote for {name} ({company}): "
            f"{len(line_items)} services, ${quote.total:,.2f}/mo + "
            f"${total_setup:,.2f} setup",
            "Pricing",
        )
        return quote

    # ── 9. Pricing Page Content Generation ───────────────────────────

    async def generate_pricing_page(
        self,
        services: list[str] | None = None,
        style: str = "modern saas",
    ) -> dict[str, Any]:
        """Generate website pricing page content (headlines, descriptions,
        feature lists, CTAs) for the given services or all services."""
        if not services:
            services = list(SERVICE_CATALOGUE.keys())

        svc_info = []
        for s in services:
            svc = SERVICE_CATALOGUE.get(s)
            if svc:
                svc_info.append({
                    "name": svc.service_name,
                    "price_range": f"${svc.min_price}-${svc.max_price}",
                    "setup": f"${svc.setup_fee}",
                    "description": svc.description,
                })

        result = await self.llm.ask_json(
            f"Generate website pricing page content in a {style} style.\n\n"
            f"Services:\n{svc_info}\n\n"
            f"For each service produce:\n"
            f"- Headline (under 8 words)\n"
            f"- Sub-headline (1 sentence, benefit-focused)\n"
            f"- 4-6 feature bullet points\n"
            f"- CTA button text\n"
            f"- Social proof line (e.g. 'Trusted by 50+ businesses')\n\n"
            f"Also produce:\n"
            f"- Page hero headline\n"
            f"- Page hero sub-headline\n"
            f"- FAQ section (5 questions)\n\n"
            f"Return JSON: {{"
            f'"hero_headline": str, "hero_subheadline": str, '
            f'"services": [{{"name": str, "headline": str, "subheadline": str, '
            f'"features": [str], "cta": str, "social_proof": str}}], '
            f'"faqs": [{{"q": str, "a": str}}]}}',
            tier=Tier.SONNET,
        )
        self.memory.log(
            f"[Pricing] Generated pricing page content for {len(svc_info)} services",
            "Pricing",
        )
        return result

    # ── 10. Custom Proposal Pricing ──────────────────────────────────

    async def generate_proposal_pricing(
        self,
        prospect_name: str,
        company: str,
        scope_description: str,
        budget_range: str = "",
        timeline: str = "",
    ) -> dict[str, Any]:
        """Generate a scope-based pricing estimate for a custom proposal.

        This is for non-standard work that doesn't map neatly to catalogue
        services (e.g. a bespoke AI agent build with unusual integrations).
        """
        catalogue_summary = {
            k: {"name": v.service_name, "base": v.base_price, "setup": v.setup_fee}
            for k, v in SERVICE_CATALOGUE.items()
        }

        result = await self.llm.ask_json(
            f"Generate a custom project pricing estimate for a proposal.\n\n"
            f"Prospect: {prospect_name} at {company}\n"
            f"Scope: {scope_description}\n"
            f"Budget range (if stated): {budget_range or 'Not stated'}\n"
            f"Timeline: {timeline or 'Flexible'}\n\n"
            f"Our service catalogue for reference:\n{catalogue_summary}\n\n"
            f"Break the scope into phases and line items. For each:\n"
            f"- Description of deliverable\n"
            f"- Estimated hours\n"
            f"- Hourly rate ($150-250 depending on complexity)\n"
            f"- Line total\n\n"
            f"Also provide:\n"
            f"- Total project cost\n"
            f"- Suggested payment schedule (milestone-based)\n"
            f"- Optional add-ons with prices\n"
            f"- Ongoing monthly maintenance estimate\n\n"
            f"Return JSON: {{"
            f'"phases": [{{"name": str, "deliverables": [str], '
            f'"hours": float, "rate": float, "total": float}}], '
            f'"project_total": float, '
            f'"payment_schedule": [{{"milestone": str, "percent": float, "amount": float}}], '
            f'"add_ons": [{{"name": str, "price": float}}], '
            f'"monthly_maintenance": float, '
            f'"notes": str}}',
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[Pricing] Custom proposal for {prospect_name} ({company}): "
            f"${result.get('project_total', 0):,.0f} project + "
            f"${result.get('monthly_maintenance', 0):,.0f}/mo",
            "Pricing",
        )
        return result
