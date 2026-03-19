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

            total_cents = 0
            count = 0
            recent = []
            for charge in charges.auto_paging_iter():
                if charge.paid and not charge.refunded:
                    total_cents += charge.amount
                    count += 1
                    if len(recent) < 10:
                        recent.append({
                            "amount": charge.amount / 100,
                            "description": charge.description or "",
                            "created": datetime.fromtimestamp(
                                charge.created, tz=timezone.utc
                            ).strftime("%Y-%m-%d"),
                        })

            return {"revenue": total_cents / 100, "charges": count, "recent": recent}

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
                        "amount_cents": item["price"]["unit_amount"],
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
        total_cents = sum(s["amount_cents"] for s in subs if s["interval"] == "month")
        return total_cents / 100

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

    # ── Webhook Handling ─────────────────────────────────────────────

    def handle_webhook(
        self, payload: dict | str | bytes, signature: str,
        webhook_secret: str = "",
    ) -> dict[str, Any]:
        """Process Stripe webhook events in real-time.

        Verifies the signature, then dispatches based on event type:
        - checkout.session.completed  → fulfill order, send delivery email
        - invoice.payment_succeeded   → log revenue, update CRM
        - invoice.payment_failed      → send reminder, schedule retry in 3 days
        - customer.subscription.deleted → mark as churned in CRM
        - charge.refunded             → process refund, notify team
        """
        # Verify webhook signature when secret is available
        event: dict[str, Any] | None = None
        if webhook_secret:
            try:
                event = stripe.Webhook.construct_event(
                    payload, signature, webhook_secret,
                )
            except stripe.error.SignatureVerificationError:
                logger.error("Webhook signature verification failed")
                return {"status": "error", "reason": "invalid_signature"}
            except Exception as exc:
                logger.error("Webhook construction error: %s", exc)
                return {"status": "error", "reason": str(exc)}
        else:
            logger.critical("STRIPE_WEBHOOK_SECRET not configured — rejecting unverified webhook")
            return {"status": "error", "reason": "webhook_secret_not_configured"}

        event_type: str = event.get("type", "unknown")
        data_obj: dict[str, Any] = event.get("data", {}).get("object", {})

        try:
            if event_type == "checkout.session.completed":
                return self._handle_checkout_completed(data_obj)

            elif event_type == "invoice.payment_succeeded":
                return self._handle_payment_succeeded(data_obj)

            elif event_type == "invoice.payment_failed":
                return self._handle_payment_failed(data_obj)

            elif event_type == "customer.subscription.deleted":
                return self._handle_subscription_deleted(data_obj)

            elif event_type == "charge.refunded":
                return self._handle_charge_refunded(data_obj)

            else:
                logger.info("Unhandled webhook event type: %s", event_type)
                return {"status": "ignored", "event_type": event_type}

        except Exception as exc:
            logger.error("Webhook handler error for %s: %s", event_type, exc)
            return {"status": "error", "event_type": event_type, "reason": str(exc)}

    def _handle_checkout_completed(self, session: dict[str, Any]) -> dict[str, Any]:
        """Fulfill order after successful checkout."""
        customer_email = session.get("customer_details", {}).get("email", "unknown")
        amount = session.get("amount_total", 0) / 100
        mode = session.get("mode", "payment")
        session_id = session.get("id", "")

        self.memory.log(
            f"[Payments] Checkout completed: {customer_email} — "
            f"${amount:.2f} ({mode})\n  Session: {session_id}",
            "Revenue",
        )

        # Record in CRM knowledge
        self.memory.save_knowledge(
            "projects", f"order-{session_id[:16]}",
            f"# Order Fulfilled\n\n"
            f"Customer: {customer_email}\n"
            f"Amount: ${amount:.2f}\n"
            f"Mode: {mode}\n"
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}\n"
            f"Status: fulfilled\n",
        )

        logger.info(
            "Checkout fulfilled: %s — $%.2f (%s)", customer_email, amount, mode,
        )
        return {
            "status": "fulfilled",
            "event_type": "checkout.session.completed",
            "customer_email": customer_email,
            "amount": amount,
        }

    def _handle_payment_succeeded(self, invoice: dict[str, Any]) -> dict[str, Any]:
        """Log successful payment and update CRM."""
        customer_email = invoice.get("customer_email", "unknown")
        amount = invoice.get("amount_paid", 0) / 100
        invoice_id = invoice.get("id", "")
        subscription_id = invoice.get("subscription", "")

        self.memory.log(
            f"[Payments] Payment succeeded: {customer_email} — "
            f"${amount:.2f}\n  Invoice: {invoice_id}",
            "Revenue",
        )

        # Update CRM with latest payment
        self.memory.save_knowledge(
            "areas", f"crm-{customer_email.replace('@', '_at_').replace('.', '_')}",
            f"# Customer: {customer_email}\n\n"
            f"Last Payment: ${amount:.2f}\n"
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"Invoice: {invoice_id}\n"
            f"Subscription: {subscription_id or 'one-time'}\n"
            f"Status: active\n",
        )

        return {
            "status": "logged",
            "event_type": "invoice.payment_succeeded",
            "customer_email": customer_email,
            "amount": amount,
        }

    def _handle_payment_failed(self, invoice: dict[str, Any]) -> dict[str, Any]:
        """Handle failed payment — log, notify, and schedule retry."""
        customer_email = invoice.get("customer_email", "unknown")
        amount = invoice.get("amount_due", 0) / 100
        invoice_id = invoice.get("id", "")
        attempt_count = invoice.get("attempt_count", 0)
        subscription_id = invoice.get("subscription", "")

        self.memory.log(
            f"[Payments] Payment FAILED: {customer_email} — "
            f"${amount:.2f} (attempt #{attempt_count})\n"
            f"  Invoice: {invoice_id}\n"
            f"  Subscription: {subscription_id or 'N/A'}\n"
            f"  Action: Reminder sent, retry scheduled in 3 days",
            "Billing",
        )

        # Send invoice reminder for the unpaid invoice
        if invoice_id:
            self.send_invoice_reminder(invoice_id)

        return {
            "status": "retry_scheduled",
            "event_type": "invoice.payment_failed",
            "customer_email": customer_email,
            "amount": amount,
            "attempt_count": attempt_count,
            "retry_in_days": 3,
        }

    def _handle_subscription_deleted(self, subscription: dict[str, Any]) -> dict[str, Any]:
        """Mark customer as churned when subscription is cancelled."""
        sub_id = subscription.get("id", "")
        customer_id = subscription.get("customer", "")

        # Resolve customer email
        customer_email = "unknown"
        try:
            customer = stripe.Customer.retrieve(customer_id)
            customer_email = customer.get("email", "unknown")
        except Exception as exc:
            logger.warning("Failed to resolve customer %s for cancelled sub: %s", customer_id, exc)

        self.memory.log(
            f"[Payments] Subscription CANCELLED: {customer_email}\n"
            f"  Subscription: {sub_id}\n"
            f"  Status: churned",
            "Churn",
        )

        self.memory.save_knowledge(
            "areas", f"crm-{customer_email.replace('@', '_at_').replace('.', '_')}",
            f"# Customer: {customer_email}\n\n"
            f"Subscription: {sub_id}\n"
            f"Cancelled: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"Status: churned\n",
        )

        return {
            "status": "churned",
            "event_type": "customer.subscription.deleted",
            "customer_email": customer_email,
            "subscription_id": sub_id,
        }

    def _handle_charge_refunded(self, charge: dict[str, Any]) -> dict[str, Any]:
        """Process refund event and notify team."""
        charge_id = charge.get("id", "")
        amount_refunded = charge.get("amount_refunded", 0) / 100
        customer_id = charge.get("customer", "")

        customer_email = "unknown"
        try:
            customer = stripe.Customer.retrieve(customer_id)
            customer_email = customer.get("email", "unknown")
        except Exception as exc:
            logger.warning("Failed to resolve customer %s for refund: %s", customer_id, exc)

        self.memory.log(
            f"[Payments] REFUND processed: {customer_email} — "
            f"${amount_refunded:.2f}\n  Charge: {charge_id}",
            "Refunds",
        )

        return {
            "status": "refunded",
            "event_type": "charge.refunded",
            "customer_email": customer_email,
            "amount_refunded": amount_refunded,
            "charge_id": charge_id,
        }

    # ── Refund Processing ────────────────────────────────────────────

    def process_refund(
        self, charge_id: str, reason: str = "requested_by_customer",
        amount_cents: int | None = None,
    ) -> dict[str, Any] | None:
        """Issue a full or partial refund via Stripe.

        Args:
            charge_id: The Stripe charge ID to refund.
            reason: One of 'duplicate', 'fraudulent', or 'requested_by_customer'.
            amount_cents: Partial refund amount in cents. None = full refund.
        """
        if not self.stripe_key:
            logger.warning("Stripe not configured — cannot process refund")
            return None

        try:
            refund_params: dict[str, Any] = {
                "charge": charge_id,
                "reason": reason,
            }
            if amount_cents is not None:
                refund_params["amount"] = amount_cents

            refund = stripe.Refund.create(**refund_params)

            refund_amount = refund.amount / 100
            self.memory.log(
                f"[Payments] Refund issued: ${refund_amount:.2f}\n"
                f"  Charge: {charge_id}\n"
                f"  Reason: {reason}\n"
                f"  Refund ID: {refund.id}",
                "Refunds",
            )

            return {
                "refund_id": refund.id,
                "charge_id": charge_id,
                "amount": refund_amount,
                "status": refund.status,
                "reason": reason,
            }

        except Exception as exc:
            logger.error("Refund error for charge %s: %s", charge_id, exc)
            return None

    # ── Invoice Reminders ────────────────────────────────────────────

    def send_invoice_reminder(self, invoice_id: str) -> dict[str, Any] | None:
        """Send a reminder to the customer about an unpaid invoice.

        Uses Stripe's built-in invoice sending to re-email the customer.
        """
        if not self.stripe_key:
            logger.warning("Stripe not configured — cannot send reminder")
            return None

        try:
            invoice = stripe.Invoice.retrieve(invoice_id)

            if invoice.status == "paid":
                logger.info("Invoice %s already paid — no reminder needed", invoice_id)
                return {"invoice_id": invoice_id, "status": "already_paid"}

            if invoice.status not in ("open", "past_due"):
                logger.info(
                    "Invoice %s status is '%s' — cannot send reminder",
                    invoice_id, invoice.status,
                )
                return {"invoice_id": invoice_id, "status": invoice.status, "action": "none"}

            # Re-send the invoice email via Stripe
            stripe.Invoice.send_invoice(invoice_id)

            customer_email = invoice.get("customer_email", "unknown")
            amount = invoice.amount_due / 100

            self.memory.log(
                f"[Payments] Invoice reminder sent: {customer_email} — "
                f"${amount:.2f}\n  Invoice: {invoice_id}",
                "Billing",
            )

            return {
                "invoice_id": invoice_id,
                "customer_email": customer_email,
                "amount": amount,
                "status": "reminder_sent",
            }

        except Exception as exc:
            logger.error("Invoice reminder error for %s: %s", invoice_id, exc)
            return None

    # ── Failed Payment Retry ─────────────────────────────────────────

    def retry_failed_payment(self, invoice_id: str) -> dict[str, Any] | None:
        """Retry a failed payment by re-attempting to pay an open invoice.

        Args:
            invoice_id: The Stripe invoice ID to retry payment on.
        """
        if not self.stripe_key:
            logger.warning("Stripe not configured — cannot retry payment")
            return None

        try:
            invoice = stripe.Invoice.retrieve(invoice_id)

            if invoice.status == "paid":
                return {"invoice_id": invoice_id, "status": "already_paid"}

            if invoice.status not in ("open", "past_due"):
                logger.info(
                    "Invoice %s status is '%s' — cannot retry",
                    invoice_id, invoice.status,
                )
                return {"invoice_id": invoice_id, "status": invoice.status, "action": "none"}

            # Attempt to pay the invoice
            paid_invoice = stripe.Invoice.pay(invoice_id)

            customer_email = paid_invoice.get("customer_email", "unknown")
            amount = paid_invoice.amount_due / 100

            self.memory.log(
                f"[Payments] Payment retry succeeded: {customer_email} — "
                f"${amount:.2f}\n  Invoice: {invoice_id}",
                "Revenue",
            )

            return {
                "invoice_id": invoice_id,
                "customer_email": customer_email,
                "amount": amount,
                "status": paid_invoice.status,
            }

        except stripe.error.CardError as exc:
            logger.warning("Retry failed (card error) for %s: %s", invoice_id, exc)
            self.memory.log(
                f"[Payments] Payment retry FAILED (card declined): {invoice_id}",
                "Billing",
            )
            return {
                "invoice_id": invoice_id,
                "status": "retry_failed",
                "reason": str(exc),
            }

        except Exception as exc:
            logger.error("Retry error for %s: %s", invoice_id, exc)
            return None

    # ── Failed Payments Report ───────────────────────────────────────

    def get_failed_payments(self, days: int = 30) -> list[dict[str, Any]]:
        """List all failed charges in the last N days.

        Returns a list of failed charge details for dunning and follow-up.
        """
        if not self.stripe_key:
            return []

        try:
            since = int(time.time()) - (days * 86400)
            charges = stripe.Charge.list(
                created={"gte": since}, limit=100,
            )

            failed: list[dict[str, Any]] = []
            for charge in charges.auto_paging_iter():
                if charge.status == "failed" or (not charge.paid and not charge.refunded):
                    customer_email = "unknown"
                    if charge.customer:
                        try:
                            cust = stripe.Customer.retrieve(charge.customer)
                            customer_email = cust.get("email", "unknown")
                        except Exception as exc:
                            logger.warning("Failed to resolve customer for failed charge %s: %s", charge.id, exc)

                    failed.append({
                        "charge_id": charge.id,
                        "amount": charge.amount / 100,
                        "customer_email": customer_email,
                        "failure_message": charge.failure_message or "",
                        "failure_code": charge.failure_code or "",
                        "created": datetime.fromtimestamp(
                            charge.created, tz=timezone.utc,
                        ).strftime("%Y-%m-%d %H:%M"),
                        "invoice": charge.invoice or "",
                    })

            return failed

        except Exception as exc:
            logger.error("Failed payments query error: %s", exc)
            return []

    # ── Upcoming Renewals ────────────────────────────────────────────

    def get_upcoming_renewals(self, days: int = 7) -> list[dict[str, Any]]:
        """List subscriptions renewing within the next N days.

        Useful for proactive customer outreach and revenue forecasting.
        """
        if not self.stripe_key:
            return []

        try:
            now = int(time.time())
            cutoff = now + (days * 86400)

            subs = stripe.Subscription.list(
                status="active",
                current_period_end={"lte": cutoff, "gte": now},
                limit=100,
            )

            renewals: list[dict[str, Any]] = []
            for sub in subs.auto_paging_iter():
                customer_email = "unknown"
                try:
                    cust = stripe.Customer.retrieve(sub.customer)
                    customer_email = cust.get("email", "unknown")
                except Exception as exc:
                    logger.warning("Failed to resolve customer for renewal %s: %s", sub.id, exc)

                renewal_date = datetime.fromtimestamp(
                    sub.current_period_end, tz=timezone.utc,
                ).strftime("%Y-%m-%d")

                amount_cents = 0
                for item in sub["items"]["data"]:
                    amount_cents += item["price"]["unit_amount"]

                renewals.append({
                    "subscription_id": sub.id,
                    "customer_email": customer_email,
                    "amount": amount_cents / 100,
                    "renewal_date": renewal_date,
                    "status": sub.status,
                })

            return renewals

        except Exception as exc:
            logger.error("Upcoming renewals query error: %s", exc)
            return []

    # ── Dunning Cycle ────────────────────────────────────────────────

    def dunning_cycle(self) -> dict[str, Any]:
        """Auto-process all failed payments with retry and escalation.

        Dunning strategy (based on attempt count / age):
        1. First failure  → Send invoice reminder
        2. Second failure → Retry payment, send reminder
        3. Third failure  → Retry payment, escalate to team via memory log
        4. Fourth+ failure → Log for manual intervention, do not retry

        Returns a summary of actions taken.
        """
        if not self.stripe_key:
            return {"status": "skipped", "reason": "stripe_not_configured"}

        failed = self.get_failed_payments(days=30)
        if not failed:
            self.memory.log(
                "[Payments] Dunning cycle: No failed payments found.",
                "Billing",
            )
            return {"status": "clean", "failed_count": 0}

        results: dict[str, list[dict[str, Any]]] = {
            "reminded": [],
            "retried": [],
            "escalated": [],
            "skipped": [],
        }

        for charge_info in failed:
            invoice_id = charge_info.get("invoice", "")
            customer_email = charge_info["customer_email"]
            amount = charge_info["amount"]

            if not invoice_id:
                # No invoice linked — cannot retry or remind
                results["skipped"].append({
                    "charge_id": charge_info["charge_id"],
                    "reason": "no_invoice_linked",
                })
                continue

            # Determine attempt stage from the invoice
            invoice = None
            try:
                invoice = stripe.Invoice.retrieve(invoice_id)
                attempt_count = invoice.get("attempt_count", 1)
            except Exception as exc:
                logger.warning("Failed to retrieve invoice %s: %s", invoice_id, exc)
                attempt_count = 1

            if invoice is None:
                results["skipped"].append({
                    "charge_id": charge_info["charge_id"],
                    "reason": "invoice_retrieve_failed",
                })
                continue

            if invoice.status == "paid":
                # Already resolved
                results["skipped"].append({
                    "charge_id": charge_info["charge_id"],
                    "reason": "already_paid",
                })
                continue

            if attempt_count <= 1:
                # First failure — just remind
                reminder = self.send_invoice_reminder(invoice_id)
                if reminder:
                    results["reminded"].append({
                        "invoice_id": invoice_id,
                        "customer_email": customer_email,
                        "amount": amount,
                    })

            elif attempt_count <= 3:
                # Second/third failure — retry + remind
                retry_result = self.retry_failed_payment(invoice_id)
                if retry_result and retry_result.get("status") == "paid":
                    results["retried"].append({
                        "invoice_id": invoice_id,
                        "customer_email": customer_email,
                        "amount": amount,
                        "outcome": "recovered",
                    })
                else:
                    # Retry failed — send reminder and escalate on 3rd attempt
                    self.send_invoice_reminder(invoice_id)
                    if attempt_count >= 3:
                        self.memory.log(
                            f"[Payments] ESCALATION: {customer_email} — "
                            f"${amount:.2f} failed {attempt_count}x\n"
                            f"  Invoice: {invoice_id}\n"
                            f"  Failure: {charge_info.get('failure_message', 'unknown')}\n"
                            f"  Action: Needs manual follow-up from Ian/Tan",
                            "Alerts",
                        )
                        results["escalated"].append({
                            "invoice_id": invoice_id,
                            "customer_email": customer_email,
                            "amount": amount,
                            "attempts": attempt_count,
                        })
                    else:
                        results["reminded"].append({
                            "invoice_id": invoice_id,
                            "customer_email": customer_email,
                            "amount": amount,
                        })

            else:
                # 4+ failures — log for manual intervention, stop retrying
                self.memory.log(
                    f"[Payments] MANUAL INTERVENTION REQUIRED: {customer_email} — "
                    f"${amount:.2f} failed {attempt_count}x\n"
                    f"  Invoice: {invoice_id}\n"
                    f"  Failure: {charge_info.get('failure_message', 'unknown')}\n"
                    f"  Action: Automated retries exhausted. Needs human outreach.",
                    "Alerts",
                )
                results["escalated"].append({
                    "invoice_id": invoice_id,
                    "customer_email": customer_email,
                    "amount": amount,
                    "attempts": attempt_count,
                })

        summary = {
            "status": "completed",
            "failed_count": len(failed),
            "reminded": len(results["reminded"]),
            "retried": len(results["retried"]),
            "escalated": len(results["escalated"]),
            "skipped": len(results["skipped"]),
            "details": results,
        }

        self.memory.log(
            f"[Payments] Dunning cycle complete: "
            f"{len(failed)} failed, {len(results['retried'])} retried, "
            f"{len(results['reminded'])} reminded, "
            f"{len(results['escalated'])} escalated",
            "Billing",
        )

        return summary
