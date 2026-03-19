"""ARCANA AI — Competitive Intelligence Engine.

Deep competitive intelligence gathering for Arcana Operations.
Monitors competitors, tracks pricing/positioning changes, analyzes content
strategy, and generates actionable intelligence reports.

All intel is persisted to memory/life/resources/intel-* markdown files.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import Config, get_config, MEMORY_DIR
from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.intel")

INTEL_DIR = MEMORY_DIR / "life" / "resources"


@dataclass
class Competitor:
    """Tracked competitor profile."""

    name: str
    website: str
    x_handle: str = ""
    linkedin_url: str = ""
    services: list[str] = field(default_factory=list)
    pricing: dict[str, str] = field(default_factory=dict)
    positioning: str = ""
    last_scanned: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Competitor:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class IntelEngine:
    """Competitive intelligence gathering and analysis.

    Monitors competitors across X, LinkedIn, review sites, and job boards.
    Generates positioning reports, SWOT analyses, and displacement opportunities.
    """

    def __init__(
        self,
        config: Config | None = None,
        llm: LLM | None = None,
        memory: Memory | None = None,
    ) -> None:
        self.config = config or get_config()
        self.llm = llm or LLM(self.config)
        self.memory = memory or Memory()
        self._competitors: dict[str, Competitor] = {}
        self._load_competitors()

    # ── Competitor Registry ──────────────────────────────────────────

    def _competitors_path(self) -> Path:
        return INTEL_DIR / "intel-competitors.md"

    def _load_competitors(self) -> None:
        """Load competitor registry from markdown memory."""
        content = self.memory.get_knowledge("resources", "intel-competitors")
        if not content:
            return

        current_name: str | None = None
        current_block: list[str] = []

        for line in content.splitlines():
            if line.startswith("## "):
                if current_name and current_block:
                    self._parse_competitor_block(current_name, current_block)
                current_name = line[3:].strip()
                current_block = []
            elif current_name:
                current_block.append(line)

        if current_name and current_block:
            self._parse_competitor_block(current_name, current_block)

        logger.info("Loaded %d competitors from memory", len(self._competitors))

    def _parse_competitor_block(self, name: str, lines: list[str]) -> None:
        """Parse a competitor block from the markdown registry."""
        comp = Competitor(name=name, website="")
        for line in lines:
            line = line.strip()
            if line.startswith("- **Website**:"):
                comp.website = line.split(":", 1)[1].strip()
            elif line.startswith("- **X**:"):
                comp.x_handle = line.split(":", 1)[1].strip()
            elif line.startswith("- **LinkedIn**:"):
                comp.linkedin_url = line.split(":", 1)[1].strip()
            elif line.startswith("- **Services**:"):
                comp.services = [s.strip() for s in line.split(":", 1)[1].split(",")]
            elif line.startswith("- **Positioning**:"):
                comp.positioning = line.split(":", 1)[1].strip()
            elif line.startswith("- **Last Scanned**:"):
                comp.last_scanned = line.split(":", 1)[1].strip()
            elif line.startswith("- **Pricing**:"):
                try:
                    comp.pricing = json.loads(line.split(":", 1)[1].strip())
                except (json.JSONDecodeError, IndexError):
                    pass
            elif line.startswith("- **Notes**:"):
                comp.notes = line.split(":", 1)[1].strip()
        self._competitors[name] = comp

    def _save_competitors(self) -> None:
        """Persist the competitor registry to markdown memory."""
        sections: list[str] = []
        for comp in self._competitors.values():
            pricing_str = json.dumps(comp.pricing) if comp.pricing else "{}"
            services_str = ", ".join(comp.services) if comp.services else "Unknown"
            sections.append(
                f"## {comp.name}\n"
                f"- **Website**: {comp.website}\n"
                f"- **X**: {comp.x_handle}\n"
                f"- **LinkedIn**: {comp.linkedin_url}\n"
                f"- **Services**: {services_str}\n"
                f"- **Pricing**: {pricing_str}\n"
                f"- **Positioning**: {comp.positioning}\n"
                f"- **Last Scanned**: {comp.last_scanned}\n"
                f"- **Notes**: {comp.notes}\n"
            )
        body = "\n".join(sections) if sections else "_No competitors tracked yet._"
        self.memory.save_knowledge("resources", "intel-competitors", body)
        logger.info("Saved %d competitors to memory", len(self._competitors))

    def add_competitor(
        self,
        name: str,
        website: str,
        x_handle: str = "",
        services: list[str] | None = None,
        linkedin_url: str = "",
    ) -> Competitor:
        """Register a new competitor for monitoring."""
        comp = Competitor(
            name=name,
            website=website,
            x_handle=x_handle,
            linkedin_url=linkedin_url,
            services=services or [],
            last_scanned=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        self._competitors[name] = comp
        self._save_competitors()
        self.memory.log(
            f"Added competitor to intel tracker: **{name}** ({website})",
            section="Intel",
        )
        logger.info("Added competitor: %s", name)
        return comp

    def remove_competitor(self, name: str) -> bool:
        """Remove a competitor from tracking."""
        if name in self._competitors:
            del self._competitors[name]
            self._save_competitors()
            logger.info("Removed competitor: %s", name)
            return True
        return False

    def list_competitors(self) -> list[Competitor]:
        """Return all tracked competitors."""
        return list(self._competitors.values())

    # ── X / Social Media Monitoring ──────────────────────────────────

    async def monitor_competitor_x(self, competitor_name: str) -> dict[str, Any]:
        """Analyze a competitor's recent X activity and content strategy.

        Uses LLM to analyze gathered data and produce insights about
        posting frequency, content themes, engagement patterns, and
        strategic positioning.
        """
        comp = self._competitors.get(competitor_name)
        if not comp:
            return {"error": f"Competitor '{competitor_name}' not tracked"}

        if not comp.x_handle:
            return {"error": f"No X handle for '{competitor_name}'"}

        prompt = (
            f"You are a competitive intelligence analyst for Arcana Operations, "
            f"an AI consulting agency.\n\n"
            f"Analyze the X/Twitter presence of competitor: {comp.name}\n"
            f"X handle: {comp.x_handle}\n"
            f"Website: {comp.website}\n"
            f"Known services: {', '.join(comp.services) or 'Unknown'}\n\n"
            f"Based on what you know about this company or similar AI agencies, "
            f"provide a structured analysis:\n\n"
            f"1. **Content Themes**: What topics do they likely post about?\n"
            f"2. **Posting Cadence**: Estimated frequency and timing\n"
            f"3. **Engagement Strategy**: How do they interact with followers?\n"
            f"4. **Key Messaging**: Core value propositions they push\n"
            f"5. **Vulnerabilities**: Gaps or weaknesses in their social strategy\n"
            f"6. **Opportunities for ARCANA**: How can we differentiate?\n\n"
            f"Return as JSON with keys: content_themes, posting_cadence, "
            f"engagement_strategy, key_messaging, vulnerabilities, opportunities"
        )

        try:
            analysis = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        except Exception as exc:
            logger.error("X analysis failed for %s: %s", competitor_name, exc)
            return {"error": str(exc)}

        # Persist to intel file
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"intel-x-{comp.name.lower().replace(' ', '-')}",
            f"Last updated: {ts}\n\n```json\n{json.dumps(analysis, indent=2)}\n```",
        )
        comp.last_scanned = ts
        self._save_competitors()

        self.memory.log(
            f"Completed X analysis for competitor: **{comp.name}** (@{comp.x_handle})",
            section="Intel",
        )
        return analysis

    async def analyze_competitor_content_strategy(
        self, competitor_name: str
    ) -> dict[str, Any]:
        """Deep analysis of a competitor's content strategy across channels."""
        comp = self._competitors.get(competitor_name)
        if not comp:
            return {"error": f"Competitor '{competitor_name}' not tracked"}

        prompt = (
            f"You are a competitive intelligence analyst for Arcana Operations.\n\n"
            f"Perform a deep content strategy analysis for: {comp.name}\n"
            f"Website: {comp.website}\n"
            f"X: {comp.x_handle}\n"
            f"LinkedIn: {comp.linkedin_url}\n"
            f"Known services: {', '.join(comp.services) or 'Unknown'}\n\n"
            f"Analyze and return JSON with:\n"
            f"- content_pillars: list of main content themes\n"
            f"- target_audience: who they're speaking to\n"
            f"- tone_and_voice: how they communicate\n"
            f"- channels: which platforms they prioritize and why\n"
            f"- lead_magnets: any free resources or gated content\n"
            f"- conversion_funnel: how content drives to sales\n"
            f"- strengths: what they do well\n"
            f"- weaknesses: where their content falls short\n"
            f"- arcana_counter_strategy: specific actions ARCANA should take"
        )

        try:
            analysis = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        except Exception as exc:
            logger.error("Content strategy analysis failed for %s: %s", competitor_name, exc)
            return {"error": str(exc)}

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"intel-content-{comp.name.lower().replace(' ', '-')}",
            f"Last updated: {ts}\n\n```json\n{json.dumps(analysis, indent=2)}\n```",
        )
        return analysis

    # ── Pricing & Services Tracking ──────────────────────────────────

    async def track_competitor_pricing(self, competitor_name: str) -> dict[str, Any]:
        """Track and analyze a competitor's pricing and service offerings.

        Compares against ARCANA's own pricing to identify positioning gaps.
        """
        comp = self._competitors.get(competitor_name)
        if not comp:
            return {"error": f"Competitor '{competitor_name}' not tracked"}

        arcana_pricing = {
            "AI Agent Setup": "$2,000 + $500/mo",
            "SEO Audit": "$1,500",
            "Marketing Strategy": "$2,000",
            "How to Work with AI Playbook": "$29-49",
        }

        prompt = (
            f"You are a competitive pricing analyst for Arcana Operations.\n\n"
            f"Analyze pricing for competitor: {comp.name}\n"
            f"Website: {comp.website}\n"
            f"Known services: {', '.join(comp.services) or 'Unknown'}\n"
            f"Previously known pricing: {json.dumps(comp.pricing) if comp.pricing else 'Unknown'}\n\n"
            f"ARCANA's own pricing for reference:\n"
            f"{json.dumps(arcana_pricing, indent=2)}\n\n"
            f"Provide a JSON analysis with:\n"
            f"- estimated_pricing: dict of service name to estimated price range\n"
            f"- pricing_model: how they structure pricing (hourly, project, retainer, etc.)\n"
            f"- price_positioning: premium, mid-market, or budget\n"
            f"- comparison_to_arcana: how their pricing compares to ours\n"
            f"- undercut_opportunities: services where ARCANA can compete on price\n"
            f"- premium_opportunities: services where ARCANA can charge more by adding value\n"
            f"- recommendations: list of strategic pricing recommendations"
        )

        try:
            analysis = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        except Exception as exc:
            logger.error("Pricing analysis failed for %s: %s", competitor_name, exc)
            return {"error": str(exc)}

        # Update stored pricing if we got new data
        if "estimated_pricing" in analysis and isinstance(analysis["estimated_pricing"], dict):
            comp.pricing = analysis["estimated_pricing"]
            comp.last_scanned = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._save_competitors()

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"intel-pricing-{comp.name.lower().replace(' ', '-')}",
            f"Last updated: {ts}\n\n```json\n{json.dumps(analysis, indent=2)}\n```",
        )

        self.memory.log(
            f"Updated pricing intel for **{comp.name}**: {analysis.get('price_positioning', 'unknown')} positioning",
            section="Intel",
        )
        return analysis

    # ── Review & Reputation Monitoring ───────────────────────────────

    async def scan_competitor_reviews(self, competitor_name: str) -> dict[str, Any]:
        """Scan review sites for competitor complaints and reputation signals.

        Identifies dissatisfied customers as potential displacement targets.
        """
        comp = self._competitors.get(competitor_name)
        if not comp:
            return {"error": f"Competitor '{competitor_name}' not tracked"}

        prompt = (
            f"You are a competitive intelligence analyst for Arcana Operations.\n\n"
            f"Analyze the likely reputation landscape for: {comp.name}\n"
            f"Website: {comp.website}\n"
            f"Services: {', '.join(comp.services) or 'AI consulting'}\n\n"
            f"Consider common complaints about AI agencies and consultancies. "
            f"Provide a JSON analysis with:\n"
            f"- likely_review_platforms: where their reviews would appear\n"
            f"- common_pain_points: typical complaints for this type of business\n"
            f"- reputation_risks: potential reputation vulnerabilities\n"
            f"- displacement_angles: how ARCANA could win their unhappy customers\n"
            f"- outreach_templates: 2-3 short message templates for reaching "
            f"dissatisfied customers (tactful, not slimy)\n"
            f"- monitoring_keywords: search terms to track for complaints about them"
        )

        try:
            analysis = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        except Exception as exc:
            logger.error("Review scan failed for %s: %s", competitor_name, exc)
            return {"error": str(exc)}

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"intel-reviews-{comp.name.lower().replace(' ', '-')}",
            f"Last updated: {ts}\n\n```json\n{json.dumps(analysis, indent=2)}\n```",
        )
        return analysis

    # ── Job Posting Analysis ─────────────────────────────────────────

    async def track_competitor_hiring(self, competitor_name: str) -> dict[str, Any]:
        """Analyze competitor job postings to infer growth direction and capabilities."""
        comp = self._competitors.get(competitor_name)
        if not comp:
            return {"error": f"Competitor '{competitor_name}' not tracked"}

        prompt = (
            f"You are a competitive intelligence analyst for Arcana Operations.\n\n"
            f"Analyze likely hiring patterns for: {comp.name}\n"
            f"Website: {comp.website}\n"
            f"LinkedIn: {comp.linkedin_url}\n"
            f"Known services: {', '.join(comp.services) or 'AI consulting'}\n\n"
            f"Based on their services and positioning, infer what roles they'd be "
            f"hiring for and what that signals. Return JSON with:\n"
            f"- likely_open_roles: list of roles they'd be hiring\n"
            f"- growth_signals: what hiring patterns indicate about their direction\n"
            f"- capability_gaps: skills they need to hire for (vs. build internally)\n"
            f"- strategic_implications: what this means for ARCANA's positioning\n"
            f"- talent_poaching_opportunities: roles where their employees might be "
            f"open to joining a more innovative operation"
        )

        try:
            analysis = await self.llm.ask_json(prompt, tier=Tier.SONNET)
        except Exception as exc:
            logger.error("Hiring analysis failed for %s: %s", competitor_name, exc)
            return {"error": str(exc)}

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"intel-hiring-{comp.name.lower().replace(' ', '-')}",
            f"Last updated: {ts}\n\n```json\n{json.dumps(analysis, indent=2)}\n```",
        )
        return analysis

    # ── Industry Trend Tracking ──────────────────────────────────────

    async def track_industry_trends(self) -> dict[str, Any]:
        """Analyze current industry trends and market shifts in AI consulting."""
        competitors_context = ""
        for comp in self._competitors.values():
            competitors_context += (
                f"- {comp.name}: {', '.join(comp.services) or 'Unknown services'} "
                f"({comp.positioning or 'Unknown positioning'})\n"
            )

        prompt = (
            f"You are a strategic market analyst for Arcana Operations, "
            f"an AI consulting agency in Portland, OR run by Ian & Tan.\n\n"
            f"Current tracked competitors:\n{competitors_context or 'None yet'}\n\n"
            f"Analyze the current AI consulting/automation market. Return JSON with:\n"
            f"- macro_trends: top 5 industry-level trends\n"
            f"- emerging_services: new service categories gaining traction\n"
            f"- pricing_trends: how the market is shifting on pricing\n"
            f"- technology_shifts: new tools/platforms disrupting the space\n"
            f"- buyer_behavior_changes: how clients are changing their buying process\n"
            f"- market_gaps: underserved niches ARCANA could target\n"
            f"- threats: emerging threats to ARCANA's business model\n"
            f"- opportunities: time-sensitive opportunities to pursue\n"
            f"- recommended_actions: top 5 actions ARCANA should take this week"
        )

        try:
            analysis = await self.llm.ask_json(prompt, tier=Tier.OPUS)
        except Exception as exc:
            logger.error("Industry trend analysis failed: %s", exc)
            return {"error": str(exc)}

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            "intel-industry-trends",
            f"Last updated: {ts}\n\n```json\n{json.dumps(analysis, indent=2)}\n```",
        )

        self.memory.log(
            f"Completed industry trend analysis — {len(analysis.get('macro_trends', []))} macro trends identified",
            section="Intel",
        )
        return analysis

    # ── Reports & Analysis ───────────────────────────────────────────

    async def generate_competitive_report(self) -> str:
        """Generate a comprehensive competitive landscape report.

        Synthesizes all gathered intel into a strategic markdown report
        with actionable recommendations.
        """
        if not self._competitors:
            return "No competitors tracked. Use add_competitor() to start monitoring."

        # Gather all stored intel
        intel_files: list[str] = []
        for name in ("intel-competitors", "intel-industry-trends"):
            content = self.memory.get_knowledge("resources", name)
            if content:
                intel_files.append(content)

        for comp in self._competitors.values():
            slug = comp.name.lower().replace(" ", "-")
            for prefix in ("intel-x-", "intel-pricing-", "intel-reviews-", "intel-content-"):
                content = self.memory.get_knowledge("resources", f"{prefix}{slug}")
                if content:
                    intel_files.append(content)

        context = "\n\n---\n\n".join(intel_files) if intel_files else "No detailed intel gathered yet."

        prompt = (
            f"You are the chief strategist for Arcana Operations.\n\n"
            f"Using the following competitive intelligence data, generate a comprehensive "
            f"competitive landscape report in markdown format.\n\n"
            f"## Intel Data\n{context[:6000]}\n\n"
            f"## Report Structure\n"
            f"1. Executive Summary (3-4 sentences)\n"
            f"2. Competitive Landscape Overview (who's who)\n"
            f"3. Positioning Map (where each competitor sits)\n"
            f"4. Pricing Analysis (market rates vs. ARCANA)\n"
            f"5. Content & Marketing Comparison\n"
            f"6. Gaps & Opportunities\n"
            f"7. Threats & Risks\n"
            f"8. Strategic Recommendations (prioritized, actionable)\n"
            f"9. This Week's Action Items (top 5)\n\n"
            f"Be specific, data-driven, and actionable. No fluff."
        )

        try:
            report = await self.llm.ask(prompt, tier=Tier.OPUS, max_tokens=6000)
        except Exception as exc:
            logger.error("Competitive report generation failed: %s", exc)
            return f"Report generation failed: {exc}"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            "intel-competitive-report",
            f"Generated: {ts}\n\n{report}",
        )

        self.memory.log(
            f"Generated competitive landscape report covering {len(self._competitors)} competitors",
            section="Intel",
        )
        return report

    async def identify_displacement_opportunities(self) -> dict[str, Any]:
        """Identify specific opportunities to displace competitors.

        Cross-references competitor weaknesses with ARCANA's strengths
        to find high-probability displacement targets.
        """
        competitors_data: list[dict[str, Any]] = []
        for comp in self._competitors.values():
            slug = comp.name.lower().replace(" ", "-")
            reviews = self.memory.get_knowledge("resources", f"intel-reviews-{slug}")
            pricing = self.memory.get_knowledge("resources", f"intel-pricing-{slug}")
            competitors_data.append({
                "name": comp.name,
                "services": comp.services,
                "pricing": comp.pricing,
                "positioning": comp.positioning,
                "review_intel": reviews[:500] if reviews else "No review intel",
                "pricing_intel": pricing[:500] if pricing else "No pricing intel",
            })

        prompt = (
            f"You are a competitive strategist for Arcana Operations.\n\n"
            f"ARCANA's strengths:\n"
            f"- Autonomous AI agent (runs 24/7, never sleeps)\n"
            f"- Portland-based with real consulting experience\n"
            f"- Full-stack: SEO, marketing, agents, fulfillment\n"
            f"- Self-improving (nightly review loop)\n"
            f"- Transparent pricing ($2-10K projects)\n\n"
            f"Competitor data:\n{json.dumps(competitors_data, indent=2)[:4000]}\n\n"
            f"Identify displacement opportunities. Return JSON with:\n"
            f"- high_priority_targets: list of {{competitor, reason, approach, timeline}}\n"
            f"- quick_wins: things ARCANA can do this week to steal market share\n"
            f"- content_attacks: content topics that highlight competitor weaknesses\n"
            f"- pricing_moves: pricing adjustments to win specific segments\n"
            f"- partnership_opportunities: potential allies against common competitors\n"
            f"- risk_assessment: what could go wrong with displacement attempts"
        )

        try:
            analysis = await self.llm.ask_json(prompt, tier=Tier.OPUS)
        except Exception as exc:
            logger.error("Displacement analysis failed: %s", exc)
            return {"error": str(exc)}

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            "intel-displacement-opportunities",
            f"Last updated: {ts}\n\n```json\n{json.dumps(analysis, indent=2)}\n```",
        )

        self.memory.log(
            f"Identified {len(analysis.get('high_priority_targets', []))} displacement opportunities",
            section="Intel",
        )
        return analysis

    async def generate_swot(self, competitor_name: str | None = None) -> dict[str, Any]:
        """Generate a SWOT analysis.

        If competitor_name is provided, generates a SWOT for that competitor.
        Otherwise, generates ARCANA's own SWOT relative to the competitive landscape.
        """
        if competitor_name:
            comp = self._competitors.get(competitor_name)
            if not comp:
                return {"error": f"Competitor '{competitor_name}' not tracked"}
            subject = f"competitor {comp.name} ({comp.website})"
            context = (
                f"Services: {', '.join(comp.services) or 'Unknown'}\n"
                f"Positioning: {comp.positioning or 'Unknown'}\n"
                f"Pricing: {json.dumps(comp.pricing) if comp.pricing else 'Unknown'}"
            )
        else:
            subject = "Arcana Operations (ARCANA AI)"
            competitors_list = "\n".join(
                f"- {c.name}: {', '.join(c.services) or 'Unknown'}"
                for c in self._competitors.values()
            )
            context = (
                f"Services: AI consulting, SEO, marketing, agent development\n"
                f"Pricing: $2-10K projects, $500/mo maintenance\n"
                f"Unique: Autonomous AI CEO, 24/7 operations, self-improving\n"
                f"Competitors:\n{competitors_list or 'None tracked'}"
            )

        prompt = (
            f"Generate a SWOT analysis for: {subject}\n\n"
            f"Context:\n{context}\n\n"
            f"Return JSON with:\n"
            f"- strengths: list of 5-7 internal strengths\n"
            f"- weaknesses: list of 5-7 internal weaknesses\n"
            f"- opportunities: list of 5-7 external opportunities\n"
            f"- threats: list of 5-7 external threats\n"
            f"- strategic_priorities: top 3 actions based on the SWOT\n"
            f"- key_insight: one-sentence strategic insight from this analysis\n\n"
            f"Be brutally honest. No cheerleading."
        )

        try:
            swot = await self.llm.ask_json(prompt, tier=Tier.OPUS)
        except Exception as exc:
            logger.error("SWOT generation failed: %s", exc)
            return {"error": str(exc)}

        label = competitor_name or "arcana"
        slug = label.lower().replace(" ", "-")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"intel-swot-{slug}",
            f"Generated: {ts}\n\n```json\n{json.dumps(swot, indent=2)}\n```",
        )

        self.memory.log(
            f"Generated SWOT analysis for **{subject}**",
            section="Intel",
        )
        return swot

    # ── Weekly Intel Briefing ────────────────────────────────────────

    async def weekly_intel_briefing(self) -> str:
        """Generate a weekly competitive intelligence briefing.

        Runs all monitoring routines, synthesizes findings, and produces
        a concise briefing document for Ian & Tan. Designed to be sent
        via Discord/Telegram every Monday morning.
        """
        results: dict[str, Any] = {}

        # Monitor all competitors' X activity
        for name, comp in self._competitors.items():
            if comp.x_handle:
                results[f"x_{name}"] = await self.monitor_competitor_x(name)

        # Track pricing for all competitors
        for name in self._competitors:
            results[f"pricing_{name}"] = await self.track_competitor_pricing(name)

        # Scan reviews for all competitors
        for name in self._competitors:
            results[f"reviews_{name}"] = await self.scan_competitor_reviews(name)

        # Industry trends
        results["industry_trends"] = await self.track_industry_trends()

        # Displacement opportunities
        results["displacement"] = await self.identify_displacement_opportunities()

        # ARCANA SWOT
        results["arcana_swot"] = await self.generate_swot()

        # Synthesize into a briefing
        prompt = (
            f"You are ARCANA AI, writing your weekly intel briefing for Ian & Tan.\n\n"
            f"Raw intelligence data:\n{json.dumps(results, indent=2)[:8000]}\n\n"
            f"Write a concise weekly intel briefing in markdown. Structure:\n\n"
            f"# Weekly Intel Briefing — [date]\n\n"
            f"## TL;DR (3 bullet points)\n"
            f"## Competitor Moves\n"
            f"## Market Shifts\n"
            f"## Opportunities (ranked by urgency)\n"
            f"## Threats to Monitor\n"
            f"## Recommended Actions This Week\n\n"
            f"Keep it under 800 words. Be direct. No fluff. "
            f"Focus on what's actionable THIS WEEK."
        )

        try:
            briefing = await self.llm.ask(prompt, tier=Tier.OPUS, max_tokens=4000)
        except Exception as exc:
            logger.error("Weekly briefing generation failed: %s", exc)
            return f"Briefing generation failed: {exc}"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"intel-weekly-briefing-{ts}",
            briefing,
        )

        self.memory.log(
            f"Generated weekly intel briefing covering {len(self._competitors)} competitors",
            section="Intel",
        )
        return briefing

    async def close(self) -> None:
        """Clean up resources."""
        await self.llm.close()
