"""ARCANA AI — Orchestrator.

The autonomous brain. Runs the Felix-style daily cycle with ALL revenue channels:
1. Morning Report (7 AM PT) — Full revenue dashboard, priorities, Ian/Tan review
2. Daily Ops (every 15 min) — X posting, mentions, leads, affiliates, trade receipts
3. Weekly Ops — Newsletter issue, SEO batch, service delivery
4. Nightly Self-Improvement (11 PM PT) — Review, consolidate, learn, propose automations

Revenue target: $100K+/month across 10 channels.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
from datetime import datetime, timezone
from typing import Any

from src.affiliates import AffiliateManager
from src.agents.iris import Iris
from src.agents.remy import Remy
from src.config import STOP_FILE, Config, get_config
from src.content_engine import ContentEngine
from src.heartbeat import Heartbeat
from src.leads import LeadPipeline
from src.llm import LLM, Tier
from src.memory import Memory
from src.newsletter import Newsletter
from src.notify import Notifier
from src.products import ProductManager
from src.revenue_engine import RevenueEngine
from src.self_improve import SelfImprover
from src.seo_engine import SEOEngine
from src.services import ServiceEngine
from src.trader_bridge import TraderBridge
from src.ugc_engine import UGCEngine
from src.x_client import XClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/arcana.log", mode="a"),
    ],
)
logger = logging.getLogger("arcana")


class Orchestrator:
    """The autonomous brain of ARCANA AI — every revenue channel, one loop."""

    def __init__(self) -> None:
        self.config: Config | None = None
        self.llm: LLM | None = None
        self.memory: Memory | None = None
        self.notifier: Notifier | None = None
        self.heartbeat: Heartbeat | None = None
        self.x: XClient | None = None
        self.content: ContentEngine | None = None
        self.products: ProductManager | None = None
        self.leads: LeadPipeline | None = None
        self.iris: Iris | None = None
        self.remy: Remy | None = None
        self.improver: SelfImprover | None = None
        # Revenue expansion
        self.trader: TraderBridge | None = None
        self.revenue: RevenueEngine | None = None
        self.affiliates: AffiliateManager | None = None
        self.newsletter: Newsletter | None = None
        self.services: ServiceEngine | None = None
        self.seo: SEOEngine | None = None
        self.ugc: UGCEngine | None = None
        # State
        self._running = True
        self._last_mention_id: str | None = None
        self._completed_today: list[str] = []
        self._priorities: list[str] = []

    async def initialize(self) -> None:
        """Boot up all components — every revenue channel online."""
        self.config = get_config()
        self.llm = LLM(self.config)
        self.memory = Memory()
        self.notifier = Notifier(self.config)
        self.heartbeat = Heartbeat()

        # Core ops
        self.x = XClient(self.config, self.memory)
        self.content = ContentEngine(self.llm, self.memory)
        self.products = ProductManager(self.config, self.memory)
        self.leads = LeadPipeline(self.llm, self.memory, self.notifier)
        self.iris = Iris(self.llm, self.memory)
        self.remy = Remy(self.llm, self.memory)
        self.improver = SelfImprover(self.llm, self.memory)

        # Revenue channels
        self.trader = TraderBridge(self.llm, self.memory)
        self.affiliates = AffiliateManager(self.llm, self.memory)
        self.newsletter = Newsletter(
            self.llm, self.memory,
            self.config.beehiiv_api_key,
            self.config.beehiiv_publication_id,
        )
        self.services = ServiceEngine(self.llm, self.memory)
        self.seo = SEOEngine(self.llm, self.memory)
        self.ugc = UGCEngine(
            self.llm, self.memory,
            self.config.heygen_api_key,
            self.config.makeugc_api_key,
        )
        self.revenue = RevenueEngine(self.memory, self.products, self.trader)

        self.memory.log("ARCANA AI initialized. All revenue channels online.", "System")
        await self.notifier.send("ARCANA AI online. All revenue channels active. Let's get paid.")
        logger.info("Orchestrator initialized — all channels online")

    def _kill_switch_active(self) -> bool:
        if STOP_FILE.exists():
            logger.warning("KILL SWITCH ACTIVE — STOP file detected")
            return True
        return False

    # ── Morning Report ──────────────────────────────────────────────

    async def morning_report(self) -> str:
        """Full revenue dashboard + priorities. Ian reviews in 5 minutes."""
        logger.info("=== MORNING REPORT ===")
        self.heartbeat.update("Running morning report", "Compiling revenue dashboard")

        # Full revenue snapshot across ALL channels
        rev_snapshot = await self.revenue.get_full_revenue_snapshot()
        rev_report = self.revenue.format_revenue_report(rev_snapshot)

        # Trading bot status
        trading_summary = self.trader.get_trading_summary_for_report()

        # Get yesterday's notes
        recent = self.memory.get_recent_days(2)
        yesterday = recent[1][1] if len(recent) > 1 else "No data from yesterday."

        # Sub-agent reports
        iris_report = await self.iris.nightly_report()
        remy_report = await self.remy.nightly_report()

        # Open leads
        open_leads = [n for n in self.memory.list_knowledge("projects") if n.startswith("lead-")]

        # Service clients
        active_clients = self.services.get_active_clients()
        services_mrr = self.services.get_services_mrr()

        # Newsletter stats
        nl_stats = await self.newsletter.get_stats()

        # UGC stats
        ugc_clients = self.ugc.get_ugc_clients()
        ugc_mrr = self.ugc.get_ugc_mrr()

        # Generate priorities with full context
        result = await self.llm.ask_json(
            f"Generate ARCANA AI's morning report. Target: $100K/month.\n\n"
            f"REVENUE DASHBOARD:\n{rev_report}\n\n"
            f"TRADING BOT:\n{trading_summary}\n\n"
            f"SERVICES: {len(active_clients)} clients, ${services_mrr:,.2f} MRR\n"
            f"UGC: {len(ugc_clients)} clients, ${ugc_mrr:,.2f} MRR\n"
            f"NEWSLETTER: {nl_stats.get('subscribers', 0)} subscribers\n"
            f"OPEN LEADS: {', '.join(open_leads) or 'None'}\n\n"
            f"Yesterday:\n{yesterday[:800]}\n\n"
            f"Support (Iris): {iris_report}\n"
            f"Sales (Remy): {remy_report}\n\n"
            f"Revenue channels to activate or grow:\n"
            f"- Consulting, digital products, trading, affiliates, newsletter, services, SEO, micro-SaaS\n\n"
            f"Return JSON: {{"
            f'"report_summary": str (3-4 sentences, revenue-focused), '
            f'"revenue_actions": [str] (specific actions to grow revenue TODAY), '
            f'"open_items_for_ian": [str] (things needing human input), '
            f'"priorities": [str, str, str, str, str] (today\'s top 5, revenue-first)}}',
            tier=Tier.SONNET,
        )

        self._priorities = result.get("priorities", [])

        # Format full report
        report = (
            f"**ARCANA AI — Morning Report — {datetime.now(timezone.utc).strftime('%B %d, %Y')}**\n\n"
            f"{result.get('report_summary', 'Report generation failed.')}\n\n"
            f"{rev_report}\n\n"
            f"{trading_summary}\n\n"
            f"**Services MRR:** ${services_mrr:,.2f} ({len(active_clients)} clients)\n"
            f"**UGC MRR:** ${ugc_mrr:,.2f} ({len(ugc_clients)} clients)\n"
            f"**Newsletter:** {nl_stats.get('subscribers', 0)} subscribers\n"
            f"**Open Leads:** {len(open_leads)}\n\n"
            f"**Revenue Actions:**\n"
            + "\n".join(f"- {a}" for a in result.get("revenue_actions", []))
            + "\n\n**Waiting on Ian/Tan:**\n"
            + "\n".join(f"- {item}" for item in result.get("open_items_for_ian", ["Nothing"]))
            + "\n\n**Today's Priorities:**\n"
            + "\n".join(f"{i+1}. {p}" for i, p in enumerate(self._priorities))
        )

        await self.notifier.morning_report(report)
        self.memory.log(report, "Morning Report")
        self.heartbeat.update(
            "Active",
            self._priorities[0] if self._priorities else "Awaiting tasks",
            upcoming=self._priorities,
        )

        logger.info("Morning report sent with full revenue dashboard")
        return report

    # ── Daily Operations ────────────────────────────────────────────

    async def daily_ops_cycle(self) -> None:
        """One cycle of daily operations. Runs every 15 minutes."""
        if self._kill_switch_active():
            return

        logger.info("--- Daily ops cycle ---")

        # 1. HIGHEST PRIORITY: Check mentions → qualify leads
        await self._process_mentions()

        # 2. Post content (tweets, threads, trade receipts)
        await self._maybe_post_content()

        # 3. Post trade receipts from trader bot
        await self._maybe_post_trade_receipt()

        # 4. Update heartbeat
        self.heartbeat.update(
            "Active",
            "Monitoring all channels",
            completed=self._completed_today,
            upcoming=[p for p in self._priorities if p not in self._completed_today],
        )

    async def _process_mentions(self) -> None:
        """Check X mentions for leads and engagement opportunities."""
        mentions = await self.x.get_mentions(since_id=self._last_mention_id)
        if not mentions:
            return

        self._last_mention_id = mentions[0].get("id")

        # Process for leads
        lead_results = await self.leads.process_mentions(mentions)

        # Reply to qualified leads immediately
        for lead in lead_results.get("qualified", []):
            if lead.get("suggested_reply"):
                mention_id = next(
                    (m["id"] for m in mentions if m.get("author_id") == lead["handle"]),
                    None,
                )
                if mention_id:
                    await self.x.reply_to(mention_id, lead["suggested_reply"])

        # Generate replies for non-lead mentions
        for mention in mentions:
            text = mention.get("text", "")
            reply_decision = await self.content.reply_to_mention(text)

            if reply_decision.get("should_reply") and reply_decision.get("reply"):
                tweet_id = await self.x.reply_to(mention["id"], reply_decision["reply"])

                # Check for affiliate opportunity on the reply
                if tweet_id:
                    aff = await self.affiliates.find_relevant_affiliate(reply_decision["reply"])
                    if aff:
                        await self.x.reply_to(tweet_id, aff["reply_text"])

                await asyncio.sleep(random.uniform(5, 30))

        if mentions:
            self.memory.log(
                f"Processed {len(mentions)} mentions: "
                f"{lead_results.get('leads_found', 0)} leads, "
                f"{len(lead_results.get('qualified', []))} qualified",
                "X Mentions",
            )
            self._completed_today.append(f"Processed {len(mentions)} X mentions")

    async def _maybe_post_content(self) -> None:
        """Decide what content to post based on time and schedule."""
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Morning briefing (7 AM PT = 15 UTC)
        if hour == self.config.morning_report_hour:
            tweets = await self.content.morning_briefing()
            if tweets:
                await self.x.post_thread(tweets)
                self._completed_today.append("Posted Morning Briefing")

        # Analysis tweets (spread throughout the day)
        elif random.random() < 0.3:
            tweet = await self.content.analysis_tweet()
            if tweet:
                tweet_id = await self.x.post_with_self_reply(tweet)
                # Inject affiliate link in self-reply if relevant
                if tweet_id:
                    aff = await self.affiliates.find_relevant_affiliate(tweet)
                    if aff:
                        await self.x.reply_to(tweet_id, aff["reply_text"])
                self._completed_today.append("Posted analysis tweet")

        # Case File (2x per week — Mon and Thu)
        elif now.weekday() in (0, 3) and hour == 18 and random.random() < 0.5:
            tweets = await self.content.case_file()
            if tweets:
                await self.x.post_thread(tweets)
                self._completed_today.append("Posted Case File thread")

        # Behind-the-scenes (2-3x per week)
        elif now.weekday() in (1, 3, 5) and hour == 20 and random.random() < 0.4:
            tweet = await self.content.bts_tweet()
            if tweet:
                await self.x.post_with_self_reply(tweet)
                self._completed_today.append("Posted BTS tweet")

        # Newsletter CTA (1x per week — Wednesday)
        elif now.weekday() == 2 and hour == 17 and random.random() < 0.5:
            cta = await self.newsletter.generate_x_to_newsletter_cta()
            if cta:
                await self.x.post_with_self_reply(cta)
                self._completed_today.append("Posted newsletter CTA")

        # Product promotion (1x per week — Friday)
        elif now.weekday() == 4 and hour == 19 and random.random() < 0.4:
            tweets = await self.content.product_launch_thread("Arcana Playbook", "AI automation guide")
            if tweets:
                await self.x.post_thread(tweets)
                self._completed_today.append("Posted product promotion")

    async def _maybe_post_trade_receipt(self) -> None:
        """Post trade receipts from the trading bot to X."""
        winners = self.trader.get_recent_winners(3)
        if not winners:
            return

        # Only post 1 trade receipt per cycle, randomly
        if random.random() > 0.15:  # ~15% chance = 1-2 per day
            return

        trade = random.choice(winners)
        trade_key = f"receipt-{trade.get('symbol', '')}-{trade.get('timestamp', '')}"

        # Check if we already posted this one
        if self.memory.get_knowledge("resources", trade_key):
            return

        receipt = await self.trader.generate_trade_receipt(trade)
        if receipt:
            await self.x.post_with_self_reply(receipt)
            self.memory.save_knowledge("resources", trade_key, "posted")
            self._completed_today.append(f"Posted trade receipt: {trade.get('symbol', '?')}")

    # ── Weekly Operations ───────────────────────────────────────────

    async def weekly_ops(self) -> None:
        """Weekly operations: newsletter, SEO batch, lead follow-ups."""
        logger.info("=== WEEKLY OPS ===")

        # 1. Generate and schedule weekly newsletter
        try:
            issue = await self.newsletter.generate_weekly_issue()
            self.memory.log(
                f"Weekly newsletter: {issue.get('subject', 'N/A')} "
                f"({issue.get('sections', 0)} sections)",
                "Newsletter",
            )
            self._completed_today.append("Generated weekly newsletter")
        except Exception as exc:
            logger.error("Newsletter generation failed: %s", exc)

        # 2. Generate SEO articles batch
        try:
            cluster = await self.seo.generate_keyword_cluster(
                "AI business automation", "AI consulting"
            )
            # Pick top 3 keywords to write articles for
            keywords = cluster.get("keywords", [])[:3]
            for kw_data in keywords:
                kw = kw_data.get("keyword", "")
                if kw:
                    await self.seo.generate_article(kw)
            self._completed_today.append(f"Generated {len(keywords)} SEO articles")
        except Exception as exc:
            logger.error("SEO batch failed: %s", exc)

        # 3. Produce UGC videos for clients + self-promo
        try:
            ugc_clients = self.ugc.get_ugc_clients()
            if ugc_clients:
                for client_key in ugc_clients[:5]:
                    client_data = self.memory.get_knowledge("projects", client_key)
                    if client_data:
                        # Extract product info from client data and produce videos
                        logger.info("UGC batch for %s", client_key)
                self._completed_today.append(f"UGC batch for {len(ugc_clients)} clients")

            # Self-promo video for ARCANA's own products
            if random.random() < 0.3:  # ~30% chance each week
                promo = await self.ugc.produce_promo_video(
                    "The Arcana Playbook",
                    "https://arcanaoperations.gumroad.com/l/playbook",
                )
                if promo.get("video_url"):
                    self._completed_today.append("Produced self-promo UGC video")
        except Exception as exc:
            logger.error("UGC production failed: %s", exc)

        # 4. Follow up on warm leads
        try:
            open_leads = [
                n for n in self.memory.list_knowledge("projects")
                if n.startswith("lead-")
            ]
            for lead_key in open_leads[:5]:  # Max 5 follow-ups per week
                handle = lead_key.replace("lead-", "")
                context = self.memory.get_knowledge("projects", lead_key)
                if context:
                    await self.remy.follow_up(handle, context[:300])
            if open_leads:
                self._completed_today.append(f"Followed up on {min(len(open_leads), 5)} leads")
        except Exception as exc:
            logger.error("Lead follow-up failed: %s", exc)

    # ── Nightly Self-Improvement ────────────────────────────────────

    async def nightly_review(self) -> dict[str, Any]:
        """Run the nightly self-improvement cycle with revenue focus."""
        logger.info("=== NIGHTLY SELF-IMPROVEMENT ===")
        self.heartbeat.update("Running nightly review", "Self-improvement + revenue analysis")

        # Sub-agent reports
        iris_report = await self.iris.nightly_report()
        remy_report = await self.remy.nightly_report()
        self.memory.log(f"Iris report: {iris_report}", "Sub-Agent Reports")
        self.memory.log(f"Remy report: {remy_report}", "Sub-Agent Reports")

        # Full revenue snapshot
        rev_snapshot = await self.revenue.get_full_revenue_snapshot()
        rev_report = self.revenue.format_revenue_report(rev_snapshot)

        # Update services + UGC MRR in revenue tracking
        services_mrr = self.services.get_services_mrr()
        if services_mrr > 0:
            self.revenue.update_channel_revenue("services", services_mrr)
        ugc_mrr = self.ugc.get_ugc_mrr()
        if ugc_mrr > 0:
            self.revenue.update_channel_revenue("ugc", ugc_mrr)

        # Run self-improvement analysis
        analysis = await self.improver.run_nightly_review()

        # Send comprehensive summary
        summary = (
            f"**Nightly Review Complete**\n\n"
            f"{rev_report}\n\n"
            f"{analysis.get('summary', 'N/A')}\n"
            f"Wins: {len(analysis.get('wins', []))}\n"
            f"Bottlenecks: {len(analysis.get('bottlenecks', []))}\n"
            f"Lessons: {len(analysis.get('lessons_learned', []))}\n"
            f"Tomorrow: {', '.join(analysis.get('tomorrow_priorities', [])[:3])}"
        )
        await self.notifier.send(summary, "report")

        self.heartbeat.clear()
        self._completed_today = []

        logger.info("Nightly review complete")
        return analysis

    # ── Main Loop ───────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Main loop: morning → 15-min ops → weekly ops → nightly review."""
        await self.initialize()
        interval = 15 * 60  # 15 minutes

        did_morning = False
        did_nightly = False
        did_weekly = False

        while self._running:
            if self._kill_switch_active():
                logger.info("Kill switch active. Sleeping 60s...")
                await asyncio.sleep(60)
                continue

            now = datetime.now(timezone.utc)

            # Morning report (once per day)
            if now.hour == self.config.morning_report_hour and not did_morning:
                try:
                    await self.morning_report()
                    did_morning = True
                except Exception as exc:
                    logger.error("Morning report failed: %s", exc)
                    await self.notifier.error_alert("morning_report", str(exc))

            # Weekly ops (Sunday at 16 UTC / 8 AM PT)
            if now.weekday() == 6 and now.hour == 16 and not did_weekly:
                try:
                    await self.weekly_ops()
                    did_weekly = True
                except Exception as exc:
                    logger.error("Weekly ops failed: %s", exc)
                    await self.notifier.error_alert("weekly_ops", str(exc))

            # Nightly review (once per day)
            if now.hour == self.config.nightly_review_hour and not did_nightly:
                try:
                    await self.nightly_review()
                    did_nightly = True
                except Exception as exc:
                    logger.error("Nightly review failed: %s", exc)
                    await self.notifier.error_alert("nightly_review", str(exc))

            # Reset flags at midnight UTC
            if now.hour == 0:
                did_morning = False
                did_nightly = False
                if now.weekday() == 0:  # Reset weekly on Monday
                    did_weekly = False

            # Regular daily ops cycle
            try:
                await self.daily_ops_cycle()
            except Exception as exc:
                logger.error("Daily ops cycle failed: %s", exc)
                await self.notifier.error_alert("daily_ops", str(exc))

            # Sleep with jitter (anti-bot)
            jitter = random.randint(0, 60)
            await asyncio.sleep(interval + jitter)

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down ARCANA AI...")
        self._running = False
        self.memory.log("ARCANA AI shutting down.", "System")
        if self.notifier:
            await self.notifier.send("ARCANA AI shutting down.")
            await self.notifier.close()
        if self.llm:
            await self.llm.close()
        if self.products:
            await self.products.close()
        if self.newsletter:
            await self.newsletter.close()
        if self.ugc:
            await self.ugc.close()


def main() -> None:
    os.makedirs("logs", exist_ok=True)
    orchestrator = Orchestrator()

    def handle_signal(signum, frame):
        asyncio.get_event_loop().create_task(orchestrator.shutdown())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(orchestrator.run_forever())


if __name__ == "__main__":
    main()
