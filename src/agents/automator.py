"""ARCANA AI — Automator Agent
Builds micro-SaaS tools, manages digital products, handles Stripe payments,
runs affiliate tracking, manages Gumroad products, and handles lead pipeline.

Revenue channels managed:
- Micro-SaaS tools ($2K MRR each)
- Digital products on Gumroad ($29-299)
- Affiliate marketing (MEXC 80%, Bybit 50%, Coinbase referrals)
- Lead qualification pipeline (X DMs → Supabase CRM → Discord)
- Stripe payment processing
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

from src.config import ArcanaConfig
from src.utils.db import log_action
from src.utils.llm import LLMClient, ModelTier
from src.utils.memory import MemorySystem

logger = logging.getLogger("arcana.automator")


class Product(BaseModel):
    name: str
    type: str  # "digital", "saas", "course"
    platform: str  # "gumroad", "stripe", "teachable"
    price_usd: float
    description: str = ""
    url: str | None = None


class Lead(BaseModel):
    handle: str
    source: str = "x"
    industry: str = ""
    stated_need: str = ""
    qualification_score: float = 0
    status: str = "new"


class Automator:
    """Revenue automation and product management agent."""

    def __init__(
        self,
        config: ArcanaConfig,
        llm: LLMClient,
        db: Any,
        memory: MemorySystem,
    ) -> None:
        self.config = config
        self.llm = llm
        self.db = db
        self.memory = memory
        self._client = httpx.AsyncClient(timeout=60.0)

    async def get_available_actions(self) -> list:
        """Return available automator actions."""
        from src.orchestrator import Action
        actions = []

        # Lead qualification (ALWAYS highest priority per CLAUDE.md)
        new_leads = await self.db.table("leads").select("*", count="exact").eq("status", "new").execute()
        if new_leads.count and new_leads.count > 0:
            actions.append(
                Action(
                    agent="automator",
                    name="qualify_leads",
                    description=f"Qualify {new_leads.count} new consulting leads — HIGHEST PRIORITY",
                    expected_revenue=5000,  # Potential $5K contract
                    probability=0.1,
                    time_hours=0.25,
                    risk=1.0,
                    params={"lead_ids": [l["id"] for l in (new_leads.data or [])]},
                )
            )

        # Check product sales
        actions.append(
            Action(
                agent="automator",
                name="check_revenue",
                description="Check Gumroad/Stripe for new sales and update metrics",
                expected_revenue=10,
                probability=0.5,
                time_hours=0.02,
                risk=1.0,
            )
        )

        # Affiliate link monitoring
        actions.append(
            Action(
                agent="automator",
                name="update_affiliates",
                description="Update affiliate click/conversion tracking",
                expected_revenue=5,
                probability=0.3,
                time_hours=0.02,
                risk=1.0,
            )
        )

        return actions

    async def execute_action(self, action: Any) -> dict[str, Any]:
        """Execute an automator action."""
        if action.name == "qualify_leads":
            return await self._qualify_leads(action.params.get("lead_ids", []))
        elif action.name == "check_revenue":
            return await self._check_revenue()
        elif action.name == "update_affiliates":
            return await self._update_affiliates()
        return {"status": "unknown_action"}

    async def _qualify_leads(self, lead_ids: list[str]) -> dict[str, Any]:
        """Qualify consulting leads using LLM analysis."""
        qualified = 0
        for lead_id in lead_ids:
            try:
                result = await self.db.table("leads").select("*").eq("id", lead_id).single().execute()
                if not result.data:
                    continue

                lead_data = result.data
                prompt = (
                    f"Qualify this potential consulting lead for Arcana Operations.\n\n"
                    f"Handle: @{lead_data.get('handle', 'unknown')}\n"
                    f"Source: {lead_data.get('source', 'x')}\n"
                    f"Stated need: {lead_data.get('stated_need', 'N/A')}\n\n"
                    f"Arcana Operations services:\n"
                    f"- AI agent development ($3-10K/mo)\n"
                    f"- Business strategy ($2-8K/mo)\n"
                    f"- SEO ($1.5-5K/mo)\n"
                    f"- Marketing ($2-6K/mo)\n"
                    f"- Fulfillment ($1-4K/mo)\n"
                    f"- Operational management ($2-6K/mo)\n\n"
                    f"Score 0-100 on: budget likelihood, urgency, fit with services, conversion probability.\n"
                    f"Return JSON: {{\"score\": int, \"recommended_service\": str, "
                    f"\"estimated_value\": float, \"priority\": \"high\"|\"medium\"|\"low\", "
                    f"\"suggested_response\": str}}"
                )

                analysis = await self.llm.complete_json(prompt, tier=ModelTier.SONNET)

                score = analysis.get("score", 0)
                status = "qualified" if score >= 50 else "dead"

                await self.db.table("leads").update({
                    "qualification_score": score,
                    "status": status,
                    "industry": analysis.get("recommended_service", ""),
                    "routed_to": "ian" if score >= 70 else "tan" if score >= 50 else None,
                    "notes": analysis.get("suggested_response", ""),
                }).eq("id", lead_id).execute()

                if score >= 50:
                    qualified += 1
                    # Notify immediately for qualified leads
                    from src.utils.notify import Notifier, AlertLevel
                    notifier = Notifier(self.config)
                    await notifier.lead_alert(
                        handle=lead_data.get("handle", "unknown"),
                        industry=analysis.get("recommended_service", "general"),
                        need=lead_data.get("stated_need", "N/A")[:100],
                        score=score,
                    )
                    await notifier.close()

                    # Store in memory for learning
                    await self.memory.store(
                        f"Lead qualified: @{lead_data.get('handle')} — "
                        f"Score: {score}, Service: {analysis.get('recommended_service')}, "
                        f"Est. value: ${analysis.get('estimated_value', 0)}",
                        category="lead_interaction",
                        importance_score=min(1.0, score / 100),
                    )

            except Exception as exc:
                logger.error("Failed to qualify lead %s: %s", lead_id, exc)

        await log_action(
            self.db, "automator", "qualify_leads",
            details={"processed": len(lead_ids), "qualified": qualified},
        )

        return {"status": "processed", "leads_processed": len(lead_ids), "qualified": qualified}

    async def _check_revenue(self) -> dict[str, Any]:
        """Check Gumroad and Stripe for new sales."""
        total_revenue = 0.0
        details: dict[str, Any] = {}

        # Check Gumroad
        if self.config.payments.gumroad_access_token:
            try:
                resp = await self._client.get(
                    "https://api.gumroad.com/v2/sales",
                    params={"access_token": self.config.payments.gumroad_access_token},
                )
                resp.raise_for_status()
                sales = resp.json().get("sales", [])
                gumroad_revenue = sum(float(s.get("price", 0)) / 100 for s in sales)
                total_revenue += gumroad_revenue
                details["gumroad_sales"] = len(sales)
                details["gumroad_revenue"] = gumroad_revenue
            except Exception as exc:
                logger.error("Gumroad check failed: %s", exc)

        # Check Stripe
        if self.config.payments.stripe_secret_key:
            try:
                resp = await self._client.get(
                    "https://api.stripe.com/v1/charges",
                    params={"limit": 10},
                    headers={"Authorization": f"Bearer {self.config.payments.stripe_secret_key}"},
                )
                resp.raise_for_status()
                charges = resp.json().get("data", [])
                stripe_revenue = sum(c.get("amount", 0) / 100 for c in charges if c.get("paid"))
                total_revenue += stripe_revenue
                details["stripe_charges"] = len(charges)
                details["stripe_revenue"] = stripe_revenue
            except Exception as exc:
                logger.error("Stripe check failed: %s", exc)

        await log_action(
            self.db, "automator", "check_revenue",
            details=details, revenue_usd=total_revenue,
        )

        return {"status": "checked", "total_revenue": total_revenue, **details}

    async def _update_affiliates(self) -> dict[str, Any]:
        """Update affiliate tracking data."""
        # This would integrate with various affiliate APIs
        # For now, log that the check was performed
        await log_action(self.db, "automator", "update_affiliates")
        return {"status": "checked"}

    async def create_gumroad_product(self, product: Product) -> dict[str, Any]:
        """Create a new digital product on Gumroad."""
        if not self.config.payments.gumroad_access_token:
            return {"status": "error", "message": "Gumroad token not configured"}

        try:
            resp = await self._client.post(
                "https://api.gumroad.com/v2/products",
                data={
                    "access_token": self.config.payments.gumroad_access_token,
                    "name": product.name,
                    "price": int(product.price_usd * 100),
                    "description": product.description,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            # Store in database
            await self.db.table("products").insert({
                "name": product.name,
                "type": product.type,
                "platform": "gumroad",
                "price_usd": product.price_usd,
                "url": data.get("product", {}).get("short_url"),
            }).execute()

            logger.info("Created Gumroad product: %s ($%.2f)", product.name, product.price_usd)
            return {"status": "created", "url": data.get("product", {}).get("short_url")}

        except Exception as exc:
            logger.error("Failed to create Gumroad product: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def generate_product_idea(self) -> dict[str, Any]:
        """Use LLM to generate a new micro-SaaS or digital product idea."""
        context = await self.memory.recall_context(
            "audience demand product request tool need",
            category="content_performance",
        )

        prompt = (
            f"Based on ARCANA AI's audience data and market trends, suggest a micro-SaaS tool "
            f"or digital product to build.\n\n"
            f"Context from audience interactions:\n{context}\n\n"
            f"Criteria:\n"
            f"- Can be built in 1-2 days with Claude Code\n"
            f"- Target $2K MRR\n"
            f"- Solves a real problem for the target audience\n"
            f"- Can be deployed on Vercel with Stripe\n\n"
            f"Return JSON: {{\"name\": str, \"description\": str, \"target_audience\": str, "
            f"\"price\": float, \"estimated_mrr\": float, \"build_time_hours\": float, "
            f"\"tech_stack\": [str]}}"
        )

        return await self.llm.complete_json(prompt, tier=ModelTier.OPUS)
