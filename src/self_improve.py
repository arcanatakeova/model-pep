"""ARCANA AI — Nightly self-improvement engine.

Every night, ARCANA:
1. Reads through all conversations/logs from the day
2. Identifies where Ian/Tan had to intervene
3. Figures out how to handle that class of problem autonomously next time
4. Extracts important facts from daily notes into knowledge graph (consolidation)
5. Updates tacit knowledge with new lessons learned
6. Proposes new skills/automations to build

This is what makes ARCANA get smarter every day — just like Felix.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.improve")


class SelfImprover:
    """Nightly self-improvement loop."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    async def run_nightly_review(self) -> dict[str, Any]:
        """Full nightly self-improvement cycle."""
        logger.info("Starting nightly self-improvement review")

        context = self.memory.get_consolidation_context()

        # Step 1: Analyze the day
        analysis = await self.llm.ask_json(
            f"You are ARCANA AI running your nightly self-improvement review.\n"
            f"Analyze today's activity and identify improvements.\n\n"
            f"{context}\n\n"
            f"Return JSON: {{"
            f'"summary": str (2-3 sentence day summary), '
            f'"wins": [str] (things that went well), '
            f'"bottlenecks": [str] (where human intervention was needed or things got stuck), '
            f'"lessons_learned": [str] (new insights to remember), '
            f'"knowledge_to_extract": [{{"name": str, "category": "projects"|"areas"|"resources", "content": str}}] (important facts to save to knowledge graph), '
            f'"automations_to_build": [str] (new skills/cron jobs that would help), '
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
            logger.info("Consolidated: %s → %s", item["name"], item.get("category"))

        # Step 3: Update tacit knowledge with lessons learned
        lessons = analysis.get("lessons_learned", [])
        if lessons:
            existing = self.memory.get_tacit("lessons-learned")
            new_lessons = "\n".join(f"- {lesson}" for lesson in lessons)
            updated = f"{existing}\n\n## Lessons from today\n{new_lessons}" if existing else new_lessons
            self.memory.save_tacit("lessons-learned", updated)

        # Step 4: Log bottlenecks for future reference
        bottlenecks = analysis.get("bottlenecks", [])
        if bottlenecks:
            existing = self.memory.get_tacit("bottlenecks")
            new_bottlenecks = "\n".join(f"- {b}" for b in bottlenecks)
            updated = f"{existing}\n\n## Bottlenecks from today\n{new_bottlenecks}" if existing else new_bottlenecks
            self.memory.save_tacit("bottlenecks", updated)

        # Step 5: Log the review itself
        self.memory.log(
            f"Nightly Review Complete\n"
            f"Summary: {analysis.get('summary', 'N/A')}\n"
            f"Wins: {len(analysis.get('wins', []))}\n"
            f"Bottlenecks: {len(bottlenecks)}\n"
            f"Lessons: {len(lessons)}\n"
            f"Knowledge extracted: {len(analysis.get('knowledge_to_extract', []))}\n"
            f"Tomorrow's priorities: {', '.join(analysis.get('tomorrow_priorities', [])[:3])}",
            "Nightly Review",
        )

        logger.info(
            "Nightly review done: %d wins, %d bottlenecks, %d lessons, %d knowledge items",
            len(analysis.get("wins", [])),
            len(bottlenecks),
            len(lessons),
            len(analysis.get("knowledge_to_extract", [])),
        )

        return analysis

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
