"""ARCANA AI — Skill Executor.

Reads learned skills from memory/tacit/skills/ and executes them autonomously.
This closes the feedback loop: self_improve builds skills → skill_executor runs them.

Each skill is a markdown file with:
- Trigger conditions (when to run)
- Step-by-step actions (what to do)
- Success metrics (how to measure)
- Status (active/disabled/testing)

The executor:
1. Loads all active skills
2. Checks trigger conditions against current state
3. Executes matching skills via LLM-driven action planning
4. Tracks success/failure for feedback to self_improve
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.skill_executor")


class SkillResult:
    """Result of executing a skill."""

    def __init__(
        self, skill_name: str, success: bool,
        actions_taken: list[str] | None = None,
        error: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self.skill_name = skill_name
        self.success = success
        self.actions_taken = actions_taken or []
        self.error = error
        self.metrics = metrics or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "success": self.success,
            "actions_taken": self.actions_taken,
            "error": self.error,
            "metrics": self.metrics,
            "timestamp": self.timestamp,
        }


class SkillExecutor:
    """Loads and executes learned skills from the tacit skills directory."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory
        self.skills_dir = memory.tacit / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._execution_log: list[SkillResult] = []

    def list_skills(self, status_filter: str = "active") -> list[dict[str, Any]]:
        """List all skills with their metadata."""
        skills = []
        for path in self.skills_dir.glob("*.md"):
            content = path.read_text()
            status = self._extract_field(content, "Status") or "active"
            if status_filter and status != status_filter:
                continue
            skills.append({
                "name": path.stem,
                "path": str(path),
                "status": status,
                "bottleneck": self._extract_field(content, "Bottleneck") or "",
                "expected_impact": self._extract_field(content, "Expected Impact") or "",
                "created": self._extract_field(content, "Created") or "",
            })
        return skills

    def _extract_field(self, content: str, field: str) -> str:
        """Extract a field value from skill markdown."""
        pattern = rf"##\s+{re.escape(field)}\s*\n(.+?)(?=\n##|\Z)"
        match = re.search(pattern, content, re.DOTALL)
        return match.group(1).strip() if match else ""

    async def evaluate_triggers(self) -> list[dict[str, Any]]:
        """Check which skills should fire based on current state."""
        skills = self.list_skills("active")
        if not skills:
            return []

        today = self.memory.get_today()
        skill_summaries = "\n".join(
            f"- {s['name']}: {s['bottleneck'][:100]}" for s in skills
        )

        result = await self.llm.ask_json(
            f"You are ARCANA AI's skill executor. Decide which skills should run NOW.\n\n"
            f"Available skills:\n{skill_summaries}\n\n"
            f"Current state (today's log):\n{today[-2000:]}\n\n"
            f"Current time: {datetime.now(timezone.utc).strftime('%H:%M UTC %A')}\n\n"
            f"For each skill, decide if it should run based on:\n"
            f"- Is the bottleneck it addresses currently happening?\n"
            f"- Is it the right time to run this skill?\n"
            f"- Has it already run today?\n\n"
            f"Return JSON: {{\n"
            f'  "skills_to_run": [\n'
            f'    {{"name": str, "reason": str, "priority": int}}\n'
            f"  ]\n"
            f"}}",
            tier=Tier.HAIKU,
        )

        return result.get("skills_to_run", [])

    async def execute_skill(self, skill_name: str) -> SkillResult:
        """Execute a single skill by name."""
        path = self.skills_dir / f"{skill_name}.md"
        if not path.exists():
            return SkillResult(skill_name, False, error=f"Skill file not found: {skill_name}")

        content = path.read_text()
        logic = self._extract_field(content, "Automation Logic")
        bottleneck = self._extract_field(content, "Bottleneck")

        if not logic:
            return SkillResult(skill_name, False, error="No automation logic found")

        # Get current context
        today = self.memory.get_today()

        # Ask LLM to execute the skill
        result = await self.llm.ask_json(
            f"You are ARCANA AI executing an automation skill.\n\n"
            f"Skill: {skill_name}\n"
            f"Bottleneck it solves: {bottleneck}\n"
            f"Automation logic:\n{logic}\n\n"
            f"Current state:\n{today[-1500:]}\n\n"
            f"Execute this skill. Determine the concrete actions to take.\n"
            f"Return JSON: {{\n"
            f'  "actions_taken": [str] (specific actions executed),\n'
            f'  "success": bool (did the automation achieve its goal?),\n'
            f'  "outcome": str (what happened),\n'
            f'  "metrics": {{str: any}} (measurable results),\n'
            f'  "recommendations": str (how to improve this skill)\n'
            f"}}",
            tier=Tier.SONNET,
        )

        success = result.get("success", False)
        skill_result = SkillResult(
            skill_name=skill_name,
            success=success,
            actions_taken=result.get("actions_taken", []),
            error="" if success else result.get("outcome", "Unknown failure"),
            metrics=result.get("metrics", {}),
        )

        # Log execution
        self._execution_log.append(skill_result)
        self.memory.log(
            f"Skill '{skill_name}' executed: {'SUCCESS' if success else 'FAILED'}\n"
            f"Actions: {', '.join(skill_result.actions_taken[:3])}\n"
            f"Outcome: {result.get('outcome', 'N/A')[:200]}",
            "Skill Executor",
        )

        # Track in skill execution history
        self._update_skill_history(skill_name, skill_result)

        return skill_result

    async def run_due_skills(self) -> dict[str, Any]:
        """Evaluate triggers and execute all due skills."""
        triggered = await self.evaluate_triggers()
        results = {
            "evaluated": len(self.list_skills("active")),
            "triggered": len(triggered),
            "executed": 0,
            "succeeded": 0,
            "failed": 0,
            "results": [],
        }

        for skill_info in sorted(triggered, key=lambda s: s.get("priority", 5)):
            name = skill_info["name"]
            try:
                result = await self.execute_skill(name)
                results["executed"] += 1
                if result.success:
                    results["succeeded"] += 1
                else:
                    results["failed"] += 1
                results["results"].append(result.to_dict())
            except Exception as exc:
                logger.error("Skill execution failed for '%s': %s", name, exc)
                results["failed"] += 1

        return results

    def _update_skill_history(self, skill_name: str, result: SkillResult) -> None:
        """Track skill execution history for feedback loop."""
        history = self.memory.get_tacit("skill-execution-history")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = (
            f"- [{ts}] **{skill_name}**: "
            f"{'SUCCESS' if result.success else 'FAILED'} "
            f"| Actions: {len(result.actions_taken)} "
            f"| Metrics: {json.dumps(result.metrics, default=str)[:100]}"
        )
        if history:
            updated = f"{history}\n{entry}"
        else:
            updated = f"## Skill Execution History\n\n{entry}"
        self.memory.save_tacit("skill-execution-history", updated)

    def get_skill_effectiveness(self, skill_name: str) -> dict[str, Any]:
        """Calculate effectiveness metrics for a specific skill."""
        history = [r for r in self._execution_log if r.skill_name == skill_name]
        if not history:
            return {"runs": 0, "success_rate": 0.0}

        successes = sum(1 for r in history if r.success)
        return {
            "runs": len(history),
            "successes": successes,
            "failures": len(history) - successes,
            "success_rate": successes / len(history) if history else 0.0,
        }

    async def disable_failing_skills(self, threshold: float = 0.3) -> list[str]:
        """Auto-disable skills with success rate below threshold."""
        disabled = []
        for skill in self.list_skills("active"):
            effectiveness = self.get_skill_effectiveness(skill["name"])
            if effectiveness["runs"] >= 3 and effectiveness["success_rate"] < threshold:
                # Mark as disabled
                path = self.skills_dir / f"{skill['name']}.md"
                content = path.read_text()
                content = content.replace("## Status\nactive", "## Status\ndisabled (low success rate)")
                path.write_text(content)
                disabled.append(skill["name"])
                logger.info("Disabled failing skill: %s (%.0f%% success rate)",
                           skill["name"], effectiveness["success_rate"] * 100)

        return disabled
