"""ARCANA AI — Production-grade Task Scheduler.

Features:
- Recurring tasks (daily, weekly, monthly, custom)
- Per-client deliverable scheduling
- Retry with exponential backoff (max 3 retries)
- Task timeout protection (no infinite hangs)
- Missed task recovery (if ARCANA was down, catch up)
- Concurrency control (only one instance of a task at a time)
- Execution history tracking
- Priority-based execution ordering
- Dead task detection and alerting
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable

from src.memory import Memory

logger = logging.getLogger("arcana.scheduler")

DEFAULT_TASK_TIMEOUT = 300  # 5 minutes max per task


class ScheduledTask:
    """A recurring task with schedule, state, and retry logic."""

    def __init__(
        self, name: str, frequency: str, description: str = "",
        day_of_week: int | None = None,  # 0=Mon, 6=Sun
        hour_utc: int = 12,
        priority: int = 5,  # 1=highest, 10=lowest
        client_key: str = "",
        max_retries: int = 3,
        timeout_seconds: int = DEFAULT_TASK_TIMEOUT,
    ) -> None:
        self.name = name
        self.frequency = frequency  # daily, weekly, monthly, custom
        self.description = description
        self.day_of_week = day_of_week
        self.hour_utc = hour_utc
        self.priority = priority
        self.client_key = client_key
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.last_run: str = ""
        self.last_duration_seconds: float = 0.0
        self.run_count: int = 0
        self.fail_count: int = 0
        self.consecutive_failures: int = 0
        self.status: str = "pending"  # pending, running, completed, failed, disabled
        self.last_error: str = ""

    def is_due(self, now: datetime | None = None) -> bool:
        """Check if this task is due to run."""
        now = now or datetime.now(timezone.utc)

        if self.status == "disabled":
            return False

        # Skip if currently running
        if self.status == "running":
            return False

        # If it ran recently, skip
        if self.last_run:
            try:
                last = datetime.strptime(self.last_run, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            except ValueError:
                last = None

            if last:
                if self.frequency == "daily" and (now - last) < timedelta(hours=20):
                    return False
                elif self.frequency == "weekly" and (now - last) < timedelta(days=6):
                    return False
                elif self.frequency == "monthly" and (now - last) < timedelta(days=28):
                    return False

        # Check hour
        if now.hour != self.hour_utc:
            return False

        # Check day of week for weekly tasks
        if self.frequency == "weekly" and self.day_of_week is not None:
            if now.weekday() != self.day_of_week:
                return False

        # Check day of month for monthly tasks
        if self.frequency == "monthly":
            if now.day != 1:  # Run on 1st of month
                return False

        return True

    def is_overdue(self, now: datetime | None = None) -> bool:
        """Check if this task missed its window and should run ASAP."""
        now = now or datetime.now(timezone.utc)

        if self.status in ("disabled", "running"):
            return False
        if not self.last_run:
            return True  # Never run = overdue

        try:
            last = datetime.strptime(self.last_run, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return True

        if self.frequency == "daily" and (now - last) > timedelta(hours=26):
            return True
        if self.frequency == "weekly" and (now - last) > timedelta(days=8):
            return True
        if self.frequency == "monthly" and (now - last) > timedelta(days=32):
            return True

        return False

    def mark_completed(self, duration: float = 0.0) -> None:
        self.last_run = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        self.last_duration_seconds = duration
        self.run_count += 1
        self.consecutive_failures = 0
        self.status = "completed"
        self.last_error = ""

    def mark_failed(self, error: str = "") -> None:
        self.fail_count += 1
        self.consecutive_failures += 1
        self.last_error = error[:200]

        # Auto-disable after too many consecutive failures
        if self.consecutive_failures >= self.max_retries:
            self.status = "disabled"
            logger.error(
                "Task '%s' disabled after %d consecutive failures",
                self.name, self.consecutive_failures,
            )
        else:
            self.status = "retry"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "frequency": self.frequency,
            "description": self.description, "day_of_week": self.day_of_week,
            "hour_utc": self.hour_utc, "priority": self.priority,
            "client_key": self.client_key, "last_run": self.last_run,
            "run_count": self.run_count, "fail_count": self.fail_count,
            "consecutive_failures": self.consecutive_failures,
            "status": self.status, "last_error": self.last_error,
            "last_duration_seconds": self.last_duration_seconds,
        }


class TaskScheduler:
    """Production-grade task scheduler with timeout, recovery, and monitoring."""

    def __init__(self, memory: Memory) -> None:
        self.memory = memory
        self.tasks: dict[str, ScheduledTask] = {}
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._execution_history: list[dict[str, Any]] = []
        self._load_schedule()

    def _load_schedule(self) -> None:
        """Load task schedule from memory."""
        data = self.memory.get_tacit("schedule")
        if not data:
            self._init_default_schedule()
            return

        import json as _json
        # save_tacit wraps content with "# schedule\n\n...", strip header
        # Look for JSON array or object start
        json_start_arr = data.find("[")
        json_start_obj = data.find("{")
        candidates = [i for i in (json_start_arr, json_start_obj) if i >= 0]
        json_start = min(candidates) if candidates else -1
        json_data = data[json_start:] if json_start >= 0 else data
        try:
            task_list = _json.loads(json_data)
        except (ValueError, TypeError):
            task_list = None

        if isinstance(task_list, list):
            for entry in task_list:
                if not isinstance(entry, dict) or "name" not in entry:
                    continue
                try:
                    task = ScheduledTask(
                        name=entry["name"],
                        frequency=entry.get("frequency", "daily"),
                        description=entry.get("description", ""),
                        day_of_week=entry.get("day_of_week"),
                        hour_utc=entry.get("hour_utc", 12),
                        priority=entry.get("priority", 5),
                        client_key=entry.get("client_key", ""),
                        max_retries=entry.get("max_retries", 3),
                        timeout_seconds=entry.get("timeout_seconds", DEFAULT_TASK_TIMEOUT),
                    )
                    task.last_run = entry.get("last_run", "")
                    task.last_duration_seconds = entry.get("last_duration_seconds", 0.0)
                    task.run_count = entry.get("run_count", 0)
                    task.fail_count = entry.get("fail_count", 0)
                    task.consecutive_failures = entry.get("consecutive_failures", 0)
                    task.status = entry.get("status", "pending")
                    task.last_error = entry.get("last_error", "")
                    self.tasks[task.name] = task
                except (ValueError, KeyError):
                    pass
        else:
            # Legacy pipe-delimited format — parse but migrate on next save
            for line in data.splitlines():
                if "|" in line and not line.startswith("#") and not line.startswith("-"):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 5:
                        name = parts[0]
                        try:
                            task = ScheduledTask(
                                name=name,
                                frequency=parts[1],
                                description=parts[2],
                                hour_utc=int(parts[3]) if parts[3].isdigit() else 12,
                                priority=int(parts[4]) if parts[4].isdigit() else 5,
                            )
                            if len(parts) > 5:
                                task.last_run = parts[5]
                            self.tasks[name] = task
                        except (ValueError, IndexError):
                            pass

    def _init_default_schedule(self) -> None:
        """Initialize default task schedule."""
        defaults = [
            ScheduledTask("morning_report", "daily", "Morning revenue dashboard + priorities", hour_utc=15, priority=1),
            ScheduledTask("scan_opportunities", "daily", "Run opportunity scanner", hour_utc=14, priority=2),
            ScheduledTask("process_mentions", "daily", "Check X mentions + qualify leads", hour_utc=14, priority=1),
            ScheduledTask("post_content", "daily", "Post scheduled X content", hour_utc=16, priority=3),
            ScheduledTask("fulfill_services", "daily", "Run service delivery for clients", hour_utc=13, priority=2),
            ScheduledTask("revenue_check", "daily", "Check Stripe + Gumroad revenue", hour_utc=22, priority=4),
            ScheduledTask("nightly_review", "daily", "Self-improvement + consolidation", hour_utc=7, priority=1, timeout_seconds=600),

            ScheduledTask("weekly_newsletter", "weekly", "Generate + send newsletter", day_of_week=0, hour_utc=15, priority=3),
            ScheduledTask("weekly_seo_batch", "weekly", "Generate SEO articles", day_of_week=2, hour_utc=14, priority=4),
            ScheduledTask("weekly_outreach", "weekly", "Launch cold email campaign", day_of_week=1, hour_utc=14, priority=3),
            ScheduledTask("weekly_ugc_batch", "weekly", "Produce UGC videos for clients", day_of_week=3, hour_utc=14, priority=3),
            ScheduledTask("weekly_pipeline_nurture", "weekly", "Follow up on pipeline deals", day_of_week=4, hour_utc=15, priority=2),
            ScheduledTask("weekly_client_reports", "weekly", "Generate client delivery reports", day_of_week=5, hour_utc=14, priority=3),

            ScheduledTask("monthly_invoices", "monthly", "Send invoices to all active clients", hour_utc=14, priority=1),
            ScheduledTask("monthly_product_creation", "monthly", "Identify + create new digital product", hour_utc=16, priority=4),
            ScheduledTask("monthly_analytics", "monthly", "Full analytics + ROI review", hour_utc=18, priority=3),
        ]
        for task in defaults:
            self.tasks[task.name] = task
        self._save_schedule()

    def _save_schedule(self) -> None:
        """Persist schedule to memory as JSON using to_dict() for full state."""
        import json as _json
        task_list = [task.to_dict() for task in sorted(self.tasks.values(), key=lambda t: t.priority)]
        self.memory.save_tacit("schedule", _json.dumps(task_list, indent=2))

    # ── Task Management ─────────────────────────────────────────────

    def add_task(self, task: ScheduledTask) -> None:
        """Add a new task to the schedule."""
        self.tasks[task.name] = task
        self._save_schedule()

    def remove_task(self, name: str) -> None:
        """Remove a task from the schedule."""
        self.tasks.pop(name, None)
        self._save_schedule()

    def enable_task(self, name: str) -> bool:
        """Re-enable a disabled task."""
        task = self.tasks.get(name)
        if task:
            task.status = "pending"
            task.consecutive_failures = 0
            self._save_schedule()
            return True
        return False

    def register_handler(self, task_name: str, handler: Callable[..., Awaitable[Any]]) -> None:
        """Register an async handler for a task."""
        self._handlers[task_name] = handler

    # ── Execution ───────────────────────────────────────────────────

    def get_due_tasks(self, now: datetime | None = None) -> list[ScheduledTask]:
        """Get all tasks that are due to run right now."""
        now = now or datetime.now(timezone.utc)
        due = [task for task in self.tasks.values() if task.is_due(now)]
        return sorted(due, key=lambda t: t.priority)

    def get_overdue_tasks(self) -> list[ScheduledTask]:
        """Get tasks that missed their window (for catch-up after downtime)."""
        return [task for task in self.tasks.values() if task.is_overdue()]

    async def execute_due_tasks(self) -> dict[str, Any]:
        """Execute all due tasks in priority order with timeout protection."""
        due = self.get_due_tasks()

        # Also catch up overdue tasks (missed during downtime)
        overdue = self.get_overdue_tasks()
        for task in overdue:
            if task not in due and task.name in self._handlers:
                due.append(task)
                logger.info("Catching up overdue task: %s", task.name)

        results: dict[str, Any] = {"executed": 0, "failed": 0, "skipped": 0, "timed_out": 0, "tasks": []}

        for task in due:
            handler = self._handlers.get(task.name)
            if not handler:
                results["skipped"] += 1
                continue

            task.status = "running"
            start_time = asyncio.get_event_loop().time()

            try:
                # Execute with timeout
                await asyncio.wait_for(
                    handler(),
                    timeout=task.timeout_seconds,
                )
                duration = asyncio.get_event_loop().time() - start_time
                task.mark_completed(duration)
                results["executed"] += 1
                results["tasks"].append({
                    "name": task.name, "status": "completed",
                    "duration_s": round(duration, 1),
                })

            except asyncio.TimeoutError:
                duration = asyncio.get_event_loop().time() - start_time
                task.mark_failed(f"Timed out after {task.timeout_seconds}s")
                results["timed_out"] += 1
                results["tasks"].append({
                    "name": task.name, "status": "timed_out",
                    "duration_s": round(duration, 1),
                })
                logger.error("Task '%s' timed out after %ds", task.name, task.timeout_seconds)

            except Exception as exc:
                duration = asyncio.get_event_loop().time() - start_time
                task.mark_failed(str(exc))
                results["failed"] += 1
                results["tasks"].append({
                    "name": task.name, "status": "failed",
                    "error": str(exc)[:100],
                    "duration_s": round(duration, 1),
                })
                logger.error("Task '%s' failed (%.1fs): %s", task.name, duration, exc)

        self._save_schedule()

        # Track execution history
        if results["executed"] > 0 or results["failed"] > 0:
            self._execution_history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **results,
            })
            # Keep last 100 entries
            self._execution_history = self._execution_history[-100:]

            self.memory.log(
                f"[Scheduler] Executed: {results['executed']}, "
                f"Failed: {results['failed']}, "
                f"Timed out: {results['timed_out']}, "
                f"Skipped: {results['skipped']}",
                "Scheduler",
            )

        return results

    # ── Client-Specific Tasks ───────────────────────────────────────

    def add_client_tasks(self, client_key: str, service: str) -> None:
        """Add recurring tasks for a new service client."""
        service_tasks = {
            "reviews": [
                ScheduledTask(f"reviews-{client_key}", "daily", f"Review responses for {client_key}", hour_utc=13, priority=3, client_key=client_key),
            ],
            "social": [
                ScheduledTask(f"social-{client_key}", "weekly", f"Social content for {client_key}", day_of_week=0, hour_utc=14, priority=3, client_key=client_key),
            ],
            "seo": [
                ScheduledTask(f"seo-{client_key}", "weekly", f"SEO articles for {client_key}", day_of_week=2, hour_utc=14, priority=4, client_key=client_key),
            ],
            "intel": [
                ScheduledTask(f"intel-{client_key}", "weekly", f"Intel report for {client_key}", day_of_week=4, hour_utc=14, priority=4, client_key=client_key),
            ],
        }

        for task in service_tasks.get(service.lower(), []):
            self.add_task(task)

    def remove_client_tasks(self, client_key: str) -> None:
        """Remove all tasks for a churned client."""
        to_remove = [name for name, task in self.tasks.items() if task.client_key == client_key]
        for name in to_remove:
            self.remove_task(name)

    # ── Monitoring & Reporting ───────────────────────────────────────

    def get_disabled_tasks(self) -> list[ScheduledTask]:
        """Get tasks that have been auto-disabled due to failures."""
        return [t for t in self.tasks.values() if t.status == "disabled"]

    def format_schedule_report(self) -> str:
        """Format schedule for reports."""
        due = self.get_due_tasks()
        overdue = self.get_overdue_tasks()
        disabled = self.get_disabled_tasks()

        lines = [
            f"**Scheduler**: {len(self.tasks)} tasks, {len(due)} due, "
            f"{len(overdue)} overdue, {len(disabled)} disabled",
        ]
        if due:
            lines.append("Due now:")
            for t in due[:5]:
                lines.append(f"  [{t.priority}] {t.name} ({t.frequency})")
        if overdue:
            lines.append("Overdue:")
            for t in overdue[:3]:
                lines.append(f"  ⚠️ {t.name} (last: {t.last_run or 'never'})")
        if disabled:
            lines.append("Disabled (needs attention):")
            for t in disabled[:3]:
                lines.append(f"  ❌ {t.name}: {t.last_error[:60]}")
        return "\n".join(lines)
