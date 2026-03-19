"""ARCANA AI — Evolution Tracker.

Measures ARCANA's autonomy trajectory over time:
1. Autonomy Score — % of decisions made without human intervention
2. Skill Effectiveness — which learned skills actually work
3. Revenue Attribution — which automations drive revenue
4. Feedback Loops — closed-loop improvement tracking
5. Hypothesis Testing — "thesis → test → confirm/refute → adapt" cycle

This is the brain's brain — it makes sure ARCANA is actually getting smarter.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.evolution")


class EvolutionTracker:
    """Track and measure ARCANA's evolution over time."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    # ── Autonomy Score ─────────────────────────────────────────────

    async def calculate_autonomy_score(self) -> dict[str, Any]:
        """Calculate the overall autonomy score (0-100).

        Measures:
        - Decision autonomy: % of decisions made without human input
        - Execution autonomy: % of tasks completed without human help
        - Recovery autonomy: % of errors self-recovered
        - Revenue autonomy: % of revenue from automated channels
        """
        # Gather data sources
        intervention_log = self.memory.get_tacit("intervention-log") or ""
        automation_registry = self.memory.get_tacit("automation-registry") or ""
        skill_history = self.memory.get_tacit("skill-execution-history") or ""
        today = self.memory.get_today()
        recent_days = self.memory.get_recent_days(7)
        week_logs = "\n---\n".join(content[:500] for _, content in recent_days)

        result = await self.llm.ask_json(
            f"You are ARCANA AI measuring your autonomy level.\n\n"
            f"Analyze ALL data to calculate your autonomy score.\n\n"
            f"Intervention log (recent):\n{intervention_log[-2000:]}\n\n"
            f"Automations built:\n{automation_registry[-1500:]}\n\n"
            f"Skill execution history:\n{skill_history[-1500:]}\n\n"
            f"This week's activity:\n{week_logs[-2000:]}\n\n"
            f"Score each dimension 0-100:\n"
            f"- decision_autonomy: How many decisions did ARCANA make alone vs needing human?\n"
            f"- execution_autonomy: How many tasks completed without human intervention?\n"
            f"- recovery_autonomy: How many errors were self-recovered?\n"
            f"- revenue_autonomy: How much revenue comes from automated vs manual channels?\n"
            f"- learning_autonomy: How effectively is ARCANA learning from mistakes?\n\n"
            f"Return JSON: {{\n"
            f'  "decision_autonomy": int,\n'
            f'  "execution_autonomy": int,\n'
            f'  "recovery_autonomy": int,\n'
            f'  "revenue_autonomy": int,\n'
            f'  "learning_autonomy": int,\n'
            f'  "overall_score": int (weighted average),\n'
            f'  "trend": "improving"|"stable"|"declining",\n'
            f'  "evidence": [str] (3-5 specific examples supporting the score),\n'
            f'  "bottleneck_to_higher_autonomy": str (the ONE thing preventing higher autonomy),\n'
            f'  "next_milestone": str (what would the next 10-point jump look like?)\n'
            f"}}",
            tier=Tier.SONNET,
        )

        # Save score history
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        score_entry = (
            f"\n\n## Score — {ts}\n"
            f"Overall: {result.get('overall_score', 0)}/100 ({result.get('trend', 'unknown')})\n"
            f"Decision: {result.get('decision_autonomy', 0)} | "
            f"Execution: {result.get('execution_autonomy', 0)} | "
            f"Recovery: {result.get('recovery_autonomy', 0)} | "
            f"Revenue: {result.get('revenue_autonomy', 0)} | "
            f"Learning: {result.get('learning_autonomy', 0)}\n"
            f"Bottleneck: {result.get('bottleneck_to_higher_autonomy', 'N/A')}\n"
            f"Next milestone: {result.get('next_milestone', 'N/A')}\n"
        )
        existing = self.memory.get_tacit("autonomy-scores") or "# Autonomy Score History\n"
        self.memory.save_tacit("autonomy-scores", existing + score_entry)

        self.memory.log(
            f"Autonomy Score: {result.get('overall_score', 0)}/100 ({result.get('trend', '?')})\n"
            f"Bottleneck: {result.get('bottleneck_to_higher_autonomy', 'N/A')}",
            "Evolution",
        )

        return result

    # ── Revenue Attribution ────────────────────────────────────────

    async def attribute_revenue(self) -> dict[str, Any]:
        """Link automations/skills to the revenue they generated.

        Tracks which automated actions led to deals closing.
        """
        automation_registry = self.memory.get_tacit("automation-registry") or ""
        winning_patterns = self.memory.get_tacit("winning-patterns") or ""
        skill_history = self.memory.get_tacit("skill-execution-history") or ""

        # Get deal data
        deal_data: list[str] = []
        for key in self.memory.list_knowledge("projects"):
            if key.startswith("deal-"):
                data = self.memory.get_knowledge("projects", key)
                if data:
                    deal_data.append(f"{key}: {data[:300]}")

        result = await self.llm.ask_json(
            f"You are ARCANA AI tracking revenue attribution.\n\n"
            f"Automations built:\n{automation_registry[-2000:]}\n\n"
            f"Winning patterns:\n{winning_patterns[-1500:]}\n\n"
            f"Skill executions:\n{skill_history[-1500:]}\n\n"
            f"Deal data:\n{'---'.join(deal_data[-10:])}\n\n"
            f"For each revenue-generating event, trace it back to:\n"
            f"- Which automation/skill contributed\n"
            f"- Whether it was fully automated or needed human help\n"
            f"- Estimated revenue impact\n\n"
            f"Return JSON: {{\n"
            f'  "attributions": [\n'
            f"    {{\n"
            f'      "deal_or_event": str,\n'
            f'      "automation_source": str (which skill/automation),\n'
            f'      "fully_automated": bool,\n'
            f'      "estimated_revenue": float,\n'
            f'      "attribution_confidence": float (0-1)\n'
            f"    }}\n"
            f"  ],\n"
            f'  "total_automated_revenue": float,\n'
            f'  "total_assisted_revenue": float,\n'
            f'  "top_performing_automation": str,\n'
            f'  "underperforming_automations": [str]\n'
            f"}}",
            tier=Tier.SONNET,
        )

        # Save attribution data
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.log(
            f"Revenue Attribution — {ts}\n"
            f"Automated: ${result.get('total_automated_revenue', 0):,.2f}\n"
            f"Assisted: ${result.get('total_assisted_revenue', 0):,.2f}\n"
            f"Top performer: {result.get('top_performing_automation', 'N/A')}",
            "Evolution",
        )

        return result

    # ── Hypothesis Testing ─────────────────────────────────────────

    async def generate_hypothesis(self) -> dict[str, Any]:
        """Generate a testable hypothesis based on current data.

        Example: "Reddit leads convert 40% better than X leads"
        """
        winning_patterns = self.memory.get_tacit("winning-patterns") or ""
        scan_perf = self.memory.get_tacit("scan-performance") or ""
        lessons = self.memory.get_tacit("lessons-learned") or ""
        existing_hypotheses = self.memory.get_tacit("hypotheses") or ""

        result = await self.llm.ask_json(
            f"You are ARCANA AI generating testable business hypotheses.\n\n"
            f"Winning patterns:\n{winning_patterns[-1500:]}\n\n"
            f"Scanner performance:\n{scan_perf[-1500:]}\n\n"
            f"Lessons learned:\n{lessons[-1000:]}\n\n"
            f"Existing hypotheses (avoid duplicates):\n{existing_hypotheses[-1000:]}\n\n"
            f"Generate ONE new, specific, testable hypothesis about ARCANA's revenue operations.\n"
            f"It must be falsifiable within 1 week of data.\n\n"
            f"Return JSON: {{\n"
            f'  "hypothesis": str (clear, specific statement),\n'
            f'  "metric_to_track": str (what to measure),\n'
            f'  "success_threshold": str (what confirms it),\n'
            f'  "failure_threshold": str (what refutes it),\n'
            f'  "test_duration_days": int,\n'
            f'  "actions_to_take": [str] (what to do differently during the test),\n'
            f'  "expected_impact": str (what happens if confirmed)\n'
            f"}}",
            tier=Tier.OPUS,
        )

        # Save hypothesis
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = (
            f"\n\n## Hypothesis — {ts}\n"
            f"**{result.get('hypothesis', 'N/A')}**\n"
            f"- Metric: {result.get('metric_to_track', 'N/A')}\n"
            f"- Success if: {result.get('success_threshold', 'N/A')}\n"
            f"- Failure if: {result.get('failure_threshold', 'N/A')}\n"
            f"- Test duration: {result.get('test_duration_days', 7)} days\n"
            f"- Status: TESTING\n"
        )
        updated = (existing_hypotheses or "# Hypotheses\n") + entry
        self.memory.save_tacit("hypotheses", updated)

        self.memory.log(
            f"New hypothesis: {result.get('hypothesis', 'N/A')[:100]}",
            "Evolution",
        )

        return result

    async def evaluate_hypotheses(self) -> dict[str, Any]:
        """Evaluate active hypotheses against collected data."""
        hypotheses = self.memory.get_tacit("hypotheses") or ""
        if not hypotheses or "TESTING" not in hypotheses:
            return {"evaluated": 0, "message": "No active hypotheses to evaluate."}

        today = self.memory.get_today()
        recent_days = self.memory.get_recent_days(7)
        week_data = "\n---\n".join(content[:400] for _, content in recent_days)

        result = await self.llm.ask_json(
            f"You are ARCANA AI evaluating hypotheses.\n\n"
            f"Active hypotheses:\n{hypotheses[-3000:]}\n\n"
            f"This week's data:\n{week_data[-3000:]}\n\n"
            f"For each hypothesis marked TESTING, evaluate against the data.\n"
            f"Return JSON: {{\n"
            f'  "evaluations": [\n'
            f"    {{\n"
            f'      "hypothesis": str,\n'
            f'      "verdict": "confirmed"|"refuted"|"inconclusive",\n'
            f'      "evidence": str,\n'
            f'      "action_to_take": str (what to change based on this result),\n'
            f'      "new_hypothesis": str|null (follow-up hypothesis if any)\n'
            f"    }}\n"
            f"  ]\n"
            f"}}",
            tier=Tier.SONNET,
        )

        # Update hypothesis statuses
        evaluations = result.get("evaluations", [])
        for ev in evaluations:
            verdict = ev.get("verdict", "inconclusive").upper()
            hyp_text = ev.get("hypothesis", "")[:50]
            if hyp_text and verdict in ("CONFIRMED", "REFUTED"):
                # Find the hypothesis block that contains hyp_text and replace
                # its specific "Status: TESTING" rather than the first occurrence
                hyp_pos = hypotheses.find(hyp_text)
                if hyp_pos != -1:
                    status_pos = hypotheses.find("Status: TESTING", hyp_pos)
                    if status_pos != -1:
                        hypotheses = (
                            hypotheses[:status_pos]
                            + f"Status: {verdict}"
                            + hypotheses[status_pos + len("Status: TESTING"):]
                        )

        self.memory.save_tacit("hypotheses", hypotheses)

        self.memory.log(
            f"Hypothesis evaluation: {len(evaluations)} evaluated\n"
            + "\n".join(
                f"- {e.get('hypothesis', '?')[:60]}: {e.get('verdict', '?')}"
                for e in evaluations
            ),
            "Evolution",
        )

        return result

    # ── Full Evolution Report ──────────────────────────────────────

    async def generate_evolution_report(self) -> dict[str, Any]:
        """Comprehensive evolution report — run weekly."""
        autonomy = await self.calculate_autonomy_score()
        revenue_attr = await self.attribute_revenue()
        hypothesis_eval = await self.evaluate_hypotheses()

        # Generate new hypothesis for next week
        new_hypothesis = await self.generate_hypothesis()

        report = {
            "autonomy_score": autonomy,
            "revenue_attribution": revenue_attr,
            "hypothesis_evaluation": hypothesis_eval,
            "new_hypothesis": new_hypothesis,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Save evolution report
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.memory.save_knowledge(
            "resources",
            f"evolution-report-{ts}",
            json.dumps(report, indent=2, default=str),
        )

        return report
