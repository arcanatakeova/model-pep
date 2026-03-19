"""ARCANA AI — Production-grade markdown memory system.

3-layer memory with file safety:
1. Knowledge Graph (memory/life/) — PARA system. Durable facts.
2. Daily Notes (memory/daily/) — One dated file per day.
3. Tacit Knowledge (memory/tacit/) — Preferences, rules, lessons.

Production features:
- Atomic writes (write to temp file, then rename — no corruption)
- File size limits (prevent unbounded growth)
- Automatic archiving of old daily notes
- Thread-safe file access via lock
- Search with result limiting
- Memory usage tracking
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from src.config import MEMORY_DIR

logger = logging.getLogger("arcana.memory")

MAX_FILE_SIZE = 500_000        # 500KB per file
MAX_DAILY_NOTE_SIZE = 200_000  # 200KB per daily note
MAX_TACIT_SIZE = 300_000       # 300KB per tacit file
ARCHIVE_AFTER_DAYS = 30        # Archive daily notes older than 30 days


class Memory:
    """Production-grade markdown memory with atomic writes and safety limits."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = base_dir or MEMORY_DIR
        self.life = self.base / "life"
        self.daily = self.base / "daily"
        self.tacit = self.base / "tacit"
        self._lock = threading.Lock()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in [
            self.life / "projects",
            self.life / "areas",
            self.life / "resources",
            self.life / "archives",
            self.daily,
            self.tacit,
            self.tacit / "skills",
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # ── Atomic File Operations ───────────────────────────────────

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write file atomically: write to temp, then rename.

        This prevents corruption from crashes/power loss during write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".arcana_",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, str(path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _safe_read(self, path: Path) -> str:
        """Read file with error handling."""
        try:
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.error("Failed to read %s: %s", path, exc)
        return ""

    def _check_size(self, content: str, limit: int, path: Path) -> str:
        """Truncate content if it exceeds the size limit."""
        if len(content.encode("utf-8")) > limit:
            logger.warning(
                "File %s exceeds %d bytes, truncating", path.name, limit,
            )
            # Keep the header and the tail (most recent content)
            lines = content.splitlines()
            header = lines[:5]
            # Binary search for how many tail lines fit
            tail_lines: list[str] = []
            remaining = limit - len("\n".join(header).encode("utf-8")) - 100
            for line in reversed(lines[5:]):
                line_size = len(line.encode("utf-8")) + 1
                if remaining - line_size < 0:
                    break
                tail_lines.insert(0, line)
                remaining -= line_size

            content = "\n".join(header) + "\n\n[...truncated...]\n\n" + "\n".join(tail_lines)
        return content

    # ── Daily Notes ─────────────────────────────────────────────────

    def _today_path(self) -> Path:
        return self.daily / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"

    def log(self, entry: str, section: str = "Log") -> None:
        """Append an entry to today's daily note (thread-safe)."""
        with self._lock:
            path = self._today_path()
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")

            if not path.exists():
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                existing = f"# Daily Notes — {date_str}\n\n"
            else:
                existing = self._safe_read(path)

            updated = f"{existing}\n## {section} — {ts}\n{entry}\n"
            updated = self._check_size(updated, MAX_DAILY_NOTE_SIZE, path)
            self._atomic_write(path, updated)

        logger.debug("Logged to daily: %s", entry[:60])

    def get_today(self) -> str:
        """Read today's daily notes."""
        return self._safe_read(self._today_path())

    def get_daily(self, date_str: str) -> str:
        """Read a specific day's notes."""
        return self._safe_read(self.daily / f"{date_str}.md")

    def get_recent_days(self, n: int = 7) -> list[tuple[str, str]]:
        """Get the last N days of notes (newest first)."""
        files = sorted(self.daily.glob("*.md"), reverse=True)[:n]
        return [(f.stem, self._safe_read(f)) for f in files]

    def get_recent_notes(self, n: int = 7) -> str:
        """Get concatenated recent notes as a single string."""
        days = self.get_recent_days(n)
        return "\n\n---\n\n".join(content for _, content in days)

    # ── Knowledge Graph (PARA) ──────────────────────────────────────

    VALID_CATEGORIES = {"projects", "areas", "resources", "archives"}

    def _validate_category(self, category: str) -> None:
        """Validate that category is in the allowed PARA set."""
        if category not in self.VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category {category!r}. "
                f"Must be one of: {', '.join(sorted(self.VALID_CATEGORIES))}"
            )

    def save_knowledge(self, category: str, name: str, content: str) -> Path:
        """Save or update a knowledge file (atomic write)."""
        self._validate_category(category)
        slug = name.lower().replace(" ", "-").replace("/", "-")
        path = self.life / category / f"{slug}.md"

        full_content = f"# {name}\n\n{content}\n"
        full_content = self._check_size(full_content, MAX_FILE_SIZE, path)

        with self._lock:
            self._atomic_write(path, full_content)

        logger.info("Saved knowledge: %s/%s", category, slug)
        return path

    def get_knowledge(self, category: str, name: str) -> str:
        """Read a knowledge file."""
        self._validate_category(category)
        slug = name.lower().replace(" ", "-").replace("/", "-")
        return self._safe_read(self.life / category / f"{slug}.md")

    def list_knowledge(self, category: str) -> list[str]:
        """List all files in a PARA category."""
        path = self.life / category
        if path.exists():
            return sorted(f.stem for f in path.glob("*.md"))
        return []

    def delete_knowledge(self, category: str, name: str) -> bool:
        """Delete a knowledge file."""
        self._validate_category(category)
        slug = name.lower().replace(" ", "-").replace("/", "-")
        path = self.life / category / f"{slug}.md"
        if path.exists():
            path.unlink()
            logger.info("Deleted knowledge: %s/%s", category, slug)
            return True
        return False

    # ── Tacit Knowledge ─────────────────────────────────────────────

    def save_tacit(self, name: str, content: str) -> Path:
        """Save a tacit knowledge file (atomic write)."""
        slug = name.lower().replace(" ", "-")
        path = self.tacit / f"{slug}.md"

        full_content = f"# {name}\n\n{content}\n"
        full_content = self._check_size(full_content, MAX_TACIT_SIZE, path)

        with self._lock:
            self._atomic_write(path, full_content)

        logger.info("Saved tacit: %s", slug)
        return path

    def get_tacit(self, name: str) -> str:
        slug = name.lower().replace(" ", "-")
        return self._safe_read(self.tacit / f"{slug}.md")

    def get_all_tacit(self) -> str:
        """Load all tacit knowledge as a single string."""
        files = sorted(self.tacit.glob("*.md"))
        parts = []
        for f in files:
            content = self._safe_read(f)
            if content:
                parts.append(content)
        return "\n\n---\n\n".join(parts) if parts else ""

    # ── Search ──────────────────────────────────────────────────────

    def search(self, query: str, scope: str = "all", limit: int = 20) -> list[tuple[str, str]]:
        """Keyword search across memory files. Returns (path, matching_line) tuples."""
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
                content = self._safe_read(f)
                for line in content.splitlines():
                    if query_lower in line.lower():
                        results.append((str(f.relative_to(self.base)), line.strip()))
                        break  # One match per file

                if len(results) >= limit:
                    return results

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

    # ── Archiving ───────────────────────────────────────────────────

    def archive_old_notes(self) -> int:
        """Move daily notes older than ARCHIVE_AFTER_DAYS to archives."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AFTER_DAYS)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        archived = 0

        archive_dir = self.life / "archives" / "daily"
        archive_dir.mkdir(parents=True, exist_ok=True)

        for f in self.daily.glob("*.md"):
            if f.stem < cutoff_str:
                dest = archive_dir / f.name
                f.rename(dest)
                archived += 1

        if archived:
            logger.info("Archived %d daily notes older than %s", archived, cutoff_str)
        return archived

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get memory system stats."""
        def count_files(d: Path) -> int:
            return len(list(d.glob("*.md"))) if d.exists() else 0

        def total_size(d: Path) -> int:
            return sum(f.stat().st_size for f in d.rglob("*.md")) if d.exists() else 0

        return {
            "daily_notes": count_files(self.daily),
            "projects": count_files(self.life / "projects"),
            "areas": count_files(self.life / "areas"),
            "resources": count_files(self.life / "resources"),
            "tacit_files": count_files(self.tacit),
            "skills": count_files(self.tacit / "skills"),
            "total_size_mb": round(total_size(self.base) / 1_000_000, 2),
        }
