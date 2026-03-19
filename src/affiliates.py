"""ARCANA AI — Affiliate Link Management.

Auto-injects affiliate links into content. Links go in REPLIES (never main tweet).
Tracks clicks/conversions in memory. Manages multiple affiliate programs.

Programs:
- MEXC: 80% commission on trading fees
- Bybit: 50% commission
- Coinbase: $10 per referral
- Amazon Associates: 4-8% on products
- Gumroad affiliate: 10% on referred sales
- Tool affiliates: Varies (hosting, SaaS, AI tools)
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.affiliates")


# Affiliate programs — add new ones here
AFFILIATE_PROGRAMS: dict[str, dict[str, Any]] = {
    "mexc": {
        "name": "MEXC Exchange",
        "commission": "80% trading fees",
        "link_template": "https://www.mexc.com/register?inviteCode={code}",
        "categories": ["crypto", "trading", "exchange"],
    },
    "bybit": {
        "name": "Bybit Exchange",
        "commission": "50% trading fees",
        "link_template": "https://www.bybit.com/invite?ref={code}",
        "categories": ["crypto", "trading", "exchange"],
    },
    "coinbase": {
        "name": "Coinbase",
        "commission": "$10 per referral",
        "link_template": "https://coinbase.com/join/{code}",
        "categories": ["crypto", "beginner", "exchange"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "commission": "Varies",
        "link_template": "https://openrouter.ai/?ref={code}",
        "categories": ["ai", "llm", "tools"],
    },
    "vercel": {
        "name": "Vercel",
        "commission": "Varies",
        "link_template": "https://vercel.com/?ref={code}",
        "categories": ["hosting", "deployment", "tools"],
    },
    "beehiiv": {
        "name": "Beehiiv",
        "commission": "20% recurring",
        "link_template": "https://beehiiv.com/?via={code}",
        "categories": ["newsletter", "email", "tools"],
    },
    "stripe": {
        "name": "Stripe Atlas",
        "commission": "Varies",
        "link_template": "https://stripe.com/atlas?ref={code}",
        "categories": ["payments", "business", "tools"],
    },
    "gumroad": {
        "name": "Gumroad",
        "commission": "10% on referrals",
        "link_template": "https://gumroad.com/?ref={code}",
        "categories": ["products", "digital", "tools"],
    },
}


class AffiliateManager:
    """Manage affiliate links and inject them into content."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory
        self._codes: dict[str, str] = {}
        self._load_codes()

    def _load_codes(self) -> None:
        """Load affiliate codes from memory."""
        data = self.memory.get_knowledge("areas", "affiliate-codes")
        if not data:
            return
        for line in data.splitlines():
            if ":" in line and not line.startswith("#"):
                key, _, code = line.partition(":")
                key = key.strip().lower()
                code = code.strip()
                if key and code:
                    self._codes[key] = code

    def set_code(self, program: str, code: str) -> None:
        """Set affiliate code for a program."""
        self._codes[program.lower()] = code
        # Save all codes
        lines = [f"# Affiliate Codes\n"]
        for k, v in self._codes.items():
            lines.append(f"{k}: {v}")
        self.memory.save_knowledge("areas", "affiliate-codes", "\n".join(lines))

    def get_link(self, program: str) -> str | None:
        """Get full affiliate link for a program (validated)."""
        prog = program.lower()
        if prog not in AFFILIATE_PROGRAMS or prog not in self._codes:
            return None
        code = self._codes[prog]
        # Validate code doesn't contain injection characters
        if not code or any(c in code for c in [" ", "<", ">", '"', "'"]):
            logger.warning("Invalid affiliate code for %s: %s", prog, code[:20])
            return None
        template = AFFILIATE_PROGRAMS[prog]["link_template"]
        link = template.format(code=code)
        # Validate resulting URL
        if not link.startswith("https://"):
            logger.warning("Non-HTTPS affiliate link generated: %s", link[:50])
            return None
        return link

    async def find_relevant_affiliate(self, content: str) -> dict[str, Any] | None:
        """Given tweet content, find the best affiliate link to add in reply."""
        available = [
            f"- {k}: {v['name']} ({v['commission']}) — categories: {', '.join(v['categories'])}"
            for k, v in AFFILIATE_PROGRAMS.items()
            if k in self._codes
        ]

        if not available:
            return None

        result = await self.llm.ask_json(
            f"Given this tweet content, pick the BEST affiliate link to add in a reply.\n\n"
            f"Tweet: {content}\n\n"
            f"Available affiliate programs:\n{chr(10).join(available)}\n\n"
            f"Rules:\n"
            f"- Only suggest if genuinely relevant (don't force it)\n"
            f"- The link goes in a REPLY, not the main tweet\n"
            f"- Write a natural 1-sentence reply that includes the link context\n\n"
            f"Return JSON: {{"
            f'"has_match": bool, '
            f'"program": str|null, '
            f'"reply_text": str|null (the reply that naturally mentions the tool)}}',
            tier=Tier.HAIKU,
            max_tokens=150,
        )

        if not result.get("has_match"):
            return None

        program = result.get("program", "").lower()
        link = self.get_link(program)
        if not link:
            return None

        return {
            "program": program,
            "link": link,
            "reply_text": f"{result.get('reply_text', '')} {link}",
        }

    def log_click(self, program: str) -> None:
        """Log an affiliate click/conversion."""
        self.memory.log(f"Affiliate click: {program}", "Affiliate")

    def get_revenue_estimate(self) -> float:
        """Estimate monthly affiliate revenue from memory logs (safe parsing)."""
        import re
        data = self.memory.get_knowledge("areas", "revenue-affiliate")
        if not data:
            return 0.0
        # Find all dollar amounts, take the last one
        matches = re.findall(r"\$[\d,]+(?:\.\d{1,2})?", data)
        if matches:
            try:
                return max(0.0, float(matches[-1].replace("$", "").replace(",", "")))
            except ValueError:
                pass
        return 0.0
