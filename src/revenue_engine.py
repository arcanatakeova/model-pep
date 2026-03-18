"""ARCANA AI — Unified Revenue Engine.

Tracks ALL revenue channels in one place. Morning reports show the full picture.
Monthly target: $100K+ across all channels combined.

Channels:
1. Consulting (Arcana Operations) — $2-10K/client/mo
2. Digital products (Stripe + Gumroad) — $29-299 each
3. Trading profits (trader/) — variable
4. Affiliate commissions — 5-80% depending on program
5. Newsletter sponsors (Beehiiv) — $200-2K/placement
6. Service delivery (chatbots, reviews, social, lead gen) — $300-5K/client/mo
7. Programmatic SEO (AdSense/Mediavine) — $15-40 RPM
8. Micro-SaaS subscriptions — $10-100/user/mo
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.memory import Memory
from src.products import ProductManager
from src.trader_bridge import TraderBridge

logger = logging.getLogger("arcana.revenue")


@dataclass
class RevenueChannel:
    name: str
    category: str  # consulting, products, trading, affiliate, services, seo, saas
    monthly_revenue: float = 0.0
    monthly_target: float = 0.0
    active: bool = False
    clients: int = 0
    notes: str = ""


class RevenueEngine:
    """Unified revenue tracking across all channels."""

    def __init__(self, memory: Memory, products: ProductManager, trader: TraderBridge) -> None:
        self.memory = memory
        self.products = products
        self.trader = trader

    async def get_full_revenue_snapshot(self) -> dict[str, Any]:
        """Pull revenue from every channel. The complete picture."""

        # 1. Digital products (Stripe + Gumroad)
        product_rev = await self.products.get_revenue_summary()
        stripe_rev = product_rev.get("stripe", {}).get("revenue", 0)
        gumroad_rev = product_rev.get("gumroad", {}).get("revenue", 0)

        # 2. Trading profits
        trading_rev = self.trader.get_monthly_trading_revenue()

        # 3. Other channels from memory (manually updated or from integrations)
        consulting_rev = self._get_channel_from_memory("consulting")
        affiliate_rev = self._get_channel_from_memory("affiliate")
        newsletter_rev = self._get_channel_from_memory("newsletter")
        services_rev = self._get_channel_from_memory("services")
        seo_rev = self._get_channel_from_memory("seo")
        saas_rev = self._get_channel_from_memory("saas")
        ugc_rev = self._get_channel_from_memory("ugc")

        channels = [
            RevenueChannel("Consulting", "consulting", consulting_rev, 30000, consulting_rev > 0),
            RevenueChannel("Stripe Products", "products", stripe_rev, 5000, stripe_rev > 0),
            RevenueChannel("Gumroad Products", "products", gumroad_rev, 5000, gumroad_rev > 0),
            RevenueChannel("Trading", "trading", trading_rev, 20000, trading_rev > 0),
            RevenueChannel("UGC Video", "ugc", ugc_rev, 10000, ugc_rev > 0),
            RevenueChannel("Affiliates", "affiliate", affiliate_rev, 5000, affiliate_rev > 0),
            RevenueChannel("Newsletter/Sponsors", "newsletter", newsletter_rev, 5000, newsletter_rev > 0),
            RevenueChannel("Services", "services", services_rev, 15000, services_rev > 0),
            RevenueChannel("SEO/Ads", "seo", seo_rev, 5000, seo_rev > 0),
            RevenueChannel("Micro-SaaS", "saas", saas_rev, 10000, saas_rev > 0),
        ]

        total = sum(c.monthly_revenue for c in channels)
        target = 110_000  # $100K+ across 10 channels
        active_channels = sum(1 for c in channels if c.active)

        snapshot = {
            "month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "total_monthly_revenue": total,
            "monthly_target": target,
            "pct_of_target": (total / target * 100) if target else 0,
            "active_channels": active_channels,
            "channels": [
                {
                    "name": c.name,
                    "category": c.category,
                    "revenue": c.monthly_revenue,
                    "target": c.monthly_target,
                    "active": c.active,
                }
                for c in channels
            ],
        }

        # Log to memory
        self.memory.log(
            f"Revenue Snapshot: ${total:,.2f} / ${target:,} target "
            f"({snapshot['pct_of_target']:.1f}%) — {active_channels} channels active",
            "Revenue",
        )

        return snapshot

    def _get_channel_from_memory(self, channel: str) -> float:
        """Read revenue for a channel from knowledge graph."""
        data = self.memory.get_knowledge("areas", f"revenue-{channel}")
        if not data:
            return 0.0
        # Parse the last line that looks like "Current month: $X"
        for line in reversed(data.splitlines()):
            if "current" in line.lower() and "$" in line:
                try:
                    amount = line.split("$")[1].split()[0].replace(",", "")
                    return float(amount)
                except (IndexError, ValueError):
                    pass
        return 0.0

    def update_channel_revenue(self, channel: str, amount: float, notes: str = "") -> None:
        """Update revenue for a specific channel."""
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        self.memory.save_knowledge(
            "areas",
            f"revenue-{channel}",
            f"# Revenue: {channel}\n\n"
            f"Current month ({month}): ${amount:,.2f}\n"
            f"Notes: {notes}\n",
        )
        self.memory.log(f"Revenue update: {channel} = ${amount:,.2f} {notes}", "Revenue")

    def format_revenue_report(self, snapshot: dict[str, Any]) -> str:
        """Format revenue snapshot for morning report."""
        lines = [
            f"**Revenue Dashboard — {snapshot['month']}**",
            f"**Total: ${snapshot['total_monthly_revenue']:,.2f} / ${snapshot['monthly_target']:,} "
            f"({snapshot['pct_of_target']:.1f}%)**",
            f"Active channels: {snapshot['active_channels']}/9\n",
        ]

        for ch in snapshot["channels"]:
            status = "●" if ch["active"] else "○"
            pct = (ch["revenue"] / ch["target"] * 100) if ch["target"] else 0
            lines.append(
                f"{status} {ch['name']}: ${ch['revenue']:,.2f} / ${ch['target']:,} ({pct:.0f}%)"
            )

        return "\n".join(lines)
