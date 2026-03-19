"""ARCANA AI — Markdown-based memory system.

Inspired by Felix Craft's 3-layer memory:
1. Knowledge Graph (memory/life/) — PARA system. Durable facts about people, clients, products.
2. Daily Notes (memory/daily/) — One dated file per day. What happened, what was decided.
3. Tacit Knowledge (memory/tacit/) — Preferences, habits, hard rules, lessons learned.

Everything is a plain markdown file — transparent, editable, version-controllable with Git.
No databases, no vector embeddings. Just files.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import MEMORY_DIR

logger = logging.getLogger("arcana.memory")


class Memory:
    """Read, write, search, and consolidate markdown memory files."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = base_dir or MEMORY_DIR
        self.life = self.base / "life"
        self.daily = self.base / "daily"
        self.tacit = self.base / "tacit"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in [
            self.life / "projects",
            self.life / "areas",
            self.life / "resources",
            self.life / "archives",
            self.daily,
            self.tacit,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # ── Daily Notes ─────────────────────────────────────────────────

    def _today_path(self) -> Path:
        return self.daily / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"

    def log(self, entry: str, section: str = "Log") -> None:
        """Append an entry to today's daily note."""
        path = self._today_path()
        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")

        if not path.exists():
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path.write_text(f"# Daily Notes — {date_str}\n\n")

        with open(path, "a") as f:
            f.write(f"\n## {section} — {ts}\n{entry}\n")

        logger.debug("Logged to daily: %s", entry[:60])

    def get_today(self) -> str:
        """Read today's daily notes."""
        path = self._today_path()
        if path.exists():
            return path.read_text()
        return ""

    def get_daily(self, date_str: str) -> str:
        """Read a specific day's notes."""
        path = self.daily / f"{date_str}.md"
        if path.exists():
            return path.read_text()
        return ""

    def get_recent_days(self, n: int = 7) -> list[tuple[str, str]]:
        """Get the last N days of notes (newest first)."""
        files = sorted(self.daily.glob("*.md"), reverse=True)[:n]
        return [(f.stem, f.read_text()) for f in files]

    # ── Knowledge Graph (PARA) ──────────────────────────────────────

    def save_knowledge(self, category: str, name: str, content: str) -> Path:
        """Save or update a knowledge file. Category: projects/areas/resources/archives."""
        slug = name.lower().replace(" ", "-").replace("/", "-")
        path = self.life / category / f"{slug}.md"
        path.write_text(f"# {name}\n\n{content}\n")
        logger.info("Saved knowledge: %s/%s", category, slug)
        return path

    def get_knowledge(self, category: str, name: str) -> str:
        """Read a knowledge file."""
        slug = name.lower().replace(" ", "-").replace("/", "-")
        path = self.life / category / f"{slug}.md"
        if path.exists():
            return path.read_text()
        return ""

    def list_knowledge(self, category: str) -> list[str]:
        """List all files in a PARA category."""
        path = self.life / category
        if path.exists():
            return [f.stem for f in path.glob("*.md")]
        return []

    # ── Tacit Knowledge ─────────────────────────────────────────────

    def save_tacit(self, name: str, content: str) -> Path:
        """Save a tacit knowledge file (preferences, rules, lessons)."""
        slug = name.lower().replace(" ", "-")
        path = self.tacit / f"{slug}.md"
        path.write_text(f"# {name}\n\n{content}\n")
        logger.info("Saved tacit: %s", slug)
        return path

    def get_tacit(self, name: str) -> str:
        slug = name.lower().replace(" ", "-")
        path = self.tacit / f"{slug}.md"
        if path.exists():
            return path.read_text()
        return ""

    def get_all_tacit(self) -> str:
        """Load all tacit knowledge as a single string."""
        files = sorted(self.tacit.glob("*.md"))
        return "\n\n---\n\n".join(f.read_text() for f in files) if files else ""

    # ── Search ──────────────────────────────────────────────────────

    def search(self, query: str, scope: str = "all") -> list[tuple[str, str]]:
        """Simple keyword search across memory files. Returns (path, matching_line) tuples."""
        query_lower = query.lower()
        results: list[tuple[str, str]] = []

        dirs = []
        if scope in ("all", "daily"):
            dirs.append(self.daily)
        if scope in ("all", "life"):
            dirs.extend([
                self.life / "projects",
                self.life / "areas",
                self.life / "resources",
                self.life / "archives",
            ])
        if scope in ("all", "tacit"):
            dirs.append(self.tacit)

        for d in dirs:
            if not d.exists():
                continue
            for f in d.glob("*.md"):
                content = f.read_text()
                for line in content.splitlines():
                    if query_lower in line.lower():
                        results.append((str(f.relative_to(self.base)), line.strip()))
                        break  # One match per file

        return results

    # ── Nightly Consolidation ───────────────────────────────────────

    def get_consolidation_context(self) -> str:
        """Get today's notes for the nightly consolidation review."""
        today = self.get_today()
        tacit = self.get_all_tacit()
        projects = self.list_knowledge("projects")
        areas = self.list_knowledge("areas")

        return (
            f"## Today's Daily Notes\n{today}\n\n"
            f"## Current Projects\n{', '.join(projects) or 'None'}\n\n"
            f"## Active Areas\n{', '.join(areas) or 'None'}\n\n"
            f"## Tacit Knowledge\n{tacit[:2000]}\n"
        )
