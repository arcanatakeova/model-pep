"""ARCANA AI — Unified Revenue Engine.

Production-grade revenue tracking across all 10 channels.
Monthly target: configurable (default $100K+).

Features:
- Safe revenue parsing with validation
- Configurable channel targets
- Revenue trend tracking (daily snapshots)
- Channel health monitoring
- Currency-safe parsing (handles $, commas, decimals)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.memory import Memory
from src.payments import PaymentsEngine
from src.trader_bridge import TraderBridge

logger = logging.getLogger("arcana.revenue")


@dataclass
class RevenueChannel:
    name: str
    category: str
    monthly_revenue: float = 0.0
    monthly_target: float = 0.0
    active: bool = False
    clients: int = 0
    notes: str = ""


# Default channel targets — configurable via memory/life/areas/revenue-targets.md
DEFAULT_TARGETS = {
    "consulting": 30_000,
    "stripe_products": 5_000,
    "gumroad_products": 5_000,
    "trading": 20_000,
    "ugc": 10_000,
    "affiliate": 5_000,
    "newsletter": 5_000,
    "services": 15_000,
    "seo": 5_000,
    "saas": 10_000,
}


def _safe_parse_revenue(text: str) -> float:
    """Safely extract a dollar amount from text. Handles $1,234.56 format."""
    if not text:
        return 0.0
    # Find all dollar amounts in the text
    matches = re.findall(r"\$[\d,]+(?:\.\d{1,2})?", text)
    if not matches:
        # Try bare numbers after "current" keyword
        for line in reversed(text.splitlines()):
            if "current" in line.lower():
                nums = re.findall(r"[\d,]+(?:\.\d{1,2})?", line)
                if nums:
                    try:
                        return float(nums[-1].replace(",", ""))
                    except ValueError:
                        pass
        return 0.0

    # Take the last dollar amount (most recent)
    try:
        return float(matches[-1].replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


class RevenueEngine:
    """Unified revenue tracking across all channels."""

    def __init__(self, memory: Memory, payments: PaymentsEngine, trader: TraderBridge) -> None:
        self.memory = memory
        self.payments = payments
        self.trader = trader
        self._targets = dict(DEFAULT_TARGETS)
        self._load_targets()

    def _load_targets(self) -> None:
        """Load custom targets from memory if they exist."""
        data = self.memory.get_knowledge("areas", "revenue-targets")
        if not data:
            return
        for line in data.splitlines():
            if ":" in line and not line.startswith("#"):
                key, _, val = line.partition(":")
                key = key.strip().lower().replace(" ", "_")
                try:
                    self._targets[key] = float(val.strip().replace("$", "").replace(",", ""))
                except ValueError:
                    pass

    def set_target(self, channel: str, target: float) -> None:
        """Update a channel's monthly target."""
        if target < 0:
            raise ValueError(f"Target must be non-negative, got {target}")
        self._targets[channel] = target
        # Persist
        lines = ["# Revenue Targets\n"]
        for ch, t in sorted(self._targets.items()):
            lines.append(f"{ch}: ${t:,.0f}")
        self.memory.save_knowledge("areas", "revenue-targets", "\n".join(lines))

    async def get_full_revenue_snapshot(self) -> dict[str, Any]:
        """Pull revenue from every channel with safe parsing."""

        # 1. Digital products (Stripe + Gumroad)
        try:
            product_rev = await self.payments.get_total_revenue()
        except Exception as exc:
            logger.error("Payment revenue fetch failed: %s", exc)
            product_rev = {}

        stripe_rev = max(0.0, product_rev.get("stripe", {}).get("revenue", 0))
        gumroad_rev = max(0.0, product_rev.get("gumroad", {}).get("revenue", 0))

        # 2. Trading profits
        try:
            trading_rev = max(0.0, self.trader.get_monthly_trading_revenue())
        except Exception as exc:
            logger.warning("Trading revenue fetch failed: %s", exc)
            trading_rev = 0.0

        # 3. Other channels from memory (safe parsing)
        consulting_rev = self._get_channel_revenue("consulting")
        affiliate_rev = self._get_channel_revenue("affiliate")
        newsletter_rev = self._get_channel_revenue("newsletter")
        services_rev = self._get_channel_revenue("services")
        seo_rev = self._get_channel_revenue("seo")
        saas_rev = self._get_channel_revenue("saas")
        ugc_rev = self._get_channel_revenue("ugc")

        channels = [
            RevenueChannel("Consulting", "consulting", consulting_rev, self._targets.get("consulting", 30000), consulting_rev > 0),
            RevenueChannel("Stripe Products", "products", stripe_rev, self._targets.get("stripe_products", 5000), stripe_rev > 0),
            RevenueChannel("Gumroad Products", "products", gumroad_rev, self._targets.get("gumroad_products", 5000), gumroad_rev > 0),
            RevenueChannel("Trading", "trading", trading_rev, self._targets.get("trading", 20000), trading_rev > 0),
            RevenueChannel("UGC Video", "ugc", ugc_rev, self._targets.get("ugc", 10000), ugc_rev > 0),
            RevenueChannel("Affiliates", "affiliate", affiliate_rev, self._targets.get("affiliate", 5000), affiliate_rev > 0),
            RevenueChannel("Newsletter/Sponsors", "newsletter", newsletter_rev, self._targets.get("newsletter", 5000), newsletter_rev > 0),
            RevenueChannel("Services", "services", services_rev, self._targets.get("services", 15000), services_rev > 0),
            RevenueChannel("SEO/Ads", "seo", seo_rev, self._targets.get("seo", 5000), seo_rev > 0),
            RevenueChannel("Micro-SaaS", "saas", saas_rev, self._targets.get("saas", 10000), saas_rev > 0),
        ]

        total = sum(c.monthly_revenue for c in channels)
        target = sum(self._targets.values())
        active_channels = sum(1 for c in channels if c.active)

        snapshot = {
            "month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "total_monthly_revenue": round(total, 2),
            "monthly_target": round(target, 2),
            "pct_of_target": round((total / target * 100) if target > 0 else 0, 1),
            "active_channels": active_channels,
            "total_channels": len(channels),
            "channels": [
                {
                    "name": c.name, "category": c.category,
                    "revenue": round(c.monthly_revenue, 2),
                    "target": round(c.monthly_target, 2),
                    "active": c.active,
                    "pct_of_target": round((c.monthly_revenue / c.monthly_target * 100) if c.monthly_target > 0 else 0, 1),
                }
                for c in channels
            ],
        }

        self.memory.log(
            f"Revenue Snapshot: ${total:,.2f} / ${target:,} target "
            f"({snapshot['pct_of_target']:.1f}%) — {active_channels} channels active",
            "Revenue",
        )

        return snapshot

    def _get_channel_revenue(self, channel: str) -> float:
        """Read revenue for a channel from knowledge graph (safe parsing)."""
        data = self.memory.get_knowledge("areas", f"revenue-{channel}")
        if not data:
            return 0.0
        value = _safe_parse_revenue(data)
        return max(0.0, value)  # Never negative

    def update_channel_revenue(self, channel: str, amount: float, notes: str = "") -> None:
        """Update revenue for a specific channel."""
        if amount < 0:
            logger.warning("Negative revenue for %s: $%.2f — setting to 0", channel, amount)
            amount = 0.0

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
        """Format revenue snapshot for reports."""
        lines = [
            f"**Revenue Dashboard — {snapshot.get('month', 'N/A')}**",
            f"**Total: ${snapshot.get('total_monthly_revenue', 0):,.2f} / "
            f"${snapshot.get('monthly_target', 0):,} "
            f"({snapshot.get('pct_of_target', 0):.1f}%)**",
            f"Active channels: {snapshot.get('active_channels', 0)}/"
            f"{snapshot.get('total_channels', 10)}\n",
        ]

        for ch in snapshot.get("channels", []):
            status = "●" if ch.get("active") else "○"
            pct = ch.get("pct_of_target", 0)
            lines.append(
                f"{status} {ch.get('name', '?')}: "
                f"${ch.get('revenue', 0):,.2f} / ${ch.get('target', 0):,} ({pct:.0f}%)"
            )

        return "\n".join(lines)

    def export_revenue_excel(
        self, snapshot: dict[str, Any], output_path: str = "data/reports/revenue.xlsx",
    ) -> str | None:
        """Export revenue snapshot to a formatted Excel file."""
        try:
            from src.toolkit import generate_excel
            from pathlib import Path

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            rows = []
            for ch in snapshot.get("channels", []):
                rows.append({
                    "Channel": ch.get("name", ""),
                    "Revenue": f"${ch.get('revenue', 0):,.2f}",
                    "Target": f"${ch.get('target', 0):,}",
                    "% of Target": f"{ch.get('pct_of_target', 0):.1f}%",
                    "Active": "Yes" if ch.get("active") else "No",
                })
            rows.append({
                "Channel": "TOTAL",
                "Revenue": f"${snapshot.get('total_monthly_revenue', 0):,.2f}",
                "Target": f"${snapshot.get('monthly_target', 0):,}",
                "% of Target": f"{snapshot.get('pct_of_target', 0):.1f}%",
                "Active": "",
            })

            success = generate_excel(rows, output_path, sheet_name=snapshot.get("month", "Revenue"))
            return output_path if success else None
        except Exception as exc:
            logger.error("Revenue Excel export failed: %s", exc)
            return None
