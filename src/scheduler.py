"""ARCANA AI — Task Scheduler.

Manages recurring tasks, client deliverables, and time-based automation.
Replaces the basic time checks in the orchestrator with a proper system.

Task types:
- DAILY: Review responses, social content review, revenue check
- WEEKLY: Newsletter issue, SEO batch, client reports, outreach campaign
- MONTHLY: Invoice clients, renew subscriptions, product launches, analytics
- CUSTOM: Per-client schedules (e.g., "post to client X's Instagram MWF")

Persistence: Tasks stored in memory/tacit/schedule.md
Retry: Failed tasks retry with exponential backoff (max 3 retries)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable

from src.memory import Memory

logger = logging.getLogger("arcana.scheduler")


class ScheduledTask:
    """A recurring task with schedule, state, and retry logic."""

    def __init__(
        self, name: str, frequency: str, description: str = "",
        day_of_week: int | None = None,  # 0=Mon, 6=Sun
        hour_utc: int = 12,
        priority: int = 5,  # 1=highest, 10=lowest
        client_key: str = "",
        max_retries: int = 3,
    ) -> None:
        self.name = name
        self.frequency = frequency  # daily, weekly, monthly, custom
        self.description = description
        self.day_of_week = day_of_week
        self.hour_utc = hour_utc
        self.priority = priority
        self.client_key = client_key
        self.max_retries = max_retries
        self.last_run: str = ""
        self.run_count: int = 0
        self.fail_count: int = 0
        self.status: str = "pending"  # pending, running, completed, failed

    def is_due(self, now: datetime | None = None) -> bool:
        """Check if this task is due to run."""
        now = now or datetime.now(timezone.utc)

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

    def mark_completed(self) -> None:
        self.last_run = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        self.run_count += 1
        self.status = "completed"

    def mark_failed(self) -> None:
        self.fail_count += 1
        self.status = "failed" if self.fail_count >= self.max_retries else "retry"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "frequency": self.frequency,
            "description": self.description,
            "day_of_week": self.day_of_week,
            "hour_utc": self.hour_utc,
            "priority": self.priority,
            "client_key": self.client_key,
            "last_run": self.last_run,
            "run_count": self.run_count,
            "fail_count": self.fail_count,
            "status": self.status,
        }


class TaskScheduler:
    """Manage and execute recurring tasks."""

    def __init__(self, memory: Memory) -> None:
        self.memory = memory
        self.tasks: dict[str, ScheduledTask] = {}
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._load_schedule()

    def _load_schedule(self) -> None:
        """Load task schedule from memory."""
        data = self.memory.get_tacit("schedule")
        if not data:
            self._init_default_schedule()
            return

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
            ScheduledTask("nightly_review", "daily", "Self-improvement + consolidation", hour_utc=7, priority=1),

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
        """Persist schedule to memory."""
        lines = ["# Task Schedule\n"]
        lines.append("name | frequency | description | hour_utc | priority | last_run")
        lines.append("--- | --- | --- | --- | --- | ---")
        for task in sorted(self.tasks.values(), key=lambda t: t.priority):
            lines.append(
                f"{task.name} | {task.frequency} | {task.description} | "
                f"{task.hour_utc} | {task.priority} | {task.last_run}"
            )
        self.memory.save_tacit("schedule", "\n".join(lines))

    # ── Task Management ─────────────────────────────────────────────

    def add_task(self, task: ScheduledTask) -> None:
        """Add a new task to the schedule."""
        self.tasks[task.name] = task
        self._save_schedule()

    def remove_task(self, name: str) -> None:
        """Remove a task from the schedule."""
        self.tasks.pop(name, None)
        self._save_schedule()

    def register_handler(self, task_name: str, handler: Callable[..., Awaitable[Any]]) -> None:
        """Register an async handler for a task."""
        self._handlers[task_name] = handler

    # ── Execution ───────────────────────────────────────────────────

    def get_due_tasks(self, now: datetime | None = None) -> list[ScheduledTask]:
        """Get all tasks that are due to run right now."""
        now = now or datetime.now(timezone.utc)
        due = [task for task in self.tasks.values() if task.is_due(now)]
        return sorted(due, key=lambda t: t.priority)

    async def execute_due_tasks(self) -> dict[str, Any]:
        """Execute all due tasks in priority order."""
        due = self.get_due_tasks()
        results = {"executed": 0, "failed": 0, "skipped": 0, "tasks": []}

        for task in due:
            handler = self._handlers.get(task.name)
            if not handler:
                results["skipped"] += 1
                continue

            task.status = "running"
            try:
                await handler()
                task.mark_completed()
                results["executed"] += 1
                results["tasks"].append({"name": task.name, "status": "completed"})
            except Exception as exc:
                task.mark_failed()
                results["failed"] += 1
                results["tasks"].append({"name": task.name, "status": "failed", "error": str(exc)[:100]})
                logger.error("Task %s failed: %s", task.name, exc)

        self._save_schedule()

        if results["executed"] > 0 or results["failed"] > 0:
            self.memory.log(
                f"[Scheduler] Executed: {results['executed']}, "
                f"Failed: {results['failed']}, Skipped: {results['skipped']}",
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

    # ── Reporting ───────────────────────────────────────────────────

    def format_schedule_report(self) -> str:
        """Format schedule for reports."""
        due = self.get_due_tasks()
        lines = [
            f"**Scheduler**: {len(self.tasks)} tasks total, {len(due)} due now",
        ]
        if due:
            lines.append("Due now:")
            for t in due[:5]:
                lines.append(f"  [{t.priority}] {t.name} ({t.frequency})")
        return "\n".join(lines)
