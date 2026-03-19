"""ARCANA AI — Nightly self-improvement engine.

Every night, ARCANA:
1. Reads through all conversations/logs from the day
2. Identifies where Ian/Tan had to intervene
3. Figures out how to handle that class of problem autonomously next time
4. Extracts important facts from daily notes into knowledge graph (consolidation)
5. Updates tacit knowledge with new lessons learned
6. ACTUALLY BUILDS automations for the top bottlenecks (not just proposals)
7. Tracks improvement trajectory week-over-week
8. Learns from closed deals to optimize scanner queries and templates

This is what makes ARCANA get smarter every day — just like Felix.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory
from src.scheduler import ScheduledTask, TaskScheduler

logger = logging.getLogger("arcana.improve")

# ── Intervention categories for tracking ────────────────────────
INTERVENTION_CATEGORIES = [
    "content_approval",      # Ian/Tan had to approve/edit content
    "lead_qualification",    # Manual lead scoring or routing
    "customer_support",      # Had to handle support manually
    "error_recovery",        # Had to fix a crash or bug
    "strategy_decision",     # Business decision that needed human
    "tone_correction",       # Voice/personality adjustments
    "api_issue",             # External API problem needing manual fix
    "data_entry",            # Manual data input or correction
    "scheduling",            # Calendar or timing adjustments
    "other",
]


class SelfImprover:
    """Nightly self-improvement loop — now with teeth."""

    def __init__(
        self,
        llm: LLM,
        memory: Memory,
        scheduler: TaskScheduler | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.scheduler = scheduler

    # ── Core: Build Automation ──────────────────────────────────────

    async def build_automation(
        self, bottleneck: str, solution: str,
    ) -> dict[str, Any]:
        """Given a bottleneck and proposed solution, actually build it.

        1. Generate a scheduled task definition for the scheduler
        2. Save the automation logic as a skill in memory/tacit/skills/
        3. Register the task with the scheduler
        4. Log what was built
        """
        logger.info("Building automation for bottleneck: %s", bottleneck[:80])

        # Ask LLM to generate concrete automation spec
        spec = await self.llm.ask_json(
            f"You are ARCANA AI. Build a concrete automation to solve this bottleneck.\n\n"
            f"Bottleneck: {bottleneck}\n"
            f"Proposed solution: {solution}\n\n"
            f"Generate a complete automation specification.\n"
            f"Return JSON: {{\n"
            f'  "task_name": str (snake_case, unique identifier),\n'
            f'  "frequency": "daily"|"weekly"|"monthly",\n'
            f'  "hour_utc": int (0-23, best time to run),\n'
            f'  "day_of_week": int|null (0=Mon, 6=Sun, null for daily/monthly),\n'
            f'  "priority": int (1=highest, 10=lowest),\n'
            f'  "description": str (one-line description for schedule),\n'
            f'  "skill_name": str (kebab-case skill file name),\n'
            f'  "skill_logic": str (detailed markdown describing: trigger conditions, '
            f"step-by-step actions, decision criteria, escalation rules, "
            f"success metrics — be SPECIFIC enough that an LLM agent can execute this),\n"
            f'  "expected_impact": str (what improvement this should produce)\n'
            f"}}",
            tier=Tier.OPUS,
        )

        task_name = spec.get("task_name", "auto_task")
        skill_name = spec.get("skill_name", task_name)
        skill_logic = spec.get("skill_logic", "")
        description = spec.get("description", bottleneck[:60])
        expected_impact = spec.get("expected_impact", "Unknown")

        # 1. Save skill to memory/tacit/skills/
        skill_content = (
            f"## Bottleneck\n{bottleneck}\n\n"
            f"## Solution\n{solution}\n\n"
            f"## Automation Logic\n{skill_logic}\n\n"
            f"## Expected Impact\n{expected_impact}\n\n"
            f"## Created\n{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"## Status\nactive\n"
        )
        skills_dir = self.memory.tacit / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skills_dir / f"{skill_name}.md"
        skill_path.write_text(f"# Skill: {skill_name}\n\n{skill_content}")
        logger.info("Saved skill: %s", skill_path)

        # 2. Register scheduled task
        if self.scheduler is not None:
            task = ScheduledTask(
                name=task_name,
                frequency=spec.get("frequency", "daily"),
                description=description,
                day_of_week=spec.get("day_of_week"),
                hour_utc=spec.get("hour_utc", 12),
                priority=spec.get("priority", 5),
            )
            self.scheduler.add_task(task)
            logger.info("Registered scheduled task: %s (%s)", task_name, task.frequency)

        # 3. Log what was built
        self.memory.log(
            f"AUTOMATION BUILT: {task_name}\n"
            f"Bottleneck: {bottleneck}\n"
            f"Solution: {solution}\n"
            f"Skill file: skills/{skill_name}.md\n"
            f"Schedule: {spec.get('frequency', 'daily')} at {spec.get('hour_utc', 12)}:00 UTC\n"
            f"Expected impact: {expected_impact}",
            "Self-Improvement",
        )

        # 4. Track in automation registry
        registry = self.memory.get_tacit("automation-registry")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"- [{ts}] **{task_name}**: {description} (impact: {expected_impact})"
        if registry:
            updated = f"{registry}\n{entry}"
        else:
            updated = f"## Automations Built\n\n{entry}"
        self.memory.save_tacit("automation-registry", updated)

        return {
            "task_name": task_name,
            "skill_name": skill_name,
            "frequency": spec.get("frequency", "daily"),
            "description": description,
            "expected_impact": expected_impact,
        }

    # ── Track Interventions ─────────────────────────────────────────

    async def track_interventions(self) -> dict[str, Any]:
        """Scan today's logs for human interventions. Categorize each one."""
        today = self.memory.get_today()
        if not today:
            return {"total": 0, "categories": {}, "interventions": []}

        result = await self.llm.ask_json(
            f"You are ARCANA AI. Analyze today's logs and identify every instance "
            f"where Ian or Tan had to manually intervene — approvals, corrections, "
            f"manual actions, error fixes, decisions ARCANA couldn't make alone.\n\n"
            f"Today's log:\n{today[-4000:]}\n\n"
            f"Valid categories: {', '.join(INTERVENTION_CATEGORIES)}\n\n"
            f"Return JSON: {{\n"
            f'  "interventions": [\n'
            f"    {{\n"
            f'      "description": str (what happened),\n'
            f'      "category": str (one of the valid categories),\n'
            f'      "could_automate": bool (could ARCANA learn to handle this?),\n'
            f'      "automation_idea": str (how to automate, empty if cannot)\n'
            f"    }}\n"
            f"  ]\n"
            f"}}",
            tier=Tier.SONNET,
        )

        interventions = result.get("interventions", [])

        # Tally by category
        categories: dict[str, int] = {}
        for i in interventions:
            cat = i.get("category", "other")
            categories[cat] = categories.get(cat, 0) + 1

        # Save to daily intervention log
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = f"## Interventions — {ts}\n\nTotal: {len(interventions)}\n\n"
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            log_content += f"- **{cat}**: {count}\n"
        log_content += "\n### Details\n"
        for i in interventions:
            automatable = " [AUTOMATABLE]" if i.get("could_automate") else ""
            log_content += (
                f"- [{i.get('category', 'other')}]{automatable} {i.get('description', 'N/A')}\n"
            )
            if i.get("automation_idea"):
                log_content += f"  - Idea: {i['automation_idea']}\n"

        # Append to rolling intervention tracker
        existing = self.memory.get_tacit("intervention-log")
        if existing:
            updated = f"{existing}\n\n{log_content}"
        else:
            updated = log_content
        self.memory.save_tacit("intervention-log", updated)

        self.memory.log(
            f"Tracked {len(interventions)} interventions: "
            + ", ".join(f"{cat}={count}" for cat, count in categories.items()),
            "Self-Improvement",
        )

        return {
            "total": len(interventions),
            "categories": categories,
            "interventions": interventions,
        }

    # ── Measure Improvement ─────────────────────────────────────────

    async def measure_improvement(self) -> dict[str, Any]:
        """Compare this week's interventions to last week's. Are we improving?"""
        intervention_log = self.memory.get_tacit("intervention-log")
        if not intervention_log:
            return {"status": "no_data", "message": "No intervention data yet."}

        today = datetime.now(timezone.utc)
        this_week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        last_week_start = (today - timedelta(days=today.weekday() + 7)).strftime("%Y-%m-%d")
        last_week_end = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")

        result = await self.llm.ask_json(
            f"You are ARCANA AI measuring self-improvement.\n\n"
            f"Analyze the intervention log and compare this week ({this_week_start} to today) "
            f"vs last week ({last_week_start} to {last_week_end}).\n\n"
            f"Intervention log:\n{intervention_log[-6000:]}\n\n"
            f"Return JSON: {{\n"
            f'  "this_week_total": int,\n'
            f'  "last_week_total": int,\n'
            f'  "improvement_pct": float (negative = fewer interventions = good),\n'
            f'  "this_week_by_category": {{str: int}},\n'
            f'  "last_week_by_category": {{str: int}},\n'
            f'  "improving_categories": [str] (categories with fewer interventions),\n'
            f'  "worsening_categories": [str] (categories with more interventions),\n'
            f'  "assessment": str (1-2 sentence overall assessment),\n'
            f'  "focus_area": str (the category to prioritize automating next)\n'
            f"}}",
            tier=Tier.SONNET,
        )

        # Log the measurement
        self.memory.log(
            f"Improvement Measurement\n"
            f"This week: {result.get('this_week_total', '?')} interventions\n"
            f"Last week: {result.get('last_week_total', '?')} interventions\n"
            f"Change: {result.get('improvement_pct', '?')}%\n"
            f"Assessment: {result.get('assessment', 'N/A')}\n"
            f"Focus area: {result.get('focus_area', 'N/A')}",
            "Self-Improvement",
        )

        return result

    # ── Update Query Performance ────────────────────────────────────

    async def update_query_performance(self) -> dict[str, Any]:
        """Track which scanner queries find opportunities that convert.

        Reads the CRM deal data and opportunity scan logs to correlate:
        - Which queries found prospects that became deals
        - Which queries produce noise (lots of responses, no conversions)
        Promotes winners, demotes losers.
        """
        # Gather scanner activity + deal outcomes
        scan_log = self.memory.get_tacit("scan-performance")
        daily_notes = self.memory.get_recent_notes(7) if hasattr(self.memory, "get_recent_notes") else self.memory.get_today()

        # Get deal data to see what converted
        deal_data_parts: list[str] = []
        for key in self.memory.list_knowledge("projects"):
            if key.startswith("deal-"):
                data = self.memory.get_knowledge("projects", key)
                if data and ("won" in data.lower() or "active" in data.lower()):
                    deal_data_parts.append(data[:500])
        deal_summary = "\n---\n".join(deal_data_parts[-10:]) if deal_data_parts else "No closed deals yet."

        result = await self.llm.ask_json(
            f"You are ARCANA AI optimizing your opportunity scanner queries.\n\n"
            f"Scanner performance log:\n{(scan_log or 'No data yet.')[-3000:]}\n\n"
            f"Recent daily notes:\n{daily_notes[-3000:]}\n\n"
            f"Won/active deals:\n{deal_summary[-2000:]}\n\n"
            f"Analyze which scanner queries and approaches are working.\n"
            f"Return JSON: {{\n"
            f'  "winning_queries": [str] (queries that led to conversions),\n'
            f'  "losing_queries": [str] (queries with low/no conversion),\n'
            f'  "new_queries_to_try": [str] (new queries based on winning patterns),\n'
            f'  "queries_to_retire": [str] (stop wasting cycles on these),\n'
            f'  "overall_conversion_assessment": str,\n'
            f'  "recommendations": [str]\n'
            f"}}",
            tier=Tier.SONNET,
        )

        # Save updated performance data
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        perf_entry = (
            f"\n\n## Performance Review — {ts}\n"
            f"Winning: {', '.join(result.get('winning_queries', ['none yet']))}\n"
            f"Losing: {', '.join(result.get('losing_queries', ['none yet']))}\n"
            f"New to try: {', '.join(result.get('new_queries_to_try', []))}\n"
            f"Retiring: {', '.join(result.get('queries_to_retire', []))}\n"
            f"Assessment: {result.get('overall_conversion_assessment', 'N/A')}\n"
        )
        existing = scan_log or ""
        self.memory.save_tacit("scan-performance", existing + perf_entry)

        self.memory.log(
            f"Query Performance Updated\n"
            f"Winners: {len(result.get('winning_queries', []))}\n"
            f"Losers: {len(result.get('losing_queries', []))}\n"
            f"New queries: {len(result.get('new_queries_to_try', []))}\n"
            f"Retired: {len(result.get('queries_to_retire', []))}",
            "Self-Improvement",
        )

        return result

    # ── Learn from Success ──────────────────────────────────────────

    async def learn_from_success(self) -> dict[str, Any]:
        """When a deal closes (CRM stage = won), trace the winning pattern.

        Identifies:
        - Which scanner query found them
        - Which response template was used
        - Which follow-up sequence worked
        Saves as a reusable winning pattern.
        """
        # Find won deals
        won_deals: list[str] = []
        for key in self.memory.list_knowledge("projects"):
            if not key.startswith("deal-"):
                continue
            data = self.memory.get_knowledge("projects", key)
            if data and "won" in data.lower():
                won_deals.append(data[:800])

        if not won_deals:
            logger.info("No won deals to learn from yet.")
            return {"patterns_extracted": 0, "message": "No won deals found."}

        # Load existing patterns to avoid duplicates
        existing_patterns = self.memory.get_tacit("winning-patterns")

        result = await self.llm.ask_json(
            f"You are ARCANA AI learning from successful deals.\n\n"
            f"Won deals:\n{'---'.join(won_deals[-5:])}\n\n"
            f"Existing winning patterns (avoid duplicates):\n{(existing_patterns or 'None yet.')[-2000:]}\n\n"
            f"For each won deal, trace the success path and extract reusable patterns.\n"
            f"Return JSON: {{\n"
            f'  "patterns": [\n'
            f"    {{\n"
            f'      "deal_ref": str (deal identifier),\n'
            f'      "discovery_method": str (how we found them — query, mention, referral, etc.),\n'
            f'      "initial_response_style": str (what approach worked in first contact),\n'
            f'      "followup_sequence": str (what follow-up cadence/messaging closed the deal),\n'
            f'      "service_sold": str (what service they bought),\n'
            f'      "deal_value": str (if available),\n'
            f'      "key_insight": str (the ONE thing that made this deal work),\n'
            f'      "reusable_template": str (a response template based on what worked)\n'
            f"    }}\n"
            f"  ]\n"
            f"}}",
            tier=Tier.OPUS,
        )

        patterns = result.get("patterns", [])
        if not patterns:
            return {"patterns_extracted": 0, "message": "No new patterns found."}

        # Save winning patterns
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_content = f"\n\n## Patterns Extracted — {ts}\n\n"
        for p in patterns:
            new_content += (
                f"### {p.get('deal_ref', 'Unknown Deal')}\n"
                f"- **Discovery**: {p.get('discovery_method', 'N/A')}\n"
                f"- **First contact style**: {p.get('initial_response_style', 'N/A')}\n"
                f"- **Follow-up sequence**: {p.get('followup_sequence', 'N/A')}\n"
                f"- **Service**: {p.get('service_sold', 'N/A')}\n"
                f"- **Value**: {p.get('deal_value', 'N/A')}\n"
                f"- **Key insight**: {p.get('key_insight', 'N/A')}\n"
                f"- **Template**: {p.get('reusable_template', 'N/A')}\n\n"
            )

        updated = (existing_patterns or "# Winning Patterns\n") + new_content
        self.memory.save_tacit("winning-patterns", updated)

        self.memory.log(
            f"Learned from {len(patterns)} won deal(s). "
            f"Key insights: {', '.join(p.get('key_insight', '?')[:50] for p in patterns)}",
            "Self-Improvement",
        )

        return {"patterns_extracted": len(patterns), "patterns": patterns}

    # ── Weekly Improvement Report ───────────────────────────────────

    async def generate_weekly_improvement_report(self) -> str:
        """What did ARCANA learn this week? New automations? Improvement trajectory?"""
        intervention_log = self.memory.get_tacit("intervention-log") or "No data."
        automation_registry = self.memory.get_tacit("automation-registry") or "No automations built yet."
        winning_patterns = self.memory.get_tacit("winning-patterns") or "No patterns yet."
        scan_perf = self.memory.get_tacit("scan-performance") or "No scan data."
        lessons = self.memory.get_tacit("lessons-learned") or "No lessons."

        improvement = await self.measure_improvement()

        report = await self.llm.ask(
            f"You are ARCANA AI generating your weekly self-improvement report.\n\n"
            f"Intervention log (recent):\n{intervention_log[-3000:]}\n\n"
            f"Automation registry:\n{automation_registry[-2000:]}\n\n"
            f"Winning patterns:\n{winning_patterns[-2000:]}\n\n"
            f"Scanner performance:\n{scan_perf[-1500:]}\n\n"
            f"Lessons learned:\n{lessons[-1500:]}\n\n"
            f"Week-over-week improvement data:\n{json.dumps(improvement, default=str)}\n\n"
            f"Write a concise weekly improvement report in markdown. Include:\n"
            f"1. **Improvement Score**: Interventions this week vs last (with % change)\n"
            f"2. **Automations Built**: What new automations were created this week\n"
            f"3. **Winning Patterns**: New patterns extracted from closed deals\n"
            f"4. **Scanner Optimization**: Query performance changes\n"
            f"5. **Lessons Learned**: Key new insights\n"
            f"6. **Next Week Focus**: Top 3 areas to improve\n"
            f"7. **Autonomy Trajectory**: Is ARCANA becoming more autonomous? Specific evidence.\n\n"
            f"Keep it factual, numbers-driven, no fluff.",
            tier=Tier.SONNET,
        )

        # Save the report
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge("resources", f"weekly-improvement-{ts}", report)
        self.memory.log(
            f"Weekly Improvement Report generated. "
            f"Interventions: {improvement.get('this_week_total', '?')} "
            f"(change: {improvement.get('improvement_pct', '?')}%)",
            "Self-Improvement",
        )

        return report

    # ── Nightly Review (the real one) ───────────────────────────────

    async def run_nightly_review(self) -> dict[str, Any]:
        """Full nightly self-improvement cycle — now actually builds automations."""
        logger.info("Starting nightly self-improvement review")

        # Step 0: Track today's interventions
        intervention_data = await self.track_interventions()

        context = self.memory.get_consolidation_context()

        # Step 1: Analyze the day
        analysis = await self.llm.ask_json(
            f"You are ARCANA AI running your nightly self-improvement review.\n"
            f"Analyze today's activity and identify improvements.\n\n"
            f"Today's interventions tracked: {json.dumps(intervention_data, default=str)}\n\n"
            f"{context}\n\n"
            f"Return JSON: {{"
            f'"summary": str (2-3 sentence day summary), '
            f'"wins": [str] (things that went well), '
            f'"bottlenecks": [{{"description": str, "proposed_solution": str}}] '
            f"(where human intervention was needed or things got stuck — include a concrete solution for each), "
            f'"lessons_learned": [str] (new insights to remember), '
            f'"knowledge_to_extract": [{{"name": str, "category": "projects"|"areas"|"resources", "content": str}}] (important facts to save to knowledge graph), '
            f'"tomorrow_priorities": [str] (top 5 things to focus on tomorrow)}}',
            tier=Tier.OPUS,
        )

        # Step 2: Consolidate — extract knowledge from daily notes
        for item in analysis.get("knowledge_to_extract", []):
            self.memory.save_knowledge(
                item.get("category", "resources"),
                item["name"],
                item["content"],
            )
            logger.info("Consolidated: %s -> %s", item["name"], item.get("category"))

        # Step 3: Update tacit knowledge with lessons learned
        lessons = analysis.get("lessons_learned", [])
        if lessons:
            existing = self.memory.get_tacit("lessons-learned")
            new_lessons = "\n".join(f"- {lesson}" for lesson in lessons)
            updated = f"{existing}\n\n## Lessons from today\n{new_lessons}" if existing else new_lessons
            self.memory.save_tacit("lessons-learned", updated)

        # Step 4: Log bottlenecks
        bottlenecks = analysis.get("bottlenecks", [])
        if bottlenecks:
            existing = self.memory.get_tacit("bottlenecks")
            new_bottlenecks = "\n".join(
                f"- {b['description'] if isinstance(b, dict) else b}" for b in bottlenecks
            )
            updated = f"{existing}\n\n## Bottlenecks from today\n{new_bottlenecks}" if existing else new_bottlenecks
            self.memory.save_tacit("bottlenecks", updated)

        # Step 5: ACTUALLY BUILD automations for the top 3 bottlenecks
        automations_built: list[dict[str, Any]] = []
        for bottleneck in bottlenecks[:3]:
            if isinstance(bottleneck, dict):
                desc = bottleneck.get("description", "")
                sol = bottleneck.get("proposed_solution", "")
            else:
                desc = str(bottleneck)
                sol = ""

            if not desc:
                continue

            # If no solution was proposed, generate one
            if not sol:
                sol_result = await self.llm.ask_json(
                    f"Propose a concrete automation solution for this bottleneck:\n{desc}\n\n"
                    f'Return JSON: {{"solution": str}}',
                    tier=Tier.SONNET,
                )
                sol = sol_result.get("solution", "Automate via scheduled check and notification")

            try:
                result = await self.build_automation(desc, sol)
                automations_built.append(result)
                logger.info("Built automation: %s", result.get("task_name"))
            except Exception as exc:
                logger.error("Failed to build automation for '%s': %s", desc[:60], exc)

        # Step 6: Learn from any newly won deals
        success_patterns = await self.learn_from_success()

        # Step 6b: Learn from failures too
        failure_patterns = await self.learn_from_failure()

        # Step 6c: Cross-channel analysis (weekly on Wednesdays)
        cross_channel = {}
        if datetime.now(timezone.utc).weekday() == 2:
            cross_channel = await self.cross_channel_analysis()

        # Step 7: Update query performance
        query_perf = await self.update_query_performance()

        # Step 8: Check if it's end of week (Friday) — generate weekly report
        weekly_report = None
        if datetime.now(timezone.utc).weekday() == 4:  # Friday
            weekly_report = await self.generate_weekly_improvement_report()

        # Step 9: Log the review
        self.memory.log(
            f"Nightly Review Complete\n"
            f"Summary: {analysis.get('summary', 'N/A')}\n"
            f"Wins: {len(analysis.get('wins', []))}\n"
            f"Bottlenecks: {len(bottlenecks)}\n"
            f"Automations BUILT: {len(automations_built)}\n"
            f"Interventions tracked: {intervention_data.get('total', 0)}\n"
            f"Lessons: {len(lessons)}\n"
            f"Knowledge extracted: {len(analysis.get('knowledge_to_extract', []))}\n"
            f"Success patterns: {success_patterns.get('patterns_extracted', 0)}\n"
            f"Tomorrow's priorities: {', '.join(analysis.get('tomorrow_priorities', [])[:3])}",
            "Nightly Review",
        )

        logger.info(
            "Nightly review done: %d wins, %d bottlenecks, %d automations built, "
            "%d lessons, %d knowledge items, %d success patterns",
            len(analysis.get("wins", [])),
            len(bottlenecks),
            len(automations_built),
            len(lessons),
            len(analysis.get("knowledge_to_extract", [])),
            success_patterns.get("patterns_extracted", 0),
        )

        analysis["automations_built"] = automations_built
        analysis["interventions"] = intervention_data
        analysis["success_patterns"] = success_patterns
        analysis["failure_patterns"] = failure_patterns
        analysis["cross_channel"] = cross_channel
        analysis["query_performance"] = query_perf
        if weekly_report:
            analysis["weekly_report"] = weekly_report

        return analysis

    # ── Learn from Failure ────────────────────────────────────────

    async def learn_from_failure(self) -> dict[str, Any]:
        """When a deal is lost or a skill fails, trace the failure pattern.

        Identifies:
        - What went wrong (timing, messaging, targeting, pricing)
        - Which queries/channels produced bad leads
        - What to avoid next time
        Saves as anti-patterns to prevent repeating mistakes.
        """
        # Find lost deals
        lost_deals: list[str] = []
        for key in self.memory.list_knowledge("projects"):
            if not key.startswith("deal-"):
                continue
            data = self.memory.get_knowledge("projects", key)
            if data and ("lost" in data.lower() or "churned" in data.lower()
                         or "rejected" in data.lower() or "ghosted" in data.lower()):
                lost_deals.append(data[:800])

        # Get skill execution failures
        skill_history = self.memory.get_tacit("skill-execution-history") or ""
        failures = [
            line for line in skill_history.splitlines()
            if "FAILED" in line
        ]

        if not lost_deals and not failures:
            logger.info("No failures to learn from yet.")
            return {"patterns_extracted": 0, "message": "No failures found."}

        existing_anti = self.memory.get_tacit("anti-patterns")

        result = await self.llm.ask_json(
            f"You are ARCANA AI learning from failures to avoid repeating them.\n\n"
            f"Lost/churned deals:\n{'---'.join(lost_deals[-5:]) if lost_deals else 'None'}\n\n"
            f"Failed skill executions:\n{chr(10).join(failures[-10:]) if failures else 'None'}\n\n"
            f"Existing anti-patterns (avoid duplicates):\n{(existing_anti or 'None yet.')[-1500:]}\n\n"
            f"For each failure, trace the failure path and extract anti-patterns.\n"
            f"Return JSON: {{\n"
            f'  "anti_patterns": [\n'
            f"    {{\n"
            f'      "source": str (deal-X or skill-Y),\n'
            f'      "failure_type": "timing"|"messaging"|"targeting"|"pricing"|"execution"|"technical",\n'
            f'      "what_went_wrong": str (specific description),\n'
            f'      "root_cause": str (why it really failed),\n'
            f'      "avoidance_rule": str (concrete rule to prevent this),\n'
            f'      "queries_to_deprioritize": [str] (scanner queries that led here),\n'
            f'      "channels_to_avoid": [str] (channels that dont work for this case)\n'
            f"    }}\n"
            f"  ],\n"
            f'  "strategic_adjustments": [str] (high-level changes to make)\n'
            f"}}",
            tier=Tier.OPUS,
        )

        patterns = result.get("anti_patterns", [])
        if not patterns:
            return {"patterns_extracted": 0, "message": "No new anti-patterns found."}

        # Save anti-patterns
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_content = f"\n\n## Anti-Patterns Extracted — {ts}\n\n"
        for p in patterns:
            new_content += (
                f"### {p.get('source', 'Unknown')}\n"
                f"- **Type**: {p.get('failure_type', 'N/A')}\n"
                f"- **What went wrong**: {p.get('what_went_wrong', 'N/A')}\n"
                f"- **Root cause**: {p.get('root_cause', 'N/A')}\n"
                f"- **RULE**: {p.get('avoidance_rule', 'N/A')}\n"
                f"- **Deprioritize queries**: {', '.join(p.get('queries_to_deprioritize', []))}\n"
                f"- **Avoid channels**: {', '.join(p.get('channels_to_avoid', []))}\n\n"
            )

        updated = (existing_anti or "# Anti-Patterns (Failure Learning)\n") + new_content
        self.memory.save_tacit("anti-patterns", updated)

        # Save strategic adjustments
        adjustments = result.get("strategic_adjustments", [])
        if adjustments:
            lessons = self.memory.get_tacit("lessons-learned") or ""
            adj_content = f"\n\n## Failure-Driven Adjustments — {ts}\n"
            adj_content += "\n".join(f"- {adj}" for adj in adjustments)
            self.memory.save_tacit("lessons-learned", lessons + adj_content)

        self.memory.log(
            f"Learned from {len(patterns)} failure(s). "
            f"Anti-patterns: {', '.join(p.get('avoidance_rule', '?')[:40] for p in patterns)}",
            "Self-Improvement",
        )

        return {
            "patterns_extracted": len(patterns),
            "anti_patterns": patterns,
            "strategic_adjustments": adjustments,
        }

    # ── Cross-Channel Pattern Learning ─────────────────────────────

    async def cross_channel_analysis(self) -> dict[str, Any]:
        """Find patterns that span multiple channels.

        Example: "Leads from Reddit that engage on X → 2x higher close rate"
        """
        winning = self.memory.get_tacit("winning-patterns") or ""
        anti = self.memory.get_tacit("anti-patterns") or ""
        scan_perf = self.memory.get_tacit("scan-performance") or ""

        result = await self.llm.ask_json(
            f"You are ARCANA AI analyzing cross-channel patterns.\n\n"
            f"Winning patterns:\n{winning[-2000:]}\n\n"
            f"Anti-patterns:\n{anti[-1500:]}\n\n"
            f"Scanner performance:\n{scan_perf[-1500:]}\n\n"
            f"Find patterns that SPAN channels. Examples:\n"
            f"- 'Leads discovered on Reddit who also engage on X convert 2x better'\n"
            f"- 'Email outreach works best for leads originally from Upwork'\n"
            f"- 'Newsletter subscribers who came from SEO have 3x higher LTV'\n\n"
            f"Return JSON: {{\n"
            f'  "cross_channel_patterns": [\n'
            f"    {{\n"
            f'      "pattern": str (clear description),\n'
            f'      "channels_involved": [str],\n'
            f'      "confidence": float (0-1),\n'
            f'      "actionable_insight": str (what to do differently),\n'
            f'      "estimated_impact": str\n'
            f"    }}\n"
            f"  ],\n"
            f'  "recommended_channel_combinations": [str],\n'
            f'  "channels_to_connect_better": [str]\n'
            f"}}",
            tier=Tier.OPUS,
        )

        # Save cross-channel insights
        patterns = result.get("cross_channel_patterns", [])
        if patterns:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            content = f"\n\n## Cross-Channel Insights — {ts}\n\n"
            for p in patterns:
                content += (
                    f"- **{p.get('pattern', 'N/A')}** "
                    f"(channels: {', '.join(p.get('channels_involved', []))}, "
                    f"confidence: {p.get('confidence', 0):.0%})\n"
                    f"  → {p.get('actionable_insight', 'N/A')}\n"
                )
            existing = self.memory.get_tacit("cross-channel-insights") or "# Cross-Channel Insights\n"
            self.memory.save_tacit("cross-channel-insights", existing + content)

        return result

    async def propose_automations(self) -> list[dict[str, str]]:
        """Based on accumulated bottlenecks, propose new automations to build."""
        bottlenecks = self.memory.get_tacit("bottlenecks")
        if not bottlenecks:
            return []

        result = await self.llm.ask_json(
            f"Based on these recurring bottlenecks, propose automations ARCANA AI should build:\n\n"
            f"{bottlenecks[-2000:]}\n\n"
            f"For each, describe:\n"
            f"- What it automates\n"
            f"- How to implement it (specific API, cron job, or script)\n"
            f"- Expected time savings\n\n"
            f'Return JSON: {{"automations": [{{"name": str, "description": str, "implementation": str, "time_saved": str}}]}}',
            tier=Tier.OPUS,
        )

        return result.get("automations", [])
