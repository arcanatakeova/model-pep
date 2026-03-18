"""ARCANA AI — Heartbeat: intra-day progress tracking.

Writes HEARTBEAT.md with current status, active task, and progress against
today's priorities. Updated after every major action.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.config import HEARTBEAT_PATH


class Heartbeat:
    """Track intra-day progress in HEARTBEAT.md."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or HEARTBEAT_PATH

    def update(self, status: str, current_task: str, completed: list[str] | None = None, upcoming: list[str] | None = None) -> None:
        """Update the heartbeat file."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        content = (
            f"# ARCANA AI — Heartbeat\n"
            f"**Last updated:** {ts}\n"
            f"**Status:** {status}\n"
            f"**Current task:** {current_task}\n\n"
        )

        if completed:
            content += "## Completed Today\n"
            content += "\n".join(f"- ✅ {item}" for item in completed)
            content += "\n\n"

        if upcoming:
            content += "## Up Next\n"
            content += "\n".join(f"- ⬜ {item}" for item in upcoming)
            content += "\n"

        self.path.write_text(content)

    def get(self) -> str:
        """Read current heartbeat."""
        if self.path.exists():
            return self.path.read_text()
        return "No heartbeat yet."

    def clear(self) -> None:
        """Clear heartbeat at end of day."""
        if self.path.exists():
            self.path.unlink()
