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

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.iris")


class Iris:
    """Customer support sub-agent."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

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

    async def nightly_report(self) -> str:
        """Generate nightly support report for ARCANA to review."""
        today = self.memory.get_today()

        # Extract support entries from today
        support_lines = [
            line for line in today.splitlines()
            if "[Iris]" in line or "Support" in line
        ]

        if not support_lines:
            return "No support tickets today."

        report = await self.llm.ask(
            f"Summarize today's customer support activity:\n\n"
            f"{chr(10).join(support_lines[:20])}\n\n"
            f"Include: tickets handled, common issues, anything that needed escalation, "
            f"and suggestions for reducing future support load.",
            tier=Tier.HAIKU,
            max_tokens=300,
        )
        return report.strip()
