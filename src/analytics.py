"""ARCANA AI — Analytics & Attribution.

Track what's working, what's not, and where every dollar comes from.

Tracks:
- Content performance (which tweets drive leads/sales)
- Channel attribution (X vs outreach vs scanner vs organic)
- Conversion funnel (impression → click → lead → client → revenue)
- ROI by channel (cost vs revenue per channel)
- Client LTV and CAC
- Service delivery metrics (time-to-value, satisfaction)
- A/B test results (subject lines, hooks, pricing)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.analytics")


class Analytics:
    """Track performance across all revenue channels."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    # ── Event Tracking ──────────────────────────────────────────────

    def track(self, event: str, properties: dict[str, Any] | None = None) -> None:
        """Track any event with properties."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        props = properties or {}
        entry = f"{ts} | {event} | {props}"

        # Append to daily analytics log
        existing = self.memory.get_tacit("analytics-log") or ""
        updated = existing + "\n" + entry if existing else entry

        # Keep last 500 entries
        lines = updated.strip().splitlines()
        if len(lines) > 500:
            lines = lines[-500:]

        self.memory.save_tacit("analytics-log", "\n".join(lines))

    def track_lead(self, source: str, service: str, value: float) -> None:
        self.track("lead_created", {"source": source, "service": service, "value": value})

    def track_conversion(self, source: str, service: str, value: float) -> None:
        self.track("conversion", {"source": source, "service": service, "value": value})

    def track_content(self, content_type: str, platform: str, engagement: int = 0) -> None:
        self.track("content_posted", {"type": content_type, "platform": platform, "engagement": engagement})

    def track_revenue(self, channel: str, amount: float) -> None:
        self.track("revenue", {"channel": channel, "amount": amount})

    def track_cost(self, category: str, amount: float) -> None:
        self.track("cost", {"category": category, "amount": amount})

    # ── Funnel Analysis ─────────────────────────────────────────────

    def get_funnel_metrics(self) -> dict[str, Any]:
        """Calculate conversion funnel metrics from tracked events."""
        log = self.memory.get_tacit("analytics-log") or ""

        counts = {
            "content_posted": 0,
            "lead_created": 0,
            "conversion": 0,
            "revenue": 0.0,
        }

        for line in log.splitlines():
            if "content_posted" in line:
                counts["content_posted"] += 1
            elif "lead_created" in line:
                counts["lead_created"] += 1
            elif "conversion" in line:
                counts["conversion"] += 1
            elif "revenue" in line:
                try:
                    # Extract amount from the properties
                    if "'amount':" in line:
                        amount_str = line.split("'amount':")[1].split(",")[0].split("}")[0].strip()
                        counts["revenue"] += float(amount_str)
                except (ValueError, IndexError):
                    pass

        # Calculate conversion rates
        content_to_lead = (counts["lead_created"] / counts["content_posted"] * 100) if counts["content_posted"] else 0
        lead_to_client = (counts["conversion"] / counts["lead_created"] * 100) if counts["lead_created"] else 0

        return {
            "content_posted": counts["content_posted"],
            "leads_generated": counts["lead_created"],
            "conversions": counts["conversion"],
            "total_revenue": counts["revenue"],
            "content_to_lead_rate": f"{content_to_lead:.1f}%",
            "lead_to_client_rate": f"{lead_to_client:.1f}%",
        }

    # ── Channel Attribution ─────────────────────────────────────────

    def get_channel_attribution(self) -> dict[str, dict[str, Any]]:
        """Break down performance by acquisition channel."""
        log = self.memory.get_tacit("analytics-log") or ""

        channels: dict[str, dict[str, Any]] = {}

        for line in log.splitlines():
            if "lead_created" in line or "conversion" in line:
                # Extract source
                source = "unknown"
                if "'source':" in line:
                    try:
                        source = line.split("'source':")[1].split(",")[0].split("'")[1]
                    except (IndexError, ValueError):
                        pass

                if source not in channels:
                    channels[source] = {"leads": 0, "conversions": 0, "revenue": 0.0}

                if "lead_created" in line:
                    channels[source]["leads"] += 1
                elif "conversion" in line:
                    channels[source]["conversions"] += 1
                    # Try to extract value
                    if "'value':" in line:
                        try:
                            val = float(line.split("'value':")[1].split(",")[0].split("}")[0].strip())
                            channels[source]["revenue"] += val
                        except (ValueError, IndexError):
                            pass

        return channels

    # ── ROI Analysis ────────────────────────────────────────────────

    async def generate_roi_report(self) -> dict[str, Any]:
        """Generate comprehensive ROI report across all channels."""
        funnel = self.get_funnel_metrics()
        channels = self.get_channel_attribution()

        # Get cost data
        log = self.memory.get_tacit("analytics-log") or ""
        total_costs = 0.0
        for line in log.splitlines():
            if "'cost'" in line or "cost" in line.split("|")[1] if "|" in line and len(line.split("|")) > 1 else False:
                try:
                    if "'amount':" in line:
                        total_costs += float(line.split("'amount':")[1].split(",")[0].split("}")[0].strip())
                except (ValueError, IndexError):
                    pass

        result = await self.llm.ask_json(
            f"Generate an ROI analysis for ARCANA AI's operations.\n\n"
            f"Funnel metrics:\n{funnel}\n\n"
            f"Channel attribution:\n{channels}\n\n"
            f"Total costs: ${total_costs:.2f}\n\n"
            f"Analyze:\n"
            f"1. Which channels have the best ROI?\n"
            f"2. Where should ARCANA invest more?\n"
            f"3. What's the customer acquisition cost?\n"
            f"4. What's the estimated LTV?\n"
            f"5. What should ARCANA stop doing?\n\n"
            f"Return JSON: {{"
            f'"total_revenue": float, "total_cost": float, "roi_pct": float, '
            f'"best_channel": str, "worst_channel": str, '
            f'"cac": float, "estimated_ltv": float, '
            f'"recommendations": [str]}}',
            tier=Tier.SONNET,
        )
        return result

    # ── Reporting ───────────────────────────────────────────────────

    def format_analytics_report(self) -> str:
        """Format analytics for morning/nightly report."""
        funnel = self.get_funnel_metrics()
        channels = self.get_channel_attribution()

        lines = [
            f"**Analytics**",
            f"Content: {funnel['content_posted']} | Leads: {funnel['leads_generated']} | "
            f"Conversions: {funnel['conversions']}",
            f"Content→Lead: {funnel['content_to_lead_rate']} | Lead→Client: {funnel['lead_to_client_rate']}",
            f"Revenue tracked: ${funnel['total_revenue']:,.2f}",
        ]

        if channels:
            top_channels = sorted(channels.items(), key=lambda x: -x[1].get("leads", 0))[:3]
            lines.append("Top channels: " + ", ".join(
                f"{ch}({data['leads']}L/{data['conversions']}C)"
                for ch, data in top_channels
            ))

        return "\n".join(lines)
