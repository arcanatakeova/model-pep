"""ARCANA AI — Autonomous Product Factory.

Creates and lists digital products without human involvement:
1. Identify demand signals from audience/scanner
2. Generate product content (guides, templates, prompts, courses)
3. Package as professional PDF with QR codes
4. List on Gumroad with copy and pricing
5. Create Stripe products with payment links (one-time + subscriptions)
6. Generate HTML landing pages
7. Generate email sequences for nurturing
8. Announce on X + newsletter
9. Generate Excel reports of product performance
10. Create service packages with scope documents

Products ARCANA can create:
- PDF guides ($29-99)
- Prompt libraries ($19-49)
- Template packs ($29-79)
- Automation playbooks ($49-149)
- Video courses ($99-299)
- Service packages ($2K-10K/mo)
- Consulting proposals (custom priced)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

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

    # ── PDF Packaging ─────────────────────────────────────────────────

    async def package_as_pdf(
        self, content: dict[str, Any], output_dir: str = "data/products",
    ) -> dict[str, str | None]:
        """Convert generated product content into a professional PDF + QR code.

        Accepts output from generate_guide(), generate_prompt_library(), or
        generate_template_pack() and produces a downloadable PDF.
        """
        from src.toolkit import generate_pdf, generate_qr_code, slugify

        title = content.get("title", "Untitled Product")
        slug = slugify(title)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build content blocks for PDF
        blocks: list[dict[str, str]] = []

        if content.get("subtitle"):
            blocks.append({"type": "paragraph", "text": f"<i>{content['subtitle']}</i>"})

        # Handle guide/playbook format (chapters)
        for chapter in content.get("chapters", []):
            blocks.append({"type": "heading", "text": chapter.get("title", "")})
            blocks.append({"type": "paragraph", "text": chapter.get("content", "")})
            for takeaway in chapter.get("takeaways", []):
                blocks.append({"type": "bullet", "text": takeaway})

        # Handle prompt library format (categories → prompts)
        for category in content.get("categories", []):
            blocks.append({"type": "heading", "text": category.get("name", "")})
            for prompt in category.get("prompts", []):
                blocks.append({"type": "heading", "text": f"→ {prompt.get('title', '')}"})
                blocks.append({"type": "paragraph", "text": prompt.get("prompt", "")})
                if prompt.get("use_case"):
                    blocks.append({"type": "paragraph", "text": f"Use case: {prompt['use_case']}"})

        # Handle template pack format (templates)
        for tmpl in content.get("templates", []):
            blocks.append({"type": "heading", "text": tmpl.get("name", "")})
            if tmpl.get("type"):
                blocks.append({"type": "paragraph", "text": f"Type: {tmpl['type']}"})
            blocks.append({"type": "paragraph", "text": tmpl.get("content", "")})
            if tmpl.get("instructions"):
                blocks.append({"type": "paragraph", "text": f"Instructions: {tmpl['instructions']}"})

        # Footer
        blocks.append({"type": "heading", "text": "About Arcana Operations"})
        blocks.append({"type": "paragraph", "text": (
            "Arcana Operations LLC is an AI consulting firm based in Portland, OR. "
            "We build autonomous AI agents, marketing systems, and business automations. "
            "Visit arcanaoperations.com for consulting inquiries."
        )})

        # Generate PDF
        pdf_path = out_dir / f"{slug}.pdf"
        success = generate_pdf(title, blocks, pdf_path, author="Arcana Operations LLC")

        # Generate QR code
        qr_path = out_dir / f"{slug}-qr.png"
        generate_qr_code(f"https://arcanaoperations.com/products/{slug}", qr_path)

        result = {
            "pdf_path": str(pdf_path) if success else None,
            "qr_path": str(qr_path),
            "slug": slug,
            "word_count": content.get("total_word_count", 0),
        }

        self.memory.log(f"[Product] PDF packaged: {title} → {pdf_path}", "Products")
        return result

    # ── Stripe Product + Payment Link Creation ────────────────────────

    async def create_stripe_product(
        self,
        name: str,
        price_cents: int,
        description: str = "",
        recurring: bool = False,
        interval: str = "month",
    ) -> dict[str, Any]:
        """Create a product + price on Stripe with a payment link.

        Works for both one-time purchases (digital products) and
        recurring subscriptions (service packages).
        """
        stripe_key = self.payments.config.stripe_secret_key if hasattr(self.payments, 'config') else ""
        if not stripe_key:
            return {"status": "dry_run", "note": "No Stripe key"}

        headers = {"Authorization": f"Bearer {stripe_key}"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Create product
                prod_resp = await client.post(
                    "https://api.stripe.com/v1/products",
                    headers=headers,
                    data={"name": name, "description": description or name},
                )
                prod_resp.raise_for_status()
                product_id = prod_resp.json().get("id")

                # Create price
                price_data: dict[str, Any] = {
                    "product": product_id,
                    "unit_amount": price_cents,
                    "currency": "usd",
                }
                if recurring:
                    price_data["recurring[interval]"] = interval

                price_resp = await client.post(
                    "https://api.stripe.com/v1/prices",
                    headers=headers,
                    data=price_data,
                )
                price_resp.raise_for_status()
                price_id = price_resp.json().get("id")

                # Create payment link
                link_resp = await client.post(
                    "https://api.stripe.com/v1/payment_links",
                    headers=headers,
                    data={
                        "line_items[0][price]": price_id,
                        "line_items[0][quantity]": 1,
                    },
                )
                link_resp.raise_for_status()
                payment_url = link_resp.json().get("url", "")

                self.memory.log(
                    f"[Product] Stripe product: {name} (${price_cents/100}, "
                    f"{'recurring' if recurring else 'one-time'}) → {payment_url}",
                    "Products",
                )
                self.memory.save_knowledge(
                    "projects", f"stripe-{name[:30].lower().replace(' ', '-')}",
                    f"# Stripe Product: {name}\n\n"
                    f"- Product ID: {product_id}\n"
                    f"- Price ID: {price_id}\n"
                    f"- Payment Link: {payment_url}\n"
                    f"- Amount: ${price_cents/100}\n"
                    f"- Type: {'Subscription (' + interval + ')' if recurring else 'One-time'}\n"
                    f"- Created: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n",
                )

                return {
                    "status": "live",
                    "product_id": product_id,
                    "price_id": price_id,
                    "payment_url": payment_url,
                }
        except Exception as exc:
            logger.error("Stripe product creation failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    # ── Service Package Creation ──────────────────────────────────────

    async def create_service_package(
        self,
        service_name: str,
        price_monthly: int,
        scope: str,
        deliverables: list[str],
    ) -> dict[str, Any]:
        """Create a consulting service package: scope doc PDF + Stripe subscription."""
        # Generate professional scope document
        scope_doc = await self.llm.ask_json(
            f"Create a professional service package for Arcana Operations.\n\n"
            f"Service: {service_name}\n"
            f"Monthly price: ${price_monthly:,}\n"
            f"Scope: {scope}\n"
            f"Deliverables: {json.dumps(deliverables)}\n\n"
            f"Return JSON: {{\n"
            f'  "title": str,\n'
            f'  "tagline": str,\n'
            f'  "description": str (2-3 paragraphs),\n'
            f'  "deliverables": [str],\n'
            f'  "timeline": str,\n'
            f'  "whats_included": [str],\n'
            f'  "whats_not_included": [str],\n'
            f'  "ideal_for": str\n'
            f"}}",
            tier=Tier.SONNET,
        )

        # Create Stripe subscription product
        stripe_result = await self.create_stripe_product(
            name=service_name,
            price_cents=price_monthly * 100,
            description=scope_doc.get("description", scope),
            recurring=True,
            interval="month",
        )

        # Generate PDF scope document
        from src.toolkit import generate_pdf, slugify
        slug = slugify(service_name)
        pdf_dir = Path("data/services")
        pdf_dir.mkdir(parents=True, exist_ok=True)

        blocks = [
            {"type": "paragraph", "text": scope_doc.get("tagline", "")},
            {"type": "paragraph", "text": scope_doc.get("description", "")},
            {"type": "heading", "text": "Deliverables"},
        ]
        for d in scope_doc.get("deliverables", deliverables):
            blocks.append({"type": "bullet", "text": d})
        blocks.append({"type": "heading", "text": "What's Included"})
        for item in scope_doc.get("whats_included", []):
            blocks.append({"type": "bullet", "text": item})
        if scope_doc.get("whats_not_included"):
            blocks.append({"type": "heading", "text": "Out of Scope"})
            for item in scope_doc["whats_not_included"]:
                blocks.append({"type": "bullet", "text": item})
        blocks.append({"type": "heading", "text": "Investment"})
        blocks.append({"type": "paragraph", "text": f"${price_monthly:,}/month"})
        blocks.append({"type": "paragraph", "text": f"Ideal for: {scope_doc.get('ideal_for', '')}"})

        pdf_path = pdf_dir / f"{slug}-scope.pdf"
        generate_pdf(scope_doc.get("title", service_name), blocks, pdf_path)

        self.memory.log(
            f"[Product] Service package: {service_name} (${price_monthly:,}/mo) → {pdf_path}",
            "Products",
        )

        return {
            "scope_doc": scope_doc,
            "stripe": stripe_result,
            "pdf_path": str(pdf_path),
        }

    # ── Landing Page Generation ───────────────────────────────────────

    async def generate_landing_page(
        self,
        product_name: str,
        description: str,
        price: str,
        purchase_url: str,
        bullet_points: list[str] | None = None,
    ) -> str:
        """Generate a complete HTML landing page for a product."""
        result = await self.llm.ask(
            f"Create a complete, modern HTML landing page.\n\n"
            f"Product: {product_name}\n"
            f"Description: {description}\n"
            f"Price: {price}\n"
            f"Purchase URL: {purchase_url}\n"
            f"Key benefits: {json.dumps(bullet_points or [])}\n\n"
            f"Requirements:\n"
            f"- Single HTML file with inline CSS\n"
            f"- Hero section with headline, subheadline, CTA button\n"
            f"- Benefits section (3-4 key benefits)\n"
            f"- What's included section\n"
            f"- FAQ section (4-5 questions)\n"
            f"- Final CTA with money-back guarantee\n"
            f"- Footer with Arcana Operations branding\n"
            f"- Mobile responsive, clean professional design\n\n"
            f"Return ONLY the HTML code.",
            tier=Tier.SONNET,
            temperature=0.5,
        )

        from src.toolkit import slugify
        slug = slugify(product_name)
        page_dir = Path("data/landing_pages")
        page_dir.mkdir(parents=True, exist_ok=True)
        page_path = page_dir / f"{slug}.html"
        page_path.write_text(result)

        self.memory.log(
            f"[Product] Landing page: {product_name} → {page_path}", "Products",
        )
        return str(page_path)

    # ── Email Sequence Generation ─────────────────────────────────────

    async def generate_email_sequence(
        self,
        product_name: str,
        price: str,
        purchase_url: str,
        sequence_type: str = "launch",
    ) -> list[dict[str, str]]:
        """Generate an email sequence for product launch or nurturing.

        Types: "launch" (announce + urgency), "welcome" (post-purchase),
               "nurture" (warm leads), "abandoned" (cart recovery)
        """
        result = await self.llm.ask_json(
            f"Create a {sequence_type} email sequence for this product.\n\n"
            f"Product: {product_name}\n"
            f"Price: {price}\n"
            f"Purchase URL: {purchase_url}\n\n"
            f"Sequence type: {sequence_type}\n"
            f"Write in ARCANA's voice — confident, analytical, practical.\n\n"
            f"Return JSON: {{\n"
            f'  "emails": [{{\n'
            f'    "subject": str,\n'
            f'    "preview_text": str (email preview text),\n'
            f'    "body_html": str (email body with HTML formatting),\n'
            f'    "send_delay_hours": int (hours after trigger to send),\n'
            f'    "purpose": str (what this email achieves)\n'
            f"  }}]\n"
            f"}}",
            tier=Tier.SONNET,
        )

        emails = result.get("emails", [])
        self.memory.log(
            f"[Product] Email sequence ({sequence_type}): {len(emails)} emails for {product_name}",
            "Products",
        )
        return emails

    # ── Affiliate Link Generator ──────────────────────────────────────

    async def generate_affiliate_links(
        self,
        product_topic: str,
    ) -> list[dict[str, str]]:
        """Identify relevant affiliate products to recommend alongside our product."""
        result = await self.llm.ask_json(
            f"Identify 5-10 tools/products related to '{product_topic}' that have "
            f"affiliate programs. ARCANA can earn commission by recommending these.\n\n"
            f"Focus on: SaaS tools, AI platforms, hosting, courses, books.\n\n"
            f"Return JSON: {{\n"
            f'  "affiliates": [{{\n'
            f'    "product_name": str,\n'
            f'    "category": str,\n'
            f'    "typical_commission": str,\n'
            f'    "relevance": str (why it fits),\n'
            f'    "signup_url": str (affiliate program page)\n'
            f"  }}]\n"
            f"}}",
            tier=Tier.HAIKU,
        )
        return result.get("affiliates", [])

    # ── Product Performance Report ────────────────────────────────────

    async def generate_product_report(self, output_path: str = "data/reports/products.xlsx") -> str | None:
        """Generate an Excel report of all products and their performance."""
        from src.toolkit import generate_excel

        catalog = self.get_product_catalog()
        if not catalog:
            return None

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        success = generate_excel(catalog, output_path, sheet_name="Product Catalog")
        return output_path if success else None

    # ── Product Catalog ───────────────────────────────────────────────

    def get_product_catalog(self) -> list[dict[str, str]]:
        """Get all products from memory."""
        products = []
        projects_dir = Path("memory/life/projects")
        if projects_dir.exists():
            for f in sorted(projects_dir.glob("product-*.md")):
                content = f.read_text()
                name = f.stem.replace("product-", "").replace("-", " ").title()
                # Extract key fields
                price = ""
                url = ""
                for line in content.splitlines():
                    if "price:" in line.lower():
                        price = line.split(":", 1)[1].strip()
                    if "url:" in line.lower() or "link:" in line.lower():
                        url = line.split(":", 1)[1].strip()
                products.append({
                    "Name": name,
                    "Price": price,
                    "URL": url,
                    "File": f.name,
                })
        return products

    # ── Full Pipeline v2: Idea → Content → PDF → Listing → Landing → Emails ──

    async def create_product_full_pipeline(
        self,
        name: str | None = None,
        product_type: str = "guide",
        price_cents: int = 4900,
        platform: str = "gumroad",
    ) -> dict[str, Any]:
        """Complete autonomous product creation — from idea to sellable product.

        If no name given, auto-identifies the best product opportunity.
        Creates: content, PDF, platform listing, landing page, email sequence, launch tweets.
        """
        logger.info("Starting full product pipeline%s", f": {name}" if name else "")

        # 1. Identify opportunity if no name given
        if not name:
            opportunity = await self.identify_product_opportunity()
            name = opportunity.get("product_name", "Arcana AI Guide")
            product_type = opportunity.get("product_type", "guide")
            price_cents = opportunity.get("price_cents", 4900)
            topics = opportunity.get("key_topics", [])
            audience = opportunity.get("target_audience", "business owners")
        else:
            topics = []
            audience = "business owners and operators"

        # 2. Generate content
        if product_type in ("guide", "playbook"):
            content = await self.generate_guide(name, topics or [name], audience)
        elif product_type == "prompt_library":
            content = await self.generate_prompt_library(audience)
        elif product_type == "template_pack":
            content = await self.generate_template_pack(audience, topics or [name])
        else:
            content = await self.generate_guide(name, topics or [name], audience)

        # 3. Package as PDF
        pdf_result = await self.package_as_pdf(content)

        # 4. Generate listing copy
        copy = await self.llm.ask_json(
            f"Generate product listing copy for this product.\n\n"
            f"Product: {name}\n"
            f"Type: {product_type}\n"
            f"Price: ${price_cents/100:.0f}\n\n"
            f"Return JSON: {{"
            f'"title": str, "subtitle": str, '
            f'"description_html": str, '
            f'"bullet_points": [str], '
            f'"cta_text": str, '
            f'"guarantee": str}}',
            tier=Tier.SONNET,
        )

        # 5. List on platform
        listing: dict[str, Any] = {"name": name, "price": price_cents / 100}
        description = copy.get("description_html", "")

        if platform == "gumroad":
            gumroad = await self.payments.create_gumroad_product(
                name=name, description=description, price_cents=price_cents,
            )
            if gumroad:
                listing["gumroad_url"] = gumroad.get("short_url")
                listing["gumroad_id"] = gumroad.get("id")

        if platform in ("stripe", "both"):
            stripe = await self.create_stripe_product(
                name=name, price_cents=price_cents, description=description,
            )
            listing["stripe"] = stripe

        purchase_url = listing.get("gumroad_url") or listing.get("stripe", {}).get("payment_url", "")

        # 6. Generate landing page
        landing_page = await self.generate_landing_page(
            product_name=name,
            description=description,
            price=f"${price_cents/100:.0f}",
            purchase_url=purchase_url or "https://arcanaoperations.com",
            bullet_points=copy.get("bullet_points", []),
        )

        # 7. Generate email sequences
        launch_emails = await self.generate_email_sequence(
            product_name=name,
            price=f"${price_cents/100:.0f}",
            purchase_url=purchase_url or "https://arcanaoperations.com",
            sequence_type="launch",
        )
        welcome_emails = await self.generate_email_sequence(
            product_name=name,
            price=f"${price_cents/100:.0f}",
            purchase_url=purchase_url or "https://arcanaoperations.com",
            sequence_type="welcome",
        )

        # 8. Generate launch tweet
        launch_tweet = await self.llm.ask(
            f"Write a product launch tweet for ARCANA AI.\n\n"
            f"Product: {name} (${price_cents/100:.0f})\n"
            f"Type: {product_type}\n\n"
            f"Rules: Under 280 chars, ARCANA voice, create urgency.\n"
            f"No link in main tweet (goes in reply).",
            tier=Tier.HAIKU, max_tokens=100,
        )

        # 9. Identify affiliate opportunities
        affiliates = await self.generate_affiliate_links(name)

        # 10. Save comprehensive product record
        self.memory.save_knowledge(
            "projects", f"product-{name[:30].lower().replace(' ', '-')}",
            f"# Product: {name}\n\n"
            f"- Type: {product_type}\n"
            f"- Price: ${price_cents/100:.0f}\n"
            f"- PDF: {pdf_result.get('pdf_path', 'N/A')}\n"
            f"- Landing: {landing_page}\n"
            f"- Gumroad: {listing.get('gumroad_url', 'N/A')}\n"
            f"- Stripe: {listing.get('stripe', {}).get('payment_url', 'N/A')}\n"
            f"- Launch emails: {len(launch_emails)}\n"
            f"- Welcome emails: {len(welcome_emails)}\n"
            f"- Affiliates identified: {len(affiliates)}\n"
            f"- Created: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n",
        )

        self.memory.log(
            f"[Product] FULL PIPELINE COMPLETE: {name} @ ${price_cents/100:.0f}\n"
            f"  PDF: {pdf_result.get('pdf_path')}\n"
            f"  Landing: {landing_page}\n"
            f"  Emails: {len(launch_emails)} launch + {len(welcome_emails)} welcome\n"
            f"  Affiliates: {len(affiliates)}",
            "Products",
        )

        return {
            "status": "created",
            "name": name,
            "type": product_type,
            "price": price_cents / 100,
            "content": {
                "chapters": len(content.get("chapters", content.get("categories", content.get("templates", [])))),
                "word_count": content.get("total_word_count", 0),
            },
            "pdf": pdf_result,
            "copy": copy,
            "listing": listing,
            "landing_page": landing_page,
            "emails": {
                "launch": launch_emails,
                "welcome": welcome_emails,
            },
            "launch_tweet": launch_tweet.strip(),
            "affiliates": affiliates,
        }
