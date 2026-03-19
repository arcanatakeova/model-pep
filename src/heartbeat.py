"""ARCANA AI — Heartbeat: system health and progress tracking.

Production features:
- Intra-day progress against priorities
- System health metrics (uptime, error rate, last cycle time)
- Component status tracking
- Stale heartbeat detection (watchdog)
- Atomic file writes
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import HEARTBEAT_PATH

logger = logging.getLogger("arcana.heartbeat")


class Heartbeat:
    """Track system health and intra-day progress."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or HEARTBEAT_PATH
        self._start_time = time.monotonic()
        self._last_update = 0.0
        self._error_count = 0
        self._cycle_count = 0
        self._component_status: dict[str, str] = {}

    def update(
        self, status: str, current_task: str,
        completed: list[str] | None = None,
        upcoming: list[str] | None = None,
    ) -> None:
        """Update the heartbeat file with current status."""
        self._last_update = time.monotonic()
        self._cycle_count += 1
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        uptime_hours = (time.monotonic() - self._start_time) / 3600

        content = (
            f"# ARCANA AI — Heartbeat\n"
            f"**Last updated:** {ts}\n"
            f"**Status:** {status}\n"
            f"**Current task:** {current_task}\n"
            f"**Uptime:** {uptime_hours:.1f}h | "
            f"**Cycles:** {self._cycle_count} | "
            f"**Errors:** {self._error_count}\n\n"
        )

        if self._component_status:
            content += "## Component Status\n"
            for comp, st in sorted(self._component_status.items()):
                icon = "✅" if st == "ok" else "⚠️" if st == "degraded" else "❌"
                content += f"- {icon} {comp}: {st}\n"
            content += "\n"

        if completed:
            content += "## Completed Today\n"
            content += "\n".join(f"- ✅ {item}" for item in completed)
            content += "\n\n"

        if upcoming:
            content += "## Up Next\n"
            content += "\n".join(f"- ⬜ {item}" for item in upcoming)
            content += "\n"

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            import tempfile, os
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, str(self.path))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.error("Failed to write heartbeat to %s: %s", self.path, exc)

    def record_error(self) -> None:
        """Increment error counter."""
        self._error_count += 1

    def set_component_status(self, component: str, status: str) -> None:
        """Track a component's health status."""
        self._component_status[component] = status

    def get(self) -> str:
        """Read current heartbeat."""
        if self.path.exists():
            return self.path.read_text(encoding="utf-8")
        return "No heartbeat yet."

    def is_stale(self, max_seconds: int = 1800) -> bool:
        """Check if heartbeat is stale (no update in max_seconds)."""
        if self._last_update == 0:
            return False  # Not stale on startup — haven't started yet
        return (time.monotonic() - self._last_update) > max_seconds

    def get_health(self) -> dict[str, Any]:
        """Get system health metrics."""
        return {
            "uptime_hours": round((time.monotonic() - self._start_time) / 3600, 2),
            "cycles": self._cycle_count,
            "errors": self._error_count,
            "stale": self.is_stale(),
            "components": dict(self._component_status),
        }

    def clear(self) -> None:
        """Clear heartbeat at end of day."""
        if self.path.exists():
            self.path.unlink()
