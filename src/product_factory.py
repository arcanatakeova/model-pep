"""ARCANA AI — Autonomous Product Factory.

Creates and lists digital products without human involvement:
1. Identify demand signals from audience/scanner
2. Generate product content (guides, templates, prompts, courses)
3. Package as PDF / Notion template / ZIP
4. List on Gumroad with copy and pricing
5. Create checkout links on Stripe
6. Announce on X + newsletter
7. Deliver to purchasers automatically

Products ARCANA can create:
- PDF guides ($29-99)
- Prompt libraries ($19-49)
- Template packs ($29-79)
- Automation playbooks ($49-149)
- Video courses ($99-299)
- Service packages (custom priced)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory
from src.payments import PaymentsEngine

logger = logging.getLogger("arcana.products")


class ProductFactory:
    """Autonomously create, list, and sell digital products."""

    def __init__(self, llm: LLM, memory: Memory, payments: PaymentsEngine) -> None:
        self.llm = llm
        self.memory = memory
        self.payments = payments

    # ── Demand Signal Analysis ──────────────────────────────────────

    async def identify_product_opportunity(self) -> dict[str, Any]:
        """Analyze audience signals to identify what product to build next."""
        # Read recent scanner data, content performance, and audience feedback
        recent_notes = "\n".join(
            notes[:200] for _, notes in self.memory.get_recent_days(7)
        )

        result = await self.llm.ask_json(
            f"Based on recent activity, what digital product should ARCANA create next?\n\n"
            f"Recent activity:\n{recent_notes[:2000]}\n\n"
            f"Product types ARCANA can create autonomously:\n"
            f"- PDF guide ($29-99) — expert knowledge packaged as a downloadable\n"
            f"- Prompt library ($19-49) — curated prompts for specific use cases\n"
            f"- Template pack ($29-79) — ready-to-use business templates\n"
            f"- Automation playbook ($49-149) — step-by-step automation guides\n"
            f"- Mini-course ($99-299) — text/video lessons on specific topic\n\n"
            f"Consider: What are people asking about most? What problems keep coming up?\n"
            f"What would demonstrate Arcana Operations' expertise?\n\n"
            f"Return JSON: {{"
            f'"product_name": str, "product_type": str, "price_cents": int, '
            f'"description": str, "target_audience": str, '
            f'"key_topics": [str], "estimated_creation_hours": int, '
            f'"revenue_potential_monthly": int, "reasoning": str}}',
            tier=Tier.SONNET,
        )
        return result

    # ── Product Content Generation ──────────────────────────────────

    async def generate_guide(
        self, title: str, topics: list[str], target_audience: str, depth: str = "comprehensive",
    ) -> dict[str, Any]:
        """Generate a full PDF guide / playbook."""
        result = await self.llm.ask_json(
            f"Generate a complete digital guide for ARCANA AI to sell.\n\n"
            f"Title: {title}\n"
            f"Topics to cover: {', '.join(topics)}\n"
            f"Target audience: {target_audience}\n"
            f"Depth: {depth}\n\n"
            f"Generate:\n"
            f"- Table of contents (6-10 chapters)\n"
            f"- Full content for each chapter (500-1000 words each)\n"
            f"- Actionable takeaways for each chapter\n"
            f"- Case studies or examples where relevant\n"
            f"- Summary and next steps\n\n"
            f"Write in ARCANA's voice — confident, pattern-focused, practical.\n"
            f"No filler, no fluff. Every sentence earns its place.\n\n"
            f"Return JSON: {{"
            f'"title": str, '
            f'"subtitle": str, '
            f'"chapters": [{{"title": str, "content": str, "takeaways": [str]}}], '
            f'"total_word_count": int}}',
            tier=Tier.OPUS,
        )
        return result

    async def generate_prompt_library(
        self, niche: str, count: int = 50,
    ) -> dict[str, Any]:
        """Generate a curated prompt library product."""
        result = await self.llm.ask_json(
            f"Generate a prompt library product for ARCANA AI to sell.\n\n"
            f"Niche: {niche}\n"
            f"Prompt count: {count}\n\n"
            f"Categories to include:\n"
            f"- Business strategy prompts\n"
            f"- Content creation prompts\n"
            f"- Sales and outreach prompts\n"
            f"- Customer support prompts\n"
            f"- Analysis and research prompts\n"
            f"- Automation design prompts\n\n"
            f"Each prompt should be:\n"
            f"- Battle-tested (ARCANA actually uses these)\n"
            f"- Specific (not generic 'write me a...' prompts)\n"
            f"- Include variables/placeholders\n"
            f"- Include expected output description\n\n"
            f"Return JSON: {{"
            f'"title": str, '
            f'"categories": [{{"name": str, "prompts": [{{"title": str, "prompt": str, "use_case": str}}]}}], '
            f'"total_prompts": int}}',
            tier=Tier.SONNET,
        )
        return result

    async def generate_template_pack(
        self, niche: str, template_types: list[str],
    ) -> dict[str, Any]:
        """Generate a business template pack."""
        result = await self.llm.ask_json(
            f"Generate a business template pack for ARCANA AI to sell.\n\n"
            f"Niche: {niche}\n"
            f"Template types: {', '.join(template_types)}\n\n"
            f"For each template, provide:\n"
            f"- Full template content (markdown format)\n"
            f"- Instructions for use\n"
            f"- Customization guide\n\n"
            f"Return JSON: {{"
            f'"title": str, '
            f'"templates": [{{"name": str, "type": str, "content": str, "instructions": str}}], '
            f'"total_templates": int}}',
            tier=Tier.SONNET,
        )
        return result

    # ── Product Listing ─────────────────────────────────────────────

    async def list_product(
        self, name: str, description: str, price_cents: int,
        product_type: str = "guide",
    ) -> dict[str, Any]:
        """List a product on Gumroad + create Stripe checkout link."""
        results: dict[str, Any] = {"name": name, "price": price_cents / 100}

        # Generate product copy
        copy = await self.llm.ask_json(
            f"Generate product listing copy for Gumroad.\n\n"
            f"Product: {name}\n"
            f"Type: {product_type}\n"
            f"Price: ${price_cents/100:.0f}\n"
            f"Description: {description}\n\n"
            f"Return JSON: {{"
            f'"title": str, "subtitle": str, '
            f'"description_html": str (rich description with formatting), '
            f'"bullet_points": [str] (5 key benefits), '
            f'"cta_text": str}}',
            tier=Tier.HAIKU,
            max_tokens=400,
        )

        # List on Gumroad
        gumroad_product = await self.payments.create_gumroad_product(
            name=name,
            description=copy.get("description_html", description),
            price_cents=price_cents,
        )
        if gumroad_product:
            results["gumroad_url"] = gumroad_product.get("short_url")
            results["gumroad_id"] = gumroad_product.get("id")

        # Create Stripe checkout link
        stripe_url = self.payments.create_checkout_link(name, price_cents)
        if stripe_url:
            results["stripe_url"] = stripe_url

        # Save to memory
        self.memory.save_knowledge(
            "projects", f"product-{name[:30].lower().replace(' ', '-')}",
            f"# Product: {name}\n\n"
            f"- Type: {product_type}\n"
            f"- Price: ${price_cents/100:.0f}\n"
            f"- Gumroad: {results.get('gumroad_url', 'N/A')}\n"
            f"- Stripe: {results.get('stripe_url', 'N/A')}\n"
            f"- Listed: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"- Copy: {copy.get('subtitle', '')}\n",
        )

        self.memory.log(
            f"[Product] Listed: {name} @ ${price_cents/100:.0f} "
            f"(Gumroad: {'YES' if gumroad_product else 'NO'}, "
            f"Stripe: {'YES' if stripe_url else 'NO'})",
            "Products",
        )

        results["copy"] = copy
        return results

    # ── Full Pipeline: Identify → Create → List → Announce ──────────

    async def create_and_list_product(self) -> dict[str, Any]:
        """Full autonomous product creation pipeline."""
        # 1. Identify what to build
        opportunity = await self.identify_product_opportunity()
        if not opportunity:
            return {"status": "no_opportunity"}

        name = opportunity.get("product_name", "Arcana Guide")
        product_type = opportunity.get("product_type", "guide")
        price = opportunity.get("price_cents", 4900)
        topics = opportunity.get("key_topics", [])
        audience = opportunity.get("target_audience", "business owners")

        # 2. Generate content based on type
        if product_type in ("guide", "playbook"):
            content = await self.generate_guide(name, topics, audience)
        elif product_type == "prompt_library":
            content = await self.generate_prompt_library(audience)
        elif product_type == "template_pack":
            content = await self.generate_template_pack(audience, topics)
        else:
            content = await self.generate_guide(name, topics, audience)

        # 3. List on Gumroad + Stripe
        listing = await self.list_product(
            name, opportunity.get("description", ""), price, product_type,
        )

        # 4. Generate launch tweet
        launch_tweet = await self.llm.ask(
            f"Write a product launch tweet for ARCANA AI.\n\n"
            f"Product: {name} (${price/100:.0f})\n"
            f"Type: {product_type}\n"
            f"Audience: {audience}\n\n"
            f"Rules: Under 280 chars, ARCANA voice, create urgency.\n"
            f"No link in main tweet (goes in reply).",
            tier=Tier.HAIKU, max_tokens=100,
        )

        listing["content"] = content
        listing["launch_tweet"] = launch_tweet.strip()
        listing["status"] = "created"

        self.memory.log(
            f"[Product] Full pipeline complete: {name} @ ${price/100:.0f}", "Products"
        )

        return listing
