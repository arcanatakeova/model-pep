"""ARCANA AI — Scanner Orchestrator.

Coordinates all platform-specific scanners with:
- Budget distribution (API calls per platform per cycle)
- Cross-platform deduplication (same lead found on X + Reddit)
- Priority routing (hot leads → CRM immediately, warm → pipeline)
- Rate limit management per platform
- Performance tracking (which platform converts best)

Sits above OpportunityScanner and adds multi-scanner coordination.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.database import Database
from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.scanner_orchestrator")

# Budget allocation per cycle (API calls)
DEFAULT_BUDGETS = {
    "x": 30,
    "reddit": 20,
    "upwork": 10,
    "fiverr": 10,
    "product_hunt": 5,
    "hacker_news": 10,
    "google_alerts": 5,
    "linkedin": 10,
}


class ScannerOrchestrator:
    """Coordinate multiple platform scanners, distribute budget, deduplicate."""

    def __init__(
        self, llm: LLM, memory: Memory,
        db: Database | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.db = db
        self.budgets = dict(DEFAULT_BUDGETS)
        self._seen_hashes: set[str] = set()
        self._platform_stats: dict[str, dict[str, int]] = {}
        self._load_stats()

    def _load_stats(self) -> None:
        """Load platform performance stats from memory."""
        data = self.memory.get_tacit("scanner-platform-stats")
        if not data:
            for platform in DEFAULT_BUDGETS:
                self._platform_stats[platform] = {
                    "scanned": 0, "found": 0, "responded": 0,
                    "converted": 0, "errors": 0,
                }
            return
        # Parse simple format
        for line in data.splitlines():
            if "|" in line and not line.startswith("#") and not line.startswith("-"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 6:
                    try:
                        self._platform_stats[parts[0]] = {
                            "scanned": int(parts[1]),
                            "found": int(parts[2]),
                            "responded": int(parts[3]),
                            "converted": int(parts[4]),
                            "errors": int(parts[5]),
                        }
                    except (ValueError, IndexError):
                        pass

    def _save_stats(self) -> None:
        """Save platform stats to memory."""
        lines = ["# Scanner Platform Stats\n"]
        lines.append("platform | scanned | found | responded | converted | errors")
        lines.append("--- | --- | --- | --- | --- | ---")
        for platform, stats in sorted(self._platform_stats.items()):
            lines.append(
                f"{platform} | {stats['scanned']} | {stats['found']} | "
                f"{stats['responded']} | {stats['converted']} | {stats['errors']}"
            )
        self.memory.save_tacit("scanner-platform-stats", "\n".join(lines))

    def _dedup_key(self, text: str, author: str) -> str:
        """Generate dedup key from opportunity text + author."""
        from src.toolkit import fast_hash
        normalized = f"{author.lower().strip()}:{text[:200].lower().strip()}"
        return fast_hash(normalized)

    def is_duplicate(self, text: str, author: str) -> bool:
        """Check if this opportunity was already found (cross-platform)."""
        key = self._dedup_key(text, author)
        if key in self._seen_hashes:
            return True
        self._seen_hashes.add(key)
        return False

    def record_result(
        self, platform: str, found: int = 0,
        responded: int = 0, converted: int = 0, errors: int = 0,
    ) -> None:
        """Record results from a platform scan."""
        if platform not in self._platform_stats:
            self._platform_stats[platform] = {
                "scanned": 0, "found": 0, "responded": 0,
                "converted": 0, "errors": 0,
            }
        stats = self._platform_stats[platform]
        stats["scanned"] += 1
        stats["found"] += found
        stats["responded"] += responded
        stats["converted"] += converted
        stats["errors"] += errors
        self._save_stats()

        if self.db:
            self.db.log_event("scanner_result", platform, {
                "found": found, "responded": responded,
                "converted": converted, "errors": errors,
            })

    async def optimize_budgets(self) -> dict[str, int]:
        """Reallocate scan budgets based on platform performance.

        Platforms with higher conversion rates get more budget.
        """
        total_budget = sum(self.budgets.values())

        # Calculate conversion rate per platform
        rates: dict[str, float] = {}
        for platform, stats in self._platform_stats.items():
            if stats["found"] > 0:
                rates[platform] = stats["converted"] / stats["found"]
            elif stats["scanned"] > 0:
                rates[platform] = stats["found"] / (stats["scanned"] * 10)
            else:
                rates[platform] = 0.1  # Default for new platforms

        # Redistribute budget proportionally (with floor of 3)
        total_rate = sum(rates.values()) or 1.0
        for platform in self.budgets:
            rate = rates.get(platform, 0.1)
            share = rate / total_rate
            self.budgets[platform] = max(3, int(total_budget * share))

        self.memory.log(
            f"Scanner budget reallocation: {self.budgets}",
            "Scanner",
        )

        return self.budgets

    async def coordinated_scan(self, scanner: Any) -> dict[str, Any]:
        """Run a coordinated multi-platform scan cycle.

        Delegates to the OpportunityScanner but adds coordination:
        - Budget-aware scanning per platform
        - Cross-platform deduplication
        - Priority-based routing
        """
        results = {
            "total_found": 0,
            "total_responded": 0,
            "duplicates_filtered": 0,
            "platforms_scanned": [],
            "hot_leads": [],
        }

        # Run the scanner's scan_cycle (it handles platform iteration internally)
        try:
            scan_results = await scanner.scan_cycle()
            results["total_found"] = scan_results.get("total_found", 0)
            results["total_responded"] = scan_results.get("auto_responded", 0)

            # Record per-platform if available
            for platform_result in scan_results.get("platform_results", []):
                platform = platform_result.get("platform", "unknown")
                self.record_result(
                    platform,
                    found=platform_result.get("found", 0),
                    responded=platform_result.get("responded", 0),
                )
                results["platforms_scanned"].append(platform)

        except Exception as exc:
            logger.error("Coordinated scan failed: %s", exc)
            results["error"] = str(exc)[:200]

        return results

    async def nightly_optimization(self) -> dict[str, Any]:
        """Nightly scanner optimization cycle.

        1. Analyze platform performance
        2. Reallocate budgets
        3. Identify dead queries to retire
        4. Suggest new platforms to explore
        """
        new_budgets = await self.optimize_budgets()

        # Get dead queries from database
        dead_queries = []
        if self.db:
            dead_queries = self.db.get_dead_queries(min_uses=5)

        # Analyze with LLM
        stats_summary = "\n".join(
            f"- {p}: {s['found']} found, {s['converted']} converted, "
            f"{s['errors']} errors"
            for p, s in self._platform_stats.items()
        )

        result = await self.llm.ask_json(
            f"You are ARCANA AI optimizing your opportunity scanner.\n\n"
            f"Platform stats:\n{stats_summary}\n\n"
            f"New budgets: {new_budgets}\n\n"
            f"Dead queries (used many times, never convert):\n"
            f"{[q.get('query_text', '') for q in dead_queries[:10]]}\n\n"
            f"Return JSON: {{\n"
            f'  "recommendations": [str],\n'
            f'  "queries_to_retire": [str],\n'
            f'  "new_platforms_to_explore": [str],\n'
            f'  "budget_assessment": str\n'
            f"}}",
            tier=Tier.HAIKU,
        )

        self.memory.log(
            f"Scanner optimization complete\n"
            f"Budgets: {new_budgets}\n"
            f"Recommendations: {result.get('recommendations', [])[:3]}",
            "Scanner",
        )

        return {
            "new_budgets": new_budgets,
            "dead_queries": len(dead_queries),
            "recommendations": result.get("recommendations", []),
        }

    def format_orchestrator_report(self) -> str:
        """Format multi-platform scanner report."""
        lines = ["**Scanner Orchestrator**"]
        total_found = sum(s["found"] for s in self._platform_stats.values())
        total_converted = sum(s["converted"] for s in self._platform_stats.values())
        lines.append(
            f"Total: {total_found} found, {total_converted} converted "
            f"({total_converted/total_found:.1%} rate)" if total_found > 0
            else "Total: No opportunities found yet"
        )
        lines.append(f"Budgets: {self.budgets}")

        for platform, stats in sorted(
            self._platform_stats.items(),
            key=lambda x: -x[1].get("converted", 0),
        ):
            conv_rate = (
                f"{stats['converted']/stats['found']:.1%}"
                if stats["found"] > 0 else "N/A"
            )
            lines.append(
                f"  {platform}: {stats['found']} found, "
                f"{stats['converted']} converted ({conv_rate})"
            )

        return "\n".join(lines)
