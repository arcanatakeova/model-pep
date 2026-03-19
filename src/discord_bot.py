"""ARCANA AI — Discord Bot: The Office.

ARCANA's primary interface with its human operators (Ian & Tan).
Runs alongside the orchestrator as a background task.

Channels:
    #general    — Day-to-day conversation, instructions, status updates
    #support    — Inbound customer inquiries routed from Iris
    #sales      — Lead alerts, deal updates, pipeline changes
    #dev        — Logs, errors, deployments, self-improvement results
    #alerts     — High-priority: hot leads (80+), payment failures, kill switch
    #revenue    — Sales, subscriptions, refunds, revenue dashboards

Slash commands give Ian/Tan full control without touching code.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from src.config import STOP_FILE, Config, get_config
from src.heartbeat import Heartbeat
from src.memory import Memory

if TYPE_CHECKING:
    from src.content_engine import ContentEngine
    from src.crm import CRM
    from src.leads import LeadPipeline
    from src.notify import Notifier
    from src.orchestrator import Orchestrator
    from src.payments import PaymentsEngine
    from src.revenue_engine import RevenueEngine

logger = logging.getLogger("arcana.discord")

# ── Channel name constants ───────────────────────────────────────────────────
CH_GENERAL = "general"
CH_SUPPORT = "support"
CH_SALES = "sales"
CH_DEV = "dev"
CH_ALERTS = "alerts"
CH_REVENUE = "revenue"

# Ian & Tan's Discord user IDs — set via env or hard-code after first run.
# Messages from these users are routed as operator instructions.
OPERATOR_IDS: set[int] = set()


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _truncate(text: str, limit: int = 4000) -> str:
    """Discord embed descriptions max out at 4096 chars."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n\n... (truncated)"


# ═══════════════════════════════════════════════════════════════════════════════
#  ARCANA Discord Bot
# ═══════════════════════════════════════════════════════════════════════════════


class ArcanaBot(discord.Client):
    """Full-featured Discord bot for ARCANA's office."""

    def __init__(
        self,
        config: Config,
        memory: Memory,
        heartbeat: Heartbeat,
        orchestrator: Orchestrator | None = None,
        *,
        intents: discord.Intents | None = None,
    ) -> None:
        _intents = intents or discord.Intents.default()
        _intents.message_content = True
        _intents.members = True
        super().__init__(intents=_intents)

        self.config = config
        self.memory = memory
        self.heartbeat = heartbeat
        self.orchestrator = orchestrator

        # Resolved lazily from the orchestrator after it initialises
        self._crm: CRM | None = None
        self._leads: LeadPipeline | None = None
        self._content: ContentEngine | None = None
        self._payments: PaymentsEngine | None = None
        self._revenue: RevenueEngine | None = None
        self._notifier: Notifier | None = None

        # Channel cache  {name: discord.TextChannel}
        self._channels: dict[str, discord.TextChannel] = {}

        # Command tree for slash commands
        self.tree = app_commands.CommandTree(self)

        # Load operator IDs from env
        import os
        raw = os.getenv("DISCORD_OPERATOR_IDS", "")
        for uid in raw.split(","):
            uid = uid.strip()
            if uid.isdigit():
                OPERATOR_IDS.add(int(uid))

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called after login, before the bot starts processing events."""
        self._register_commands()
        await self.tree.sync()
        logger.info("Slash commands synced")

    async def on_ready(self) -> None:
        if not self.user:
            logger.error("Bot user not set in on_ready")
            return
        logger.info("ARCANA Discord bot online as %s (ID: %s)", self.user, self.user.id)
        await self._resolve_channels()
        await self._resolve_components()
        await self._post(CH_DEV, embed=_embed(
            "ARCANA AI Online",
            f"Bot connected at {_ts()}.\nOperator IDs: {OPERATOR_IDS or 'none configured'}",
            colour=discord.Colour.green(),
        ))
        # Set presence
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the markets and the mentions",
            ),
        )

    async def on_message(self, message: discord.Message) -> None:
        """Listen for operator messages and route as instructions."""
        if message.author.bot:
            return

        # Only process messages from known operators
        # When OPERATOR_IDS is empty, reject all — never treat everyone as operator
        if not OPERATOR_IDS or message.author.id not in OPERATOR_IDS:
            return

        # Skip if the message is a slash command invocation (starts with /)
        if message.content.startswith("/"):
            return

        # Route operator instructions to memory and acknowledge
        content = message.content.strip()
        if not content:
            return

        operator_name = message.author.display_name
        channel_name = getattr(message.channel, "name", "dm")

        # Log instruction to memory
        self.memory.log(
            f"[Operator Instruction] {operator_name} in #{channel_name}:\n{content}",
            "Operator",
        )

        logger.info(
            "Operator instruction from %s in #%s: %s",
            operator_name, channel_name, content[:120],
        )

        # Acknowledge
        await message.add_reaction("\u2705")  # checkmark

        # If it looks like a direct task, post confirmation to #general
        if any(kw in content.lower() for kw in (
            "do", "run", "post", "send", "create", "update", "change",
            "approve", "stop", "start", "check", "fix", "add", "remove",
        )):
            await self._post(CH_GENERAL, embed=_embed(
                "Instruction Received",
                f"**From:** {operator_name}\n"
                f"**Channel:** #{channel_name}\n"
                f"**Instruction:** {content[:500]}\n\n"
                f"Logged to memory. Will action on next ops cycle.",
                colour=discord.Colour.blue(),
            ))

    # ── Channel resolution ───────────────────────────────────────────────────

    async def _resolve_channels(self) -> None:
        """Populate the channel cache from all guilds the bot is in."""
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name in (CH_GENERAL, CH_SUPPORT, CH_SALES, CH_DEV, CH_ALERTS, CH_REVENUE):
                    self._channels[channel.name] = channel
                    logger.info("Resolved #%s in %s", channel.name, guild.name)

        missing = {CH_GENERAL, CH_SUPPORT, CH_SALES, CH_DEV, CH_ALERTS, CH_REVENUE} - set(self._channels)
        if missing:
            logger.warning("Missing channels: %s — auto-posting to these will be skipped", missing)

    async def _resolve_components(self) -> None:
        """Pull component references from the orchestrator once it is ready."""
        if not self.orchestrator:
            return
        self._crm = self.orchestrator.crm
        self._leads = self.orchestrator.leads
        self._content = self.orchestrator.content
        self._payments = self.orchestrator.payments_engine
        self._revenue = self.orchestrator.revenue
        self._notifier = self.orchestrator.notifier

    # ── Posting helpers ──────────────────────────────────────────────────────

    async def _post(
        self,
        channel_name: str,
        content: str | None = None,
        embed: discord.Embed | None = None,
    ) -> discord.Message | None:
        """Post a message to a named channel. Silently skips if channel is missing."""
        ch = self._channels.get(channel_name)
        if ch is None:
            logger.debug("Channel #%s not found — skipping post", channel_name)
            return None
        try:
            return await ch.send(content=content, embed=embed)
        except Exception as exc:
            logger.error("Failed to post to #%s: %s", channel_name, exc)
            return None

    # ── Slash Command Registration ───────────────────────────────────────────

    def _register_commands(self) -> None:
        """Register all slash commands on the command tree."""

        # ── /status ──────────────────────────────────────────────────────────

        @self.tree.command(name="status", description="Show ARCANA's current status and today's metrics")
        async def cmd_status(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                hb = self.heartbeat.get()
                today_notes = self.memory.get_today()

                # Count logged events today
                log_lines = [l for l in today_notes.splitlines() if l.startswith("##")]
                kill_active = STOP_FILE.exists()

                status_text = (
                    f"```\n{hb}\n```\n"
                    f"**Kill switch:** {'ACTIVE' if kill_active else 'off'}\n"
                    f"**Log entries today:** {len(log_lines)}\n"
                    f"**Timestamp:** {_ts()}"
                )

                embed = _embed(
                    "ARCANA AI Status",
                    _truncate(status_text),
                    colour=discord.Colour.red() if kill_active else discord.Colour.green(),
                )
                await interaction.followup.send(embed=embed)
            except Exception as exc:
                await interaction.followup.send(f"Error: {exc}")

        # ── /pipeline ────────────────────────────────────────────────────────

        @self.tree.command(name="pipeline", description="Show CRM pipeline summary")
        async def cmd_pipeline(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                if not self._crm:
                    await interaction.followup.send("CRM not initialized yet.")
                    return

                report = self._crm.format_pipeline_report()
                deals = self._crm.get_pipeline()

                overdue = await self._crm.get_overdue_actions()
                overdue_text = ""
                if overdue:
                    overdue_text = "\n\n**Overdue Actions:**\n" + "\n".join(
                        f"- `{d['key']}` ({d['stage']}) — {d['days_stale']}d stale — {d['next_action']}"
                        for d in overdue[:10]
                    )

                embed = _embed(
                    "CRM Pipeline",
                    f"```\n{report}\n```{overdue_text}",
                    colour=discord.Colour.purple(),
                )
                embed.set_footer(text=f"Total deals: {len(deals)} | {_ts()}")
                await interaction.followup.send(embed=embed)
            except Exception as exc:
                await interaction.followup.send(f"Error: {exc}")

        # ── /revenue ─────────────────────────────────────────────────────────

        @self.tree.command(name="revenue", description="Show revenue dashboard (7d and 30d)")
        async def cmd_revenue(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                lines: list[str] = []

                if self._payments:
                    rev_7 = self._payments.get_revenue_summary(days=7)
                    rev_30 = self._payments.get_revenue_summary(days=30)
                    mrr = self._payments.get_mrr()
                    subs = self._payments.get_active_subscriptions()

                    lines.append("**Stripe Revenue**")
                    lines.append(f"  Last 7 days:  ${rev_7.get('revenue', 0):,.2f} ({rev_7.get('charges', 0)} charges)")
                    lines.append(f"  Last 30 days: ${rev_30.get('revenue', 0):,.2f} ({rev_30.get('charges', 0)} charges)")
                    lines.append(f"  MRR:          ${mrr:,.2f}")
                    lines.append(f"  Active subs:  {len(subs)}")

                    # Recent charges
                    recent = rev_7.get("recent", [])
                    if recent:
                        lines.append("\n**Recent Charges (7d):**")
                        for ch in recent[:5]:
                            lines.append(
                                f"  {ch['created']} — ${ch['amount']:.2f} — {ch.get('description', '')[:40]}"
                            )

                if self._revenue:
                    snapshot = await self._revenue.get_full_revenue_snapshot()
                    full_report = self._revenue.format_revenue_report(snapshot)
                    lines.append(f"\n**Full Revenue Report:**\n```\n{full_report[:1500]}\n```")

                if not lines:
                    lines.append("Revenue engines not initialized yet.")

                embed = _embed(
                    "Revenue Dashboard",
                    _truncate("\n".join(lines)),
                    colour=discord.Colour.gold(),
                )
                embed.set_footer(text=_ts())
                await interaction.followup.send(embed=embed)
            except Exception as exc:
                await interaction.followup.send(f"Error: {exc}")

        # ── /scan ────────────────────────────────────────────────────────────

        @self.tree.command(name="scan", description="Trigger a manual opportunity scan cycle")
        async def cmd_scan(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                if not self.orchestrator or not self.orchestrator.scanner:
                    await interaction.followup.send("Scanner not initialized.")
                    return

                await interaction.followup.send(
                    embed=_embed("Scan Started", "Running opportunity scan cycle...", colour=discord.Colour.blue())
                )

                results = await self.orchestrator.scanner.scan_cycle()

                total = results.get("total_found", 0)
                responded = results.get("auto_responded", 0)
                report = self.orchestrator.scanner.format_scanner_report()

                embed = _embed(
                    "Scan Results",
                    f"**Opportunities found:** {total}\n"
                    f"**Auto-responded:** {responded}\n\n"
                    f"```\n{report[:2000]}\n```",
                    colour=discord.Colour.teal(),
                )
                embed.set_footer(text=_ts())
                await interaction.followup.send(embed=embed)
            except Exception as exc:
                await interaction.followup.send(f"Scan failed: {exc}")

        # ── /leads ───────────────────────────────────────────────────────────

        @self.tree.command(name="leads", description="Show top 10 leads with scores")
        async def cmd_leads(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                lead_keys = [
                    k for k in self.memory.list_knowledge("projects")
                    if k.startswith("lead-")
                ]

                if not lead_keys:
                    await interaction.followup.send(
                        embed=_embed("Leads", "No leads in pipeline.", colour=discord.Colour.light_grey())
                    )
                    return

                entries: list[tuple[int, str, str]] = []
                for key in lead_keys:
                    data = self.memory.get_knowledge("projects", key)
                    if not data:
                        continue
                    score = 0
                    summary_parts: list[str] = []
                    for line in data.splitlines():
                        if "Score:" in line:
                            try:
                                score = int("".join(c for c in line.split("Score:")[1] if c.isdigit())[:3])
                            except (ValueError, IndexError):
                                pass
                        if "Service:" in line:
                            summary_parts.append(line.split("Service:")[1].strip()[:50])
                        if "Est. value:" in line:
                            summary_parts.append(line.split("Est. value:")[1].strip()[:20])
                    handle = key.replace("lead-", "@")
                    summary = " | ".join(summary_parts) if summary_parts else "—"
                    entries.append((score, handle, summary))

                # Sort by score descending, take top 10
                entries.sort(key=lambda x: x[0], reverse=True)
                lines = []
                for i, (score, handle, summary) in enumerate(entries[:10], 1):
                    icon = "\U0001f525" if score >= 80 else "\U0001f7e1" if score >= 50 else "\u26aa"
                    lines.append(f"{i}. {icon} **{handle}** — score {score} — {summary}")

                embed = _embed(
                    f"Top Leads ({len(entries)} total)",
                    "\n".join(lines),
                    colour=discord.Colour.orange(),
                )
                embed.set_footer(text=_ts())
                await interaction.followup.send(embed=embed)
            except Exception as exc:
                await interaction.followup.send(f"Error: {exc}")

        # ── /content ─────────────────────────────────────────────────────────

        @self.tree.command(name="content", description="Show today's content calendar")
        async def cmd_content(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            try:
                if not self._content:
                    await interaction.followup.send("Content engine not initialized.")
                    return

                calendar = await self._content.get_content_calendar()

                now = datetime.now(timezone.utc)
                day_name = now.strftime("%A").lower()
                today_plan = calendar.get("days", {}).get(day_name, [])

                lines: list[str] = []
                lines.append(f"**Week theme:** {calendar.get('theme', 'N/A')}")
                lines.append(f"\n**Today ({day_name.title()}):**")

                if today_plan:
                    for item in today_plan:
                        suit = item.get("suit", "?")
                        suit_icons = {"wands": "\U0001f9f9", "cups": "\U0001f3c6", "swords": "\u2694\ufe0f", "pentacles": "\U0001fa99"}
                        icon = suit_icons.get(suit.lower(), "\U0001f4dd")
                        lines.append(
                            f"  {icon} **{item.get('time', '?')}** — {item.get('type', '?')} — {item.get('topic', '?')}"
                        )
                else:
                    lines.append("  No posts planned for today.")

                if calendar.get("key_themes"):
                    lines.append(f"\n**Key themes:** {', '.join(calendar['key_themes'][:5])}")
                if calendar.get("optimization_notes"):
                    lines.append(f"\n**Notes:** {calendar['optimization_notes'][:300]}")

                embed = _embed(
                    "Content Calendar",
                    _truncate("\n".join(lines)),
                    colour=discord.Colour.dark_magenta(),
                )
                embed.set_footer(text=_ts())
                await interaction.followup.send(embed=embed)
            except Exception as exc:
                await interaction.followup.send(f"Error: {exc}")

        # ── /stop ────────────────────────────────────────────────────────────

        @self.tree.command(name="stop", description="Activate kill switch (STOP file)")
        async def cmd_stop(interaction: discord.Interaction) -> None:
            # Only operators can activate the kill switch
            if not OPERATOR_IDS or interaction.user.id not in OPERATOR_IDS:
                await interaction.response.send_message(
                    "Only operators (Ian/Tan) can activate the kill switch.", ephemeral=True,
                )
                return

            STOP_FILE.touch()
            self.memory.log(
                f"[KILL SWITCH] Activated by {interaction.user.display_name} via Discord /stop",
                "System",
            )
            logger.warning("Kill switch activated by %s", interaction.user.display_name)

            await interaction.response.send_message(
                embed=_embed(
                    "KILL SWITCH ACTIVATED",
                    f"STOP file created. ARCANA will halt all operations within 60 seconds.\n"
                    f"Activated by: **{interaction.user.display_name}**\n"
                    f"To resume: delete the STOP file or use a restart command.",
                    colour=discord.Colour.red(),
                ),
            )

            # Also alert in #alerts
            await self._post(CH_ALERTS, embed=_embed(
                "KILL SWITCH ACTIVATED",
                f"Activated by {interaction.user.display_name} at {_ts()}.\n"
                f"All autonomous operations halted.",
                colour=discord.Colour.red(),
            ))

        # ── /approve [task] ──────────────────────────────────────────────────

        @self.tree.command(name="approve", description="Approve a proposed action")
        @app_commands.describe(task="Description of the task/action to approve")
        async def cmd_approve(interaction: discord.Interaction, task: str) -> None:
            if not OPERATOR_IDS or interaction.user.id not in OPERATOR_IDS:
                await interaction.response.send_message(
                    "Only operators can approve actions.", ephemeral=True,
                )
                return

            self.memory.log(
                f"[Approved] {interaction.user.display_name} approved: {task}",
                "Approvals",
            )
            self.memory.save_knowledge(
                "resources",
                f"approval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
                f"# Approval\n\n"
                f"- Approved by: {interaction.user.display_name}\n"
                f"- Task: {task}\n"
                f"- Time: {_ts()}\n"
                f"- Status: approved\n",
            )

            await interaction.response.send_message(
                embed=_embed(
                    "Action Approved",
                    f"**Task:** {task}\n"
                    f"**Approved by:** {interaction.user.display_name}\n"
                    f"**Time:** {_ts()}\n\n"
                    f"ARCANA will action this on the next ops cycle.",
                    colour=discord.Colour.green(),
                ),
            )
            logger.info("Approved by %s: %s", interaction.user.display_name, task[:100])

        # ── /query [search] ──────────────────────────────────────────────────

        @self.tree.command(name="query", description="Search ARCANA's memory")
        @app_commands.describe(search="Keywords to search for in ARCANA's memory")
        async def cmd_query(interaction: discord.Interaction, search: str) -> None:
            await interaction.response.defer()
            try:
                results = self.memory.search(search)

                if not results:
                    await interaction.followup.send(
                        embed=_embed("Memory Search", f"No results for: **{search}**", colour=discord.Colour.light_grey())
                    )
                    return

                lines: list[str] = []
                for path, match_line in results[:15]:
                    lines.append(f"`{path}`\n> {match_line[:150]}")

                embed = _embed(
                    f"Memory Search: \"{search}\"",
                    _truncate("\n\n".join(lines)),
                    colour=discord.Colour.blurple(),
                )
                embed.set_footer(text=f"{len(results)} result(s) | {_ts()}")
                await interaction.followup.send(embed=embed)
            except Exception as exc:
                await interaction.followup.send(f"Error: {exc}")

    # ── Auto-posting: public methods for the orchestrator to call ────────────

    async def alert_high_score_lead(self, handle: str, score: int, service_fit: str, value: int) -> None:
        """Post to #alerts when a lead scores 80+."""
        embed = _embed(
            "HOT LEAD DETECTED",
            f"**Handle:** @{handle}\n"
            f"**Score:** {score}/100\n"
            f"**Service fit:** {service_fit}\n"
            f"**Estimated value:** ${value:,}/mo\n\n"
            f"Remy is following up. DM qualification is highest priority.",
            colour=discord.Colour.red(),
        )
        await self._post(CH_ALERTS, embed=embed)
        await self._post(CH_SALES, embed=embed)

    async def alert_deal_stage_change(self, deal_key: str, old_stage: str, new_stage: str, notes: str = "") -> None:
        """Post to #alerts when a deal changes pipeline stage."""
        colour = discord.Colour.green() if new_stage == "won" else discord.Colour.orange()
        embed = _embed(
            "Deal Stage Change",
            f"**Deal:** `{deal_key}`\n"
            f"**Stage:** {old_stage} \u2192 **{new_stage}**\n"
            f"**Notes:** {notes[:300] if notes else 'N/A'}",
            colour=colour,
        )
        await self._post(CH_ALERTS, embed=embed)
        await self._post(CH_SALES, embed=embed)

    async def alert_payment_event(self, event_type: str, customer: str, amount: float, details: str = "") -> None:
        """Post to #alerts and #revenue for payment events (success, failure, refund)."""
        colour_map = {
            "payment_succeeded": discord.Colour.green(),
            "payment_failed": discord.Colour.red(),
            "checkout_completed": discord.Colour.green(),
            "subscription_deleted": discord.Colour.dark_red(),
            "refund": discord.Colour.dark_orange(),
        }
        icon_map = {
            "payment_succeeded": "\U0001f4b0",
            "payment_failed": "\U0001f6a8",
            "checkout_completed": "\U0001f389",
            "subscription_deleted": "\U0001f4c9",
            "refund": "\u21a9\ufe0f",
        }

        embed = _embed(
            f"{icon_map.get(event_type, '')} {event_type.replace('_', ' ').title()}",
            f"**Customer:** {customer}\n"
            f"**Amount:** ${amount:,.2f}\n"
            f"{f'**Details:** {details[:300]}' if details else ''}",
            colour=colour_map.get(event_type, discord.Colour.greyple()),
        )

        await self._post(CH_REVENUE, embed=embed)

        # Critical events also go to #alerts
        if event_type in ("payment_failed", "subscription_deleted", "refund"):
            await self._post(CH_ALERTS, embed=embed)

    async def alert_new_sale(self, product: str, amount: float, source: str, customer: str = "") -> None:
        """Post to #revenue for new sales."""
        embed = _embed(
            "New Sale",
            f"**Product:** {product}\n"
            f"**Amount:** ${amount:,.2f}\n"
            f"**Source:** {source}\n"
            f"{f'**Customer:** {customer}' if customer else ''}",
            colour=discord.Colour.green(),
        )
        await self._post(CH_REVENUE, embed=embed)

    async def alert_subscription_event(self, event: str, customer: str, amount: float, interval: str = "month") -> None:
        """Post to #revenue for subscription events."""
        colour = discord.Colour.green() if event == "created" else discord.Colour.dark_orange()
        embed = _embed(
            f"Subscription {event.title()}",
            f"**Customer:** {customer}\n"
            f"**Amount:** ${amount:,.2f}/{interval}\n"
            f"**Event:** {event}",
            colour=colour,
        )
        await self._post(CH_REVENUE, embed=embed)

    async def alert_customer_inquiry(self, customer: str, subject: str, content: str) -> None:
        """Post to #support for incoming customer inquiries."""
        embed = _embed(
            "Customer Inquiry",
            f"**From:** {customer}\n"
            f"**Subject:** {subject}\n\n"
            f"{content[:800]}",
            colour=discord.Colour.blue(),
        )
        await self._post(CH_SUPPORT, embed=embed)

    async def alert_error(self, context: str, error: str) -> None:
        """Post to #alerts and #dev for errors."""
        embed = _embed(
            "Error",
            f"**Context:** {context}\n"
            f"**Error:** {error[:800]}",
            colour=discord.Colour.red(),
        )
        await self._post(CH_ALERTS, embed=embed)
        await self._post(CH_DEV, embed=embed)

    async def post_morning_report(self, report: str) -> None:
        """Post the full morning report to #general."""
        embed = _embed(
            f"Morning Report \u2014 {datetime.now(timezone.utc).strftime('%B %d, %Y')}",
            _truncate(report),
            colour=discord.Colour.gold(),
        )
        await self._post(CH_GENERAL, embed=embed)

    async def post_nightly_summary(self, summary: str) -> None:
        """Post the nightly review summary to #general and #dev."""
        embed = _embed(
            "Nightly Review Complete",
            _truncate(summary),
            colour=discord.Colour.dark_purple(),
        )
        await self._post(CH_GENERAL, embed=embed)
        await self._post(CH_DEV, embed=embed)

    async def post_dev_log(self, title: str, message: str) -> None:
        """Post a development/system log to #dev."""
        embed = _embed(title, _truncate(message), colour=discord.Colour.light_grey())
        await self._post(CH_DEV, embed=embed)

    async def post_scan_results(self, results: dict[str, Any]) -> None:
        """Post scan results to #sales."""
        total = results.get("total_found", 0)
        responded = results.get("auto_responded", 0)
        embed = _embed(
            "Scan Cycle Complete",
            f"**Opportunities found:** {total}\n"
            f"**Auto-responded:** {responded}",
            colour=discord.Colour.teal(),
        )
        await self._post(CH_SALES, embed=embed)


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _embed(
    title: str,
    description: str,
    colour: discord.Colour | None = None,
) -> discord.Embed:
    """Build a standard ARCANA embed."""
    return discord.Embed(
        title=title,
        description=description,
        colour=colour or discord.Colour.dark_theme(),
        timestamp=datetime.now(timezone.utc),
    ).set_author(name="ARCANA AI", icon_url=None)


# ═══════════════════════════════════════════════════════════════════════════════
#  Runner — integrates with the Orchestrator's asyncio loop
# ═══════════════════════════════════════════════════════════════════════════════


async def run_discord_bot(
    orchestrator: Orchestrator,
    token: str | None = None,
) -> None:
    """Start the Discord bot as a background task alongside the orchestrator.

    Usage from orchestrator.run_forever():
        asyncio.create_task(run_discord_bot(self))

    The bot will keep running until the orchestrator shuts down.
    """
    import os

    bot_token = token or os.getenv("DISCORD_BOT_TOKEN", "")
    if not bot_token:
        logger.warning("DISCORD_BOT_TOKEN not set — Discord bot will not start")
        return

    config = orchestrator.config or get_config()
    memory = orchestrator.memory or Memory()
    heartbeat = orchestrator.heartbeat or Heartbeat()

    bot = ArcanaBot(
        config=config,
        memory=memory,
        heartbeat=heartbeat,
        orchestrator=orchestrator,
    )

    # Expose bot instance on the orchestrator so other components can call
    # bot.alert_* methods directly.
    orchestrator.discord_bot = bot  # type: ignore[attr-defined]

    try:
        logger.info("Starting ARCANA Discord bot...")
        await bot.start(bot_token)
    except asyncio.CancelledError:
        logger.info("Discord bot task cancelled — shutting down")
        await bot.close()
    except Exception as exc:
        logger.error("Discord bot crashed: %s\n%s", exc, traceback.format_exc())
        await bot.close()


def start_bot_standalone() -> None:
    """Run the Discord bot by itself (for testing without the full orchestrator).

    Usage:
        python -m src.discord_bot
    """
    import os

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        logger.error("Set DISCORD_BOT_TOKEN in your environment or config/.env")
        return

    config = get_config()
    memory = Memory()
    heartbeat = Heartbeat()

    bot = ArcanaBot(config=config, memory=memory, heartbeat=heartbeat)
    bot.run(token)


if __name__ == "__main__":
    start_bot_standalone()
