"""ARCANA AI — Orchestrator.

The autonomous brain. Runs the Felix-style daily cycle:
1. Morning Report (7 AM PT) — Check revenue, compile priorities, ping Ian/Tan
2. Daily Ops (all day) — X posting, mention monitoring, lead qualification, support
3. Nightly Self-Improvement (11 PM PT) — Review day, consolidate memory, build new skills

Also handles:
- Kill switch (STOP file)
- Heartbeat updates
- Error recovery
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agents.iris import Iris
from src.agents.remy import Remy
from src.config import STOP_FILE, Config, get_config
from src.content_engine import ContentEngine
from src.heartbeat import Heartbeat
from src.leads import LeadPipeline
from src.llm import LLM, Tier
from src.memory import Memory
from src.notify import Notifier
from src.products import ProductManager
from src.self_improve import SelfImprover
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
    """The autonomous brain of ARCANA AI."""

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
        self._running = True
        self._last_mention_id: str | None = None
        self._completed_today: list[str] = []
        self._priorities: list[str] = []

    async def initialize(self) -> None:
        """Boot up all components."""
        self.config = get_config()
        self.llm = LLM(self.config)
        self.memory = Memory()
        self.notifier = Notifier(self.config)
        self.heartbeat = Heartbeat()
        self.x = XClient(self.config, self.memory)
        self.content = ContentEngine(self.llm, self.memory)
        self.products = ProductManager(self.config, self.memory)
        self.leads = LeadPipeline(self.llm, self.memory, self.notifier)
        self.iris = Iris(self.llm, self.memory)
        self.remy = Remy(self.llm, self.memory)
        self.improver = SelfImprover(self.llm, self.memory)

        self.memory.log("ARCANA AI initialized. Beginning operations.", "System")
        await self.notifier.send("ARCANA AI online. Beginning daily operations.")
        logger.info("Orchestrator initialized")

    def _kill_switch_active(self) -> bool:
        if STOP_FILE.exists():
            logger.warning("KILL SWITCH ACTIVE — STOP file detected")
            return True
        return False

    # ── Morning Report ──────────────────────────────────────────────

    async def morning_report(self) -> str:
        """Generate and send morning report. Ian reviews in 5 minutes."""
        logger.info("=== MORNING REPORT ===")
        self.heartbeat.update("Running morning report", "Compiling stats and priorities")

        # Check revenue
        revenue = await self.products.get_revenue_summary()

        # Get yesterday's notes
        recent = self.memory.get_recent_days(2)
        yesterday = recent[1][1] if len(recent) > 1 else "No data from yesterday."

        # Get sub-agent reports
        iris_report = await self.iris.nightly_report()
        remy_report = await self.remy.nightly_report()

        # Open leads
        open_leads = [n for n in self.memory.list_knowledge("projects") if n.startswith("lead-")]

        # Generate priorities with LLM
        result = await self.llm.ask_json(
            f"Generate ARCANA AI's morning report.\n\n"
            f"Revenue (recent): ${revenue.get('total_revenue', 0):.2f}\n"
            f"  Stripe: ${revenue.get('stripe', {}).get('revenue', 0):.2f}\n"
            f"  Gumroad: ${revenue.get('gumroad', {}).get('revenue', 0):.2f}\n\n"
            f"Yesterday's notes:\n{yesterday[:1000]}\n\n"
            f"Support (Iris): {iris_report}\n"
            f"Sales (Remy): {remy_report}\n"
            f"Open leads: {', '.join(open_leads) or 'None'}\n\n"
            f"Return JSON: {{"
            f'"report_summary": str (3-4 sentences), '
            f'"open_items_for_ian": [str] (things needing human input), '
            f'"priorities": [str, str, str, str, str] (today\'s top 5 tasks)}}',
            tier=Tier.SONNET,
        )

        self._priorities = result.get("priorities", [])

        # Format report
        report = (
            f"**Morning Report — {datetime.now(timezone.utc).strftime('%B %d, %Y')}**\n\n"
            f"{result.get('report_summary', 'Report generation failed.')}\n\n"
            f"**Revenue:** ${revenue.get('total_revenue', 0):.2f}\n"
            f"**Open Leads:** {len(open_leads)}\n\n"
            f"**Waiting on Ian/Tan:**\n"
            + "\n".join(f"- {item}" for item in result.get("open_items_for_ian", ["Nothing"]))
            + "\n\n**Today's Priorities:**\n"
            + "\n".join(f"{i+1}. {p}" for i, p in enumerate(self._priorities))
        )

        # Send to Discord/Telegram
        await self.notifier.morning_report(report)

        # Log to memory
        self.memory.log(report, "Morning Report")

        # Update heartbeat
        self.heartbeat.update("Active", self._priorities[0] if self._priorities else "Awaiting tasks", upcoming=self._priorities)

        logger.info("Morning report sent")
        return report

    # ── Daily Operations ────────────────────────────────────────────

    async def daily_ops_cycle(self) -> None:
        """One cycle of daily operations. Runs every 15 minutes."""
        if self._kill_switch_active():
            return

        logger.info("--- Daily ops cycle ---")

        # 1. Check mentions and qualify leads (HIGHEST PRIORITY)
        await self._process_mentions()

        # 2. Post content (if it's time)
        await self._maybe_post_content()

        # 3. Check revenue
        await self.products.get_revenue_summary()

        # 4. Update heartbeat
        self.heartbeat.update(
            "Active",
            "Monitoring",
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
                await self.x.reply_to(mention["id"], reply_decision["reply"])
                await asyncio.sleep(random.uniform(5, 30))

        if mentions:
            self.memory.log(
                f"Processed {len(mentions)} mentions: "
                f"{lead_results.get('leads_found', 0)} leads found, "
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

        # Analysis tweets (spread throughout the day with jitter)
        elif random.random() < 0.3:  # ~30% chance each cycle = 3-5 tweets/day
            tweet = await self.content.analysis_tweet()
            if tweet:
                await self.x.post_with_self_reply(tweet)
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

    # ── Nightly Self-Improvement ────────────────────────────────────

    async def nightly_review(self) -> dict[str, Any]:
        """Run the nightly self-improvement cycle."""
        logger.info("=== NIGHTLY SELF-IMPROVEMENT ===")
        self.heartbeat.update("Running nightly review", "Self-improvement cycle")

        # Get sub-agent reports
        iris_report = await self.iris.nightly_report()
        remy_report = await self.remy.nightly_report()
        self.memory.log(f"Iris report: {iris_report}", "Sub-Agent Reports")
        self.memory.log(f"Remy report: {remy_report}", "Sub-Agent Reports")

        # Run the self-improvement analysis
        analysis = await self.improver.run_nightly_review()

        # Send summary to Ian/Tan
        summary = (
            f"**Nightly Review Complete**\n"
            f"{analysis.get('summary', 'N/A')}\n"
            f"Wins: {len(analysis.get('wins', []))}\n"
            f"Bottlenecks: {len(analysis.get('bottlenecks', []))}\n"
            f"New lessons: {len(analysis.get('lessons_learned', []))}\n"
            f"Tomorrow: {', '.join(analysis.get('tomorrow_priorities', [])[:3])}"
        )
        await self.notifier.send(summary, "report")

        # Clear heartbeat for the day
        self.heartbeat.clear()
        self._completed_today = []

        logger.info("Nightly review complete")
        return analysis

    # ── Main Loop ───────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Main loop: morning report → daily ops every 15 min → nightly review."""
        await self.initialize()
        interval = 15 * 60  # 15 minutes

        did_morning = False
        did_nightly = False

        while self._running:
            if self._kill_switch_active():
                logger.info("Kill switch active. Sleeping 60s...")
                await asyncio.sleep(60)
                continue

            now = datetime.now(timezone.utc)

            # Morning report (once per day, at morning_report_hour)
            if now.hour == self.config.morning_report_hour and not did_morning:
                try:
                    await self.morning_report()
                    did_morning = True
                except Exception as exc:
                    logger.error("Morning report failed: %s", exc)
                    await self.notifier.error_alert("morning_report", str(exc))

            # Nightly review (once per day, at nightly_review_hour)
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
