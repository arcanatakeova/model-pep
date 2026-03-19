"""ARCANA AI — Iris: Customer Support Sub-Agent.

Handles:
- Customer inquiries about products
- Refund requests
- Technical support for purchasers
- FAQ responses

Escalation: Simple issues → Iris handles. Complex → escalate to ARCANA. Truly stuck → ping Ian/Tan.
"""

from __future__ import annotations

import logging
from typing import Any

from src.email_engine import EmailEngine
from src.llm import LLM, Tier
from src.memory import Memory
from src.notify import Notifier
from src.payments import PaymentsEngine

logger = logging.getLogger("arcana.iris")


class Iris:
    """Customer support sub-agent."""

    def __init__(
        self,
        llm: LLM,
        memory: Memory,
        email_engine: EmailEngine,
        notifier: Notifier,
        products: PaymentsEngine | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.email_engine = email_engine
        self.notifier = notifier
        self.products = products
        self._pending_inquiries: list[dict[str, Any]] = []

    async def handle_inquiry(self, customer: str, message: str, context: str = "") -> dict[str, Any]:
        """Handle a customer support inquiry."""
        # Check if we have history with this customer
        customer_history = self.memory.get_knowledge("resources", f"customer-{customer}")
        product_info = self.memory.get_knowledge("areas", "products")

        result = await self.llm.ask_json(
            f"You are Iris, the customer support agent for Arcana Operations.\n"
            f"Handle this support inquiry.\n\n"
            f"Customer: {customer}\n"
            f"Message: {message}\n"
            f"Context: {context}\n"
            f"Customer history: {customer_history[:500] if customer_history else 'New customer'}\n"
            f"Product info: {product_info[:500] if product_info else 'N/A'}\n\n"
            f"Rules:\n"
            f"- Be helpful, clear, and fast. Resolve in one message if possible.\n"
            f"- If it's a refund request, process it (or flag for manual processing).\n"
            f"- If you can't handle it, escalate.\n"
            f"- Never reveal internal systems or API keys.\n\n"
            f"Return JSON: {{"
            f'"response": str, '
            f'"action": "resolved"|"escalate"|"refund_needed"|"follow_up", '
            f'"escalate_reason": str|null, '
            f'"notes": str}}',
            tier=Tier.SONNET,
        )

        # Log the interaction
        self.memory.log(
            f"[Iris] Support: {customer} — {message[:100]}\n"
            f"Action: {result.get('action', 'unknown')}\n"
            f"Response: {result.get('response', '')[:200]}",
            "Support",
        )

        # Save customer history
        self.memory.save_knowledge(
            "resources",
            f"customer-{customer}",
            f"Customer: {customer}\n"
            f"Last inquiry: {message[:200]}\n"
            f"Resolution: {result.get('action', 'unknown')}\n"
            f"Notes: {result.get('notes', '')}",
        )

        # Actually send the response via email
        response_text = result.get("response", "")
        if response_text and "@" in customer:
            await self.email_engine.send(
                to_email=customer,
                subject="Re: Your inquiry — Arcana Operations",
                html_body=f"<p>{response_text}</p>",
                text_body=response_text,
            )

        # If escalation needed, alert Ian/Tan
        if result.get("action") == "escalate":
            await self.notifier.send(
                f"[Iris] Escalation needed — {customer}: "
                f"{result.get('escalate_reason', 'No reason given')}",
                level="error",
            )

        return result

    async def generate_faq_response(self, question: str) -> str:
        """Generate a response to a common FAQ."""
        response = await self.llm.ask(
            f"You are Iris, customer support for Arcana Operations.\n"
            f"Answer this FAQ concisely and helpfully:\n\n"
            f"Question: {question}\n\n"
            f"Keep it under 3 sentences. Be clear and direct.",
            tier=Tier.HAIKU,
            max_tokens=200,
        )
        return response.strip()

    async def handle_email_inquiry(
        self, from_email: str, subject: str, body: str,
    ) -> dict[str, Any]:
        """Process an incoming customer email, generate a response, and send the reply."""
        result = await self.handle_inquiry(
            customer=from_email,
            message=body,
            context=f"Email subject: {subject}",
        )

        # handle_inquiry already sends via email_engine, but use the original
        # subject for threading if the generic reply wasn't sent (no @ in customer)
        response_text = result.get("response", "")
        if response_text and "@" not in from_email:
            # Fallback: shouldn't happen for emails, but guard anyway
            logger.warning("Email inquiry from non-email address: %s", from_email)

        return result

    async def handle_discord_inquiry(
        self, user_id: str, username: str, message: str,
    ) -> dict[str, Any]:
        """Process a Discord DM support inquiry and respond via Discord."""
        result = await self.handle_inquiry(
            customer=username,
            message=message,
            context=f"Discord DM from user {user_id}",
        )

        response_text = result.get("response", "")
        if response_text:
            await self.notifier.send(
                f"[Iris → {username}] {response_text[:1500]}",
                level="info",
            )

        return result

    async def handle_refund_request(
        self, customer: str, product: str, reason: str,
        order_id: str = "", amount: float = 0.0,
    ) -> dict[str, Any]:
        """Process a refund request: evaluate, notify payments, send confirmation."""
        result = await self.llm.ask_json(
            f"You are Iris, customer support for Arcana Operations.\n"
            f"Evaluate this refund request.\n\n"
            f"Customer: {customer}\n"
            f"Product: {product}\n"
            f"Order ID: {order_id or 'not provided'}\n"
            f"Amount: ${amount:.2f}\n"
            f"Reason: {reason}\n\n"
            f"Rules:\n"
            f"- Refunds under $50 for digital products: approve automatically.\n"
            f"- Refunds over $50 or for services: escalate to Ian/Tan.\n"
            f"- Always be empathetic and professional.\n\n"
            f"Return JSON: {{"
            f'"approved": bool, '
            f'"response": str, '
            f'"escalate": bool, '
            f'"notes": str}}',
            tier=Tier.SONNET,
        )

        action = "refund_approved" if result.get("approved") else "refund_escalated"

        self.memory.log(
            f"[Iris] Refund request: {customer} — {product} (${amount:.2f})\n"
            f"Reason: {reason[:200]}\n"
            f"Decision: {action}",
            "Support",
        )

        # Notify Ian/Tan about the refund
        await self.notifier.send(
            f"[Iris] Refund {action}: {customer} — {product} "
            f"(${amount:.2f}). Reason: {reason[:100]}",
            level="sale" if result.get("approved") else "error",
        )

        # Send confirmation email to customer
        response_text = result.get("response", "")
        if response_text and "@" in customer:
            await self.email_engine.send(
                to_email=customer,
                subject=f"Re: Refund request — {product}",
                html_body=f"<p>{response_text}</p>",
                text_body=response_text,
            )

        # Save to customer history
        self.memory.save_knowledge(
            "resources",
            f"customer-{customer}",
            f"Customer: {customer}\n"
            f"Refund request: {product} (${amount:.2f})\n"
            f"Decision: {action}\n"
            f"Notes: {result.get('notes', '')}",
        )

        return {**result, "action": action}

    def queue_inquiry(
        self, channel: str, customer: str, message: str, **kwargs: Any,
    ) -> None:
        """Add an inquiry to the pending queue for batch processing."""
        self._pending_inquiries.append({
            "channel": channel,
            "customer": customer,
            "message": message,
            **kwargs,
        })

    async def auto_respond_queue(self) -> list[dict[str, Any]]:
        """Process all pending customer inquiries from the queue."""
        results: list[dict[str, Any]] = []
        while self._pending_inquiries:
            inquiry = self._pending_inquiries.pop(0)
            channel = inquiry.pop("channel", "email")
            customer = inquiry.pop("customer", "")
            message = inquiry.pop("message", "")

            try:
                if channel == "email":
                    result = await self.handle_email_inquiry(
                        from_email=customer,
                        subject=inquiry.get("subject", "Support inquiry"),
                        body=message,
                    )
                elif channel == "discord":
                    result = await self.handle_discord_inquiry(
                        user_id=inquiry.get("user_id", ""),
                        username=customer,
                        message=message,
                    )
                elif channel == "refund":
                    result = await self.handle_refund_request(
                        customer=customer,
                        product=inquiry.get("product", "Unknown"),
                        reason=message,
                        order_id=inquiry.get("order_id", ""),
                        amount=inquiry.get("amount", 0.0),
                    )
                else:
                    result = await self.handle_inquiry(customer, message)

                results.append({"customer": customer, "channel": channel, **result})
            except Exception as exc:
                logger.error("Failed to process inquiry from %s: %s", customer, exc)
                results.append({
                    "customer": customer,
                    "channel": channel,
                    "action": "error",
                    "error": str(exc),
                })

        self.memory.log(
            f"[Iris] Auto-responded to {len(results)} queued inquiries", "Support",
        )
        return results

    async def nightly_report(self) -> str:
        """Generate nightly support report for ARCANA to review."""
        today = self.memory.get_today()

        # Extract support entries from today
        support_lines = [
            line for line in today.splitlines()
            if "[Iris]" in line or "Support" in line
        ]

        if not support_lines:
            report = "No support tickets today."
        else:
            report = await self.llm.ask(
                f"Summarize today's customer support activity:\n\n"
                f"{chr(10).join(support_lines[:20])}\n\n"
                f"Include: tickets handled, common issues, anything that needed escalation, "
                f"and suggestions for reducing future support load.",
                tier=Tier.HAIKU,
                max_tokens=300,
            )
            report = report.strip()

        # Actually send the report to Discord/Telegram
        await self.notifier.send(f"[Iris Nightly Report]\n{report}", level="report")

        return report
