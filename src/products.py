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
        """Check recent Stripe charges and calculate revenue."""
        if not self.config.stripe_secret_key:
            return {"revenue": 0, "charges": 0, "status": "no_key"}

        try:
            resp = await self._client.get(
                "https://api.stripe.com/v1/charges",
                params={"limit": 25},
                headers={"Authorization": f"Bearer {self.config.stripe_secret_key}"},
            )
            resp.raise_for_status()
            charges = resp.json().get("data", [])
            paid = [c for c in charges if c.get("paid") and not c.get("refunded")]
            revenue = sum(c.get("amount", 0) / 100 for c in paid)

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
        """Check Gumroad sales."""
        if not self.config.gumroad_access_token:
            return {"revenue": 0, "sales": 0, "status": "no_key"}

        try:
            resp = await self._client.get(
                "https://api.gumroad.com/v2/sales",
                params={"access_token": self.config.gumroad_access_token},
            )
            resp.raise_for_status()
            sales = resp.json().get("sales", [])
            revenue = sum(float(s.get("price", 0)) / 100 for s in sales)

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
        """Create a new product on Gumroad."""
        if not self.config.gumroad_access_token:
            return {"error": "No Gumroad token"}

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

    async def close(self) -> None:
        await self._client.aclose()
