"""ARCANA AI — Digital product and revenue management.

Manages:
- Stripe payment tracking
- Gumroad product sales
- Revenue reporting for morning reports
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import Config, get_config
from src.memory import Memory

logger = logging.getLogger("arcana.products")


class ProductManager:
    """Track and manage digital product revenue."""

    def __init__(self, config: Config | None = None, memory: Memory | None = None) -> None:
        self.config = config or get_config()
        self.memory = memory or Memory()
        self._client = httpx.AsyncClient(timeout=30.0)

    async def check_stripe_revenue(self) -> dict[str, Any]:
        """Check recent Stripe charges and calculate revenue (safe parsing)."""
        if not self.config.stripe_secret_key:
            return {"revenue": 0, "charges": 0, "status": "no_key"}

        try:
            resp = await self._client.get(
                "https://api.stripe.com/v1/charges",
                params={"limit": 25},
                headers={"Authorization": f"Bearer {self.config.stripe_secret_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            charges = data.get("data", [])
            if not isinstance(charges, list):
                return {"revenue": 0, "charges": 0, "error": "unexpected_response"}
            paid = [c for c in charges if c.get("paid") and not c.get("refunded")]
            revenue = sum(max(0, c.get("amount", 0)) / 100 for c in paid)

            return {
                "revenue": revenue,
                "charges": len(paid),
                "recent": [
                    {
                        "amount": c["amount"] / 100,
                        "description": c.get("description", ""),
                        "created": c.get("created"),
                    }
                    for c in paid[:5]
                ],
            }
        except Exception as exc:
            logger.error("Stripe check failed: %s", exc)
            return {"revenue": 0, "charges": 0, "error": str(exc)}

    async def check_gumroad_revenue(self) -> dict[str, Any]:
        """Check Gumroad sales (safe price parsing)."""
        if not self.config.gumroad_access_token:
            return {"revenue": 0, "sales": 0, "status": "no_key"}

        try:
            resp = await self._client.get(
                "https://api.gumroad.com/v2/sales",
                params={"access_token": self.config.gumroad_access_token},
            )
            resp.raise_for_status()
            data = resp.json()
            sales = data.get("sales", [])
            if not isinstance(sales, list):
                return {"revenue": 0, "sales": 0, "error": "unexpected_response"}
            revenue = 0.0
            for s in sales:
                try:
                    price = float(s.get("price", 0))
                    revenue += max(0, price) / 100
                except (ValueError, TypeError):
                    pass

            return {"revenue": revenue, "sales": len(sales)}
        except Exception as exc:
            logger.error("Gumroad check failed: %s", exc)
            return {"revenue": 0, "sales": 0, "error": str(exc)}

    async def get_revenue_summary(self) -> dict[str, Any]:
        """Combined revenue summary for morning report."""
        stripe = await self.check_stripe_revenue()
        gumroad = await self.check_gumroad_revenue()

        total = stripe.get("revenue", 0) + gumroad.get("revenue", 0)
        summary = {
            "total_revenue": total,
            "stripe": stripe,
            "gumroad": gumroad,
        }

        self.memory.log(
            f"Revenue check: ${total:.2f} total "
            f"(Stripe: ${stripe.get('revenue', 0):.2f}, "
            f"Gumroad: ${gumroad.get('revenue', 0):.2f})",
            "Revenue",
        )

        return summary

    async def create_gumroad_product(
        self, name: str, price_cents: int, description: str
    ) -> dict[str, Any]:
        """Create a new product on Gumroad (with validation)."""
        if not self.config.gumroad_access_token:
            return {"error": "No Gumroad token"}
        if not name or not name.strip():
            return {"error": "Product name is required"}
        if price_cents < 0:
            return {"error": f"Price must be non-negative, got {price_cents}"}
        if price_cents > 100_000_00:  # $100K max
            return {"error": "Price exceeds maximum"}

        try:
            resp = await self._client.post(
                "https://api.gumroad.com/v2/products",
                data={
                    "access_token": self.config.gumroad_access_token,
                    "name": name,
                    "price": price_cents,
                    "description": description,
                },
            )
            resp.raise_for_status()
            product = resp.json().get("product", {})
            url = product.get("short_url", "")

            self.memory.log(f"Created Gumroad product: {name} (${price_cents/100}) — {url}", "Products")
            self.memory.save_knowledge("projects", name, f"Digital product on Gumroad.\nPrice: ${price_cents/100}\nURL: {url}")

            return {"url": url, "id": product.get("id")}
        except Exception as exc:
            logger.error("Gumroad create failed: %s", exc)
            return {"error": str(exc)}

    async def generate_product_pdf(
        self, title: str, content_blocks: list[dict[str, str]],
        output_dir: str = "data/products",
    ) -> str | None:
        """Generate a PDF digital product with QR code linking to purchase page.

        Returns the output file path, or None on failure.
        """
        from src.toolkit import generate_pdf, generate_qr_code, slugify
        from pathlib import Path

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = slugify(title)
        pdf_path = out_dir / f"{slug}.pdf"
        qr_path = out_dir / f"{slug}-qr.png"

        # Generate the PDF
        success = generate_pdf(title, content_blocks, pdf_path, author="Arcana Operations LLC")
        if not success:
            logger.error("Failed to generate PDF: %s", title)
            return None

        # Generate QR code linking to the product (placeholder URL)
        generate_qr_code(f"https://arcanaoperations.com/products/{slug}", qr_path)

        self.memory.log(f"[Products] Generated PDF: {title} → {pdf_path}", "Products")
        return str(pdf_path)

    async def generate_invoice(
        self, invoice_number: str, client_name: str,
        items: list[dict[str, Any]], total: float,
        output_dir: str = "data/invoices",
    ) -> str | None:
        """Generate an invoice PDF for a client."""
        from src.toolkit import generate_invoice_pdf
        from pathlib import Path

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / f"invoice-{invoice_number}.pdf"

        success = generate_invoice_pdf(pdf_path, invoice_number, client_name, items, total)
        if not success:
            return None

        self.memory.log(f"[Products] Invoice generated: #{invoice_number} for {client_name} (${total:.2f})", "Products")
        return str(pdf_path)

    async def close(self) -> None:
        await self._client.aclose()
