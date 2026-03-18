"""ARCANA AI — Payments Engine.

Full payment lifecycle — not just tracking revenue, but COLLECTING it.

1. Create Stripe checkout links for products and services
2. Create and send invoices
3. Manage subscriptions (monthly service billing)
4. Handle webhooks (payment success, failure, refund)
5. Generate payment links for custom proposals
6. Create Gumroad products autonomously
7. Track all payments in memory

This replaces the old read-only products.py.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import stripe

from src.memory import Memory

logger = logging.getLogger("arcana.payments")


class PaymentsEngine:
    """Full payment processing — create, collect, manage, track."""

    def __init__(
        self, memory: Memory,
        stripe_key: str = "", gumroad_token: str = "",
    ) -> None:
        self.memory = memory
        self.stripe_key = stripe_key
        self.gumroad_token = gumroad_token
        self._http: httpx.AsyncClient | None = None

        if stripe_key:
            stripe.api_key = stripe_key

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30)
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Stripe: Checkout Links ──────────────────────────────────────

    def create_checkout_link(
        self, product_name: str, price_cents: int,
        mode: str = "payment",  # "payment" or "subscription"
        success_url: str = "https://arcanaoperations.com/thank-you",
        cancel_url: str = "https://arcanaoperations.com",
        interval: str = "month",  # for subscriptions
    ) -> str | None:
        """Create a Stripe checkout session and return the URL."""
        if not self.stripe_key:
            logger.warning("Stripe not configured")
            return None

        try:
            price_data: dict[str, Any] = {
                "currency": "usd",
                "product_data": {"name": product_name},
                "unit_amount": price_cents,
            }
            if mode == "subscription":
                price_data["recurring"] = {"interval": interval}

            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price_data": price_data, "quantity": 1}],
                mode=mode,
                success_url=success_url,
                cancel_url=cancel_url,
            )

            url = session.url
            self.memory.log(
                f"[Payments] Checkout link created: {product_name} "
                f"${price_cents/100:.2f} ({mode})\n  URL: {url}",
                "Payments",
            )
            return url

        except Exception as exc:
            logger.error("Stripe checkout error: %s", exc)
            return None

    def create_service_subscription_link(
        self, client_name: str, service: str, monthly_price_cents: int,
    ) -> str | None:
        """Create a subscription checkout link for a service client."""
        return self.create_checkout_link(
            product_name=f"Arcana Operations — {service} ({client_name})",
            price_cents=monthly_price_cents,
            mode="subscription",
        )

    def create_payment_link(
        self, description: str, amount_cents: int,
    ) -> str | None:
        """Create a one-time Stripe Payment Link."""
        if not self.stripe_key:
            return None

        try:
            product = stripe.Product.create(name=description)
            price = stripe.Price.create(
                product=product.id, unit_amount=amount_cents, currency="usd",
            )
            link = stripe.PaymentLink.create(line_items=[{"price": price.id, "quantity": 1}])
            url = link.url

            self.memory.log(
                f"[Payments] Payment link: {description} ${amount_cents/100:.2f}\n  URL: {url}",
                "Payments",
            )
            return url

        except Exception as exc:
            logger.error("Stripe payment link error: %s", exc)
            return None

    # ── Stripe: Invoices ────────────────────────────────────────────

    def create_invoice(
        self, customer_email: str, items: list[dict[str, Any]],
        due_days: int = 7, memo: str = "",
    ) -> dict[str, Any] | None:
        """Create and send a Stripe invoice.

        items: [{"description": str, "amount_cents": int}]
        """
        if not self.stripe_key:
            return None

        try:
            # Find or create customer
            customers = stripe.Customer.list(email=customer_email, limit=1)
            if customers.data:
                customer = customers.data[0]
            else:
                customer = stripe.Customer.create(email=customer_email)

            # Create invoice
            invoice = stripe.Invoice.create(
                customer=customer.id,
                collection_method="send_invoice",
                days_until_due=due_days,
                description=memo,
            )

            # Add line items
            for item in items:
                stripe.InvoiceItem.create(
                    customer=customer.id,
                    invoice=invoice.id,
                    description=item["description"],
                    amount=item["amount_cents"],
                    currency="usd",
                )

            # Finalize and send
            finalized = stripe.Invoice.finalize_invoice(invoice.id)
            stripe.Invoice.send_invoice(invoice.id)

            self.memory.log(
                f"[Payments] Invoice sent: {customer_email} — "
                f"${sum(i['amount_cents'] for i in items)/100:.2f}",
                "Billing",
            )

            return {
                "invoice_id": finalized.id,
                "invoice_url": finalized.hosted_invoice_url,
                "amount": finalized.amount_due / 100,
                "status": finalized.status,
            }

        except Exception as exc:
            logger.error("Stripe invoice error: %s", exc)
            return None

    # ── Stripe: Revenue Tracking ────────────────────────────────────

    def get_revenue_summary(self, days: int = 30) -> dict[str, Any]:
        """Get revenue summary from Stripe for the last N days."""
        if not self.stripe_key:
            return {"revenue": 0, "charges": 0}

        try:
            since = int(time.time()) - (days * 86400)
            charges = stripe.Charge.list(created={"gte": since}, limit=100)

            total = 0
            count = 0
            recent = []
            for charge in charges.auto_paging_iter():
                if charge.paid and not charge.refunded:
                    total += charge.amount / 100
                    count += 1
                    if len(recent) < 10:
                        recent.append({
                            "amount": charge.amount / 100,
                            "description": charge.description or "",
                            "created": datetime.fromtimestamp(
                                charge.created, tz=timezone.utc
                            ).strftime("%Y-%m-%d"),
                        })

            return {"revenue": total, "charges": count, "recent": recent}

        except Exception as exc:
            logger.error("Stripe revenue error: %s", exc)
            return {"revenue": 0, "charges": 0}

    def get_active_subscriptions(self) -> list[dict[str, Any]]:
        """Get all active subscriptions (service MRR)."""
        if not self.stripe_key:
            return []

        try:
            subs = stripe.Subscription.list(status="active", limit=100)
            result = []
            for sub in subs.auto_paging_iter():
                for item in sub["items"]["data"]:
                    result.append({
                        "id": sub.id,
                        "customer": sub.customer,
                        "product": item["price"]["product"],
                        "amount": item["price"]["unit_amount"] / 100,
                        "interval": item["price"]["recurring"]["interval"],
                        "status": sub.status,
                    })
            return result
        except Exception as exc:
            logger.error("Stripe subscriptions error: %s", exc)
            return []

    def get_mrr(self) -> float:
        """Calculate Monthly Recurring Revenue from active subscriptions."""
        subs = self.get_active_subscriptions()
        return sum(s["amount"] for s in subs if s["interval"] == "month")

    # ── Gumroad: Product Creation ───────────────────────────────────

    async def create_gumroad_product(
        self, name: str, description: str, price_cents: int,
        product_type: str = "digital",  # digital, membership, physical
        url_slug: str = "",
    ) -> dict[str, Any] | None:
        """Create a new product on Gumroad autonomously."""
        if not self.gumroad_token:
            logger.warning("Gumroad not configured")
            return None

        try:
            client = await self._get_http()
            resp = await client.post(
                "https://api.gumroad.com/v2/products",
                data={
                    "access_token": self.gumroad_token,
                    "name": name,
                    "description": description,
                    "price": price_cents,
                    "customizable_price": "true",
                    "url": url_slug or name.lower().replace(" ", "-")[:20],
                },
            )
            if resp.status_code in (200, 201):
                product = resp.json().get("product", {})
                self.memory.log(
                    f"[Payments] Gumroad product created: {name} "
                    f"${price_cents/100:.2f}\n  URL: {product.get('short_url', 'N/A')}",
                    "Products",
                )
                self.memory.save_knowledge(
                    "projects", f"product-{url_slug or name[:20]}",
                    f"# Product: {name}\n\nPrice: ${price_cents/100:.2f}\n"
                    f"URL: {product.get('short_url', 'N/A')}\n"
                    f"Platform: Gumroad\nCreated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n",
                )
                return product
            logger.error("Gumroad create failed: %s", resp.text[:200])
        except Exception as exc:
            logger.error("Gumroad error: %s", exc)
        return None

    async def get_gumroad_revenue(self) -> dict[str, Any]:
        """Check Gumroad sales and revenue."""
        if not self.gumroad_token:
            return {"revenue": 0, "sales": 0}

        try:
            client = await self._get_http()
            resp = await client.get(
                "https://api.gumroad.com/v2/sales",
                params={"access_token": self.gumroad_token},
            )
            if resp.status_code == 200:
                sales = resp.json().get("sales", [])
                revenue = sum(
                    s.get("price", 0) / 100 for s in sales
                    if not s.get("refunded") and not s.get("chargebacked")
                )
                return {"revenue": revenue, "sales": len(sales)}
        except Exception as exc:
            logger.error("Gumroad revenue error: %s", exc)
        return {"revenue": 0, "sales": 0}

    # ── Unified Revenue ─────────────────────────────────────────────

    async def get_total_revenue(self) -> dict[str, Any]:
        """Get total revenue across Stripe + Gumroad."""
        stripe_rev = self.get_revenue_summary()
        gumroad_rev = await self.get_gumroad_revenue()
        mrr = self.get_mrr()

        return {
            "stripe": stripe_rev,
            "gumroad": gumroad_rev,
            "mrr": mrr,
            "total": stripe_rev.get("revenue", 0) + gumroad_rev.get("revenue", 0),
        }
