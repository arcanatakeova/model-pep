"""ARCANA AI — API Evolver.

Hourly API discovery, testing, and live integration engine.

The system:
1. DISCOVER — Scans for new APIs, tools, and services that could boost revenue
2. EVALUATE — Tests APIs in sandbox, measures usefulness, checks pricing
3. INTEGRATE — Generates wrapper code and wires into live ARCANA systems
4. MONITOR — Tracks API health, rate limits, costs, and ROI
5. EVOLVE — Swaps underperforming APIs for better alternatives

This is how ARCANA stays on the cutting edge — always pulling in new capabilities.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.api_evolver")

# Categories of APIs ARCANA hunts for
API_CATEGORIES = {
    "lead_generation": [
        "prospecting APIs", "email finder APIs", "company data APIs",
        "social listening APIs", "intent data APIs",
    ],
    "content_creation": [
        "AI writing APIs", "image generation APIs", "video generation APIs",
        "voice synthesis APIs", "social media scheduling APIs",
    ],
    "payment_processing": [
        "payment gateway APIs", "invoicing APIs", "subscription billing APIs",
        "crypto payment APIs",
    ],
    "communication": [
        "email delivery APIs", "SMS APIs", "chat APIs",
        "push notification APIs", "webhook relay APIs",
    ],
    "analytics": [
        "web analytics APIs", "social analytics APIs", "revenue analytics APIs",
        "SEO APIs", "competitor analysis APIs",
    ],
    "automation": [
        "workflow automation APIs", "scraping APIs", "browser automation APIs",
        "scheduling APIs", "task management APIs",
    ],
    "ai_ml": [
        "LLM APIs", "embedding APIs", "classification APIs",
        "sentiment analysis APIs", "OCR APIs",
    ],
}


class APIEndpoint:
    """Represents a discovered API endpoint."""

    def __init__(
        self, name: str, category: str, base_url: str,
        description: str = "", pricing: str = "",
        auth_type: str = "api_key",
    ) -> None:
        self.name = name
        self.category = category
        self.base_url = base_url
        self.description = description
        self.pricing = pricing
        self.auth_type = auth_type
        self.status = "discovered"  # discovered, testing, integrated, retired
        self.health_score = 1.0  # 0-1
        self.call_count = 0
        self.error_count = 0
        self.avg_latency_ms = 0.0
        self.monthly_cost = 0.0
        self.revenue_attributed = 0.0
        self.discovered_at = datetime.now(timezone.utc).isoformat()
        self.last_checked = ""
        self.wrapper_module: str | None = None

    @property
    def roi(self) -> float:
        return self.revenue_attributed / self.monthly_cost if self.monthly_cost > 0 else 0.0

    @property
    def error_rate(self) -> float:
        return self.error_count / self.call_count if self.call_count > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "category": self.category,
            "base_url": self.base_url, "description": self.description,
            "pricing": self.pricing, "auth_type": self.auth_type,
            "status": self.status, "health_score": self.health_score,
            "call_count": self.call_count, "error_count": self.error_count,
            "avg_latency_ms": self.avg_latency_ms,
            "monthly_cost": self.monthly_cost,
            "revenue_attributed": self.revenue_attributed,
            "discovered_at": self.discovered_at,
            "last_checked": self.last_checked,
            "wrapper_module": self.wrapper_module,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "APIEndpoint":
        ep = cls(
            data["name"], data["category"], data["base_url"],
            data.get("description", ""), data.get("pricing", ""),
            data.get("auth_type", "api_key"),
        )
        ep.status = data.get("status", "discovered")
        ep.health_score = data.get("health_score", 1.0)
        ep.call_count = data.get("call_count", 0)
        ep.error_count = data.get("error_count", 0)
        ep.avg_latency_ms = data.get("avg_latency_ms", 0.0)
        ep.monthly_cost = data.get("monthly_cost", 0.0)
        ep.revenue_attributed = data.get("revenue_attributed", 0.0)
        ep.discovered_at = data.get("discovered_at", "")
        ep.last_checked = data.get("last_checked", "")
        ep.wrapper_module = data.get("wrapper_module")
        return ep


class APIEvolver:
    """Hourly API discovery, testing, and live integration engine."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory
        self.apis: dict[str, APIEndpoint] = {}
        self.integrations_dir = Path("src/integrations")
        self.integrations_dir.mkdir(parents=True, exist_ok=True)
        # Ensure __init__.py exists
        init_path = self.integrations_dir / "__init__.py"
        if not init_path.exists():
            init_path.write_text('"""Auto-generated API integrations."""\n')
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._load_apis()

    def _load_apis(self) -> None:
        """Load known APIs from memory."""
        data = self.memory.get_tacit("api-registry")
        if not data:
            return
        try:
            # Skip the markdown header
            json_start = data.find("{")
            if json_start < 0:
                json_start = data.find("[")
            if json_start >= 0:
                parsed = json.loads(data[json_start:])
                if isinstance(parsed, dict):
                    for name, api_data in parsed.items():
                        self.apis[name] = APIEndpoint.from_dict(api_data)
        except (json.JSONDecodeError, TypeError):
            pass

    def _save_apis(self) -> None:
        """Persist API registry."""
        data = {name: api.to_dict() for name, api in self.apis.items()}
        self.memory.save_tacit("api-registry", json.dumps(data, indent=2))

    # ── DISCOVER ───────────────────────────────────────────────────

    async def discover_apis(self, focus_categories: list[str] | None = None) -> dict[str, Any]:
        """Discover new APIs that could benefit ARCANA's operations.

        Uses LLM to identify APIs based on current needs and gaps.
        """
        categories = focus_categories or list(API_CATEGORIES.keys())

        # What APIs do we already have?
        existing = [
            f"{api.name} ({api.category})" for api in self.apis.values()
            if api.status != "retired"
        ]

        # What are our current bottlenecks?
        bottlenecks = self.memory.get_tacit("bottlenecks") or ""
        lessons = self.memory.get_tacit("lessons-learned") or ""

        # Focus on categories relevant to bottlenecks
        category_queries = []
        for cat in categories[:4]:
            queries = API_CATEGORIES.get(cat, [])
            category_queries.extend(queries[:3])

        result = await self.llm.ask_json(
            f"You are ARCANA AI discovering new APIs to integrate.\n\n"
            f"Current bottlenecks:\n{bottlenecks[-1000:]}\n\n"
            f"Recent lessons:\n{lessons[-800:]}\n\n"
            f"APIs already integrated:\n{', '.join(existing) or 'None yet'}\n\n"
            f"Focus categories: {', '.join(categories)}\n"
            f"Search terms: {', '.join(category_queries)}\n\n"
            f"Discover 3-5 specific, REAL APIs that would help ARCANA.\n"
            f"Only suggest APIs that actually exist and have public documentation.\n\n"
            f"Return JSON: {{\n"
            f'  "discoveries": [\n'
            f"    {{\n"
            f'      "name": str (API name),\n'
            f'      "category": str (one of: {", ".join(categories)}),\n'
            f'      "base_url": str (API base URL),\n'
            f'      "description": str (what it does, why ARCANA needs it),\n'
            f'      "pricing": str (free tier / pricing info),\n'
            f'      "auth_type": "api_key"|"oauth"|"bearer"|"none",\n'
            f'      "integration_value": str (specific use case for ARCANA),\n'
            f'      "priority": int (1=critical, 5=nice-to-have)\n'
            f"    }}\n"
            f"  ]\n"
            f"}}",
            tier=Tier.SONNET,
        )

        discoveries = result.get("discoveries", [])
        new_apis = []
        for d in discoveries:
            name = d.get("name", "")
            if not name or name in self.apis:
                continue
            api = APIEndpoint(
                name=name,
                category=d.get("category", "automation"),
                base_url=d.get("base_url", ""),
                description=d.get("description", ""),
                pricing=d.get("pricing", ""),
                auth_type=d.get("auth_type", "api_key"),
            )
            self.apis[name] = api
            new_apis.append(name)

        self._save_apis()

        if new_apis:
            self.memory.log(
                f"Discovered {len(new_apis)} new APIs: {', '.join(new_apis)}",
                "API Evolution",
            )

        return {
            "discovered": len(new_apis),
            "apis": [d for d in discoveries if d.get("name") in new_apis],
        }

    # ── EVALUATE ───────────────────────────────────────────────────

    async def evaluate_api(self, api_name: str) -> dict[str, Any]:
        """Test an API endpoint — check health, measure latency, verify docs."""
        api = self.apis.get(api_name)
        if not api:
            return {"error": f"API not found: {api_name}"}

        # Health check
        health_result = {"reachable": False, "latency_ms": 0, "status_code": 0}
        try:
            start = datetime.now(timezone.utc)
            resp = await self._http.get(
                api.base_url,
                headers={"User-Agent": "ARCANA-AI/1.0"},
                follow_redirects=True,
            )
            latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            health_result = {
                "reachable": resp.status_code < 500,
                "latency_ms": round(latency, 1),
                "status_code": resp.status_code,
            }
        except Exception as exc:
            health_result["error"] = str(exc)[:100]

        api.avg_latency_ms = health_result.get("latency_ms", 0)
        api.last_checked = datetime.now(timezone.utc).isoformat()

        if health_result.get("reachable"):
            api.health_score = min(1.0, api.health_score + 0.1)
            api.status = "testing"
        else:
            api.health_score = max(0.0, api.health_score - 0.3)

        self._save_apis()

        # Generate integration assessment
        assessment = await self.llm.ask_json(
            f"You are ARCANA AI evaluating an API for integration.\n\n"
            f"API: {api.name}\n"
            f"Category: {api.category}\n"
            f"URL: {api.base_url}\n"
            f"Description: {api.description}\n"
            f"Pricing: {api.pricing}\n"
            f"Health check: {json.dumps(health_result)}\n\n"
            f"Evaluate this API's value to ARCANA's revenue operations.\n"
            f"Return JSON: {{\n"
            f'  "integration_score": int (1-10, how valuable),\n'
            f'  "complexity": "low"|"medium"|"high",\n'
            f'  "estimated_monthly_cost": float,\n'
            f'  "potential_revenue_impact": str,\n'
            f'  "integration_steps": [str],\n'
            f'  "risks": [str],\n'
            f'  "recommendation": "integrate"|"monitor"|"skip"\n'
            f"}}",
            tier=Tier.HAIKU,
        )

        return {
            "api_name": api_name,
            "health": health_result,
            "assessment": assessment,
        }

    # ── INTEGRATE ──────────────────────────────────────────────────

    async def generate_wrapper(self, api_name: str) -> dict[str, Any]:
        """Generate a Python wrapper module for an API and save to src/integrations/.

        The wrapper is a self-contained async client that ARCANA can import and use.
        """
        api = self.apis.get(api_name)
        if not api:
            return {"error": f"API not found: {api_name}"}

        # Generate wrapper code via LLM
        result = await self.llm.ask_json(
            f"You are ARCANA AI generating a Python API wrapper.\n\n"
            f"API: {api.name}\n"
            f"Base URL: {api.base_url}\n"
            f"Auth type: {api.auth_type}\n"
            f"Description: {api.description}\n\n"
            f"Generate a production-ready async Python wrapper class.\n"
            f"Requirements:\n"
            f"- Use httpx.AsyncClient\n"
            f"- Include retry logic (3 attempts, exponential backoff)\n"
            f"- Handle rate limiting (429 responses)\n"
            f"- Include proper error handling\n"
            f"- Environment variable for API key: {{API_NAME}}_API_KEY\n"
            f"- Include a health_check() method\n"
            f"- Include docstrings\n"
            f"- Keep it under 150 lines\n\n"
            f"Return JSON: {{\n"
            f'  "module_name": str (snake_case, e.g., "hunter_io"),\n'
            f'  "class_name": str (PascalCase, e.g., "HunterClient"),\n'
            f'  "code": str (complete Python module code),\n'
            f'  "env_var": str (environment variable name for API key),\n'
            f'  "usage_example": str (how to use in ARCANA)\n'
            f"}}",
            tier=Tier.SONNET,
        )

        module_name = result.get("module_name", api_name.lower().replace(" ", "_").replace("-", "_"))
        code = result.get("code", "")
        if not code:
            return {"error": "Failed to generate wrapper code"}

        # Write to a .pending.py file for human review — NOT the final path
        pending_path = self.integrations_dir / f"{module_name}.pending.py"
        pending_path.write_text(code)

        api.wrapper_module = f"src.integrations.{module_name}"
        api.status = "pending_review"
        self._save_apis()

        logger.warning(
            "LLM-generated wrapper written to %s — manual review required before use. "
            "Call approve_wrapper('%s') to promote to live.",
            pending_path, api_name,
        )
        self.memory.log(
            f"Generated API wrapper (PENDING REVIEW): {module_name} ({api.name})\n"
            f"Class: {result.get('class_name', 'N/A')}\n"
            f"Pending path: {pending_path}\n"
            f"Env var: {result.get('env_var', 'N/A')}\n"
            f"⚠ Run approve_wrapper('{api_name}') after manual review to activate.",
            "API Evolution",
        )

        return {
            "module_name": module_name,
            "class_name": result.get("class_name", ""),
            "path": str(pending_path),
            "status": "pending_review",
            "env_var": result.get("env_var", ""),
            "usage_example": result.get("usage_example", ""),
        }

    def approve_wrapper(self, api_name: str) -> dict[str, Any]:
        """Approve a pending wrapper after manual review, promoting it to live.

        Moves the .pending.py file to .py and sets the API status to 'integrated'.
        """
        api = self.apis.get(api_name)
        if not api:
            return {"error": f"Unknown API: {api_name}"}
        if api.status != "pending_review":
            return {"error": f"API '{api_name}' is not pending review (status: {api.status})"}

        module_name = api.wrapper_module.rsplit(".", 1)[-1] if api.wrapper_module else api_name.lower().replace(" ", "_").replace("-", "_")
        pending_path = self.integrations_dir / f"{module_name}.pending.py"
        final_path = self.integrations_dir / f"{module_name}.py"

        if not pending_path.exists():
            return {"error": f"Pending file not found: {pending_path}"}

        pending_path.rename(final_path)
        api.status = "integrated"
        self._save_apis()

        self.memory.log(
            f"Approved API wrapper: {module_name} ({api.name})\n"
            f"Path: {final_path}",
            "API Evolution",
        )
        logger.info("Wrapper approved and promoted: %s -> %s", pending_path, final_path)

        return {
            "module_name": module_name,
            "path": str(final_path),
            "status": "integrated",
        }

    # ── MONITOR ────────────────────────────────────────────────────

    async def health_check_all(self) -> dict[str, Any]:
        """Run health checks on all integrated APIs."""
        results = {"healthy": 0, "degraded": 0, "down": 0, "details": []}

        for name, api in self.apis.items():
            if api.status not in ("testing", "integrated"):
                continue

            try:
                start = datetime.now(timezone.utc)
                resp = await self._http.get(
                    api.base_url,
                    headers={"User-Agent": "ARCANA-AI/1.0"},
                    follow_redirects=True,
                    timeout=10.0,
                )
                latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

                if resp.status_code < 400:
                    api.health_score = min(1.0, api.health_score + 0.05)
                    status = "healthy"
                    results["healthy"] += 1
                elif resp.status_code < 500:
                    api.health_score = max(0.0, api.health_score - 0.1)
                    status = "degraded"
                    results["degraded"] += 1
                else:
                    api.health_score = max(0.0, api.health_score - 0.2)
                    status = "down"
                    results["down"] += 1

                api.avg_latency_ms = (api.avg_latency_ms * 0.7) + (latency * 0.3)
                api.last_checked = datetime.now(timezone.utc).isoformat()

                results["details"].append({
                    "name": name, "status": status,
                    "latency_ms": round(latency, 1),
                    "health_score": round(api.health_score, 2),
                })

            except Exception as exc:
                api.health_score = max(0.0, api.health_score - 0.3)
                api.error_count += 1
                results["down"] += 1
                results["details"].append({
                    "name": name, "status": "error",
                    "error": str(exc)[:80],
                })

        self._save_apis()
        return results

    def record_api_call(self, api_name: str, success: bool, latency_ms: float = 0) -> None:
        """Record an API call for monitoring."""
        api = self.apis.get(api_name)
        if not api:
            return
        api.call_count += 1
        if not success:
            api.error_count += 1
        if latency_ms > 0:
            api.avg_latency_ms = (api.avg_latency_ms * 0.8) + (latency_ms * 0.2)
        self._save_apis()

    # ── EVOLVE ─────────────────────────────────────────────────────

    async def evaluate_and_evolve(self) -> dict[str, Any]:
        """Evaluate all APIs, retire underperformers, discover replacements."""
        retired = []
        needs_replacement = []

        for name, api in list(self.apis.items()):
            if api.status == "retired":
                continue

            # Retire APIs with consistently low health
            if api.health_score < 0.2 and api.call_count > 10:
                api.status = "retired"
                retired.append(name)
                needs_replacement.append(api.category)
                continue

            # Retire APIs with high error rates
            if api.error_rate > 0.5 and api.call_count > 20:
                api.status = "retired"
                retired.append(name)
                needs_replacement.append(api.category)
                continue

            # Retire APIs with negative ROI (cost > revenue)
            if api.monthly_cost > 0 and api.roi < 0.5 and api.call_count > 50:
                api.status = "retired"
                retired.append(name)
                needs_replacement.append(api.category)

        self._save_apis()

        # Discover replacements for retired APIs
        replacements = {}
        unique_categories = list(set(needs_replacement))
        if unique_categories:
            discovery = await self.discover_apis(unique_categories)
            replacements = discovery

        if retired:
            self.memory.log(
                f"API Evolution: Retired {len(retired)} APIs ({', '.join(retired)})\n"
                f"Seeking replacements for categories: {', '.join(unique_categories)}",
                "API Evolution",
            )

        return {
            "retired": retired,
            "needs_replacement": unique_categories,
            "replacements_found": replacements,
        }

    # ── HOURLY CYCLE ───────────────────────────────────────────────

    async def hourly_cycle(self) -> dict[str, Any]:
        """The main hourly evolution cycle.

        1. Health check all integrated APIs
        2. Discover new APIs (1-2 per cycle)
        3. Evaluate discovered APIs
        4. Generate wrappers for high-value APIs
        5. Retire underperformers
        """
        logger.info("Starting API evolution hourly cycle")
        results: dict[str, Any] = {"timestamp": datetime.now(timezone.utc).isoformat()}

        # 1. Health check
        health = await self.health_check_all()
        results["health_check"] = health

        # 2. Discover (rotate through categories)
        hour = datetime.now(timezone.utc).hour
        category_list = list(API_CATEGORIES.keys())
        focus_cat = category_list[hour % len(category_list)]
        discovery = await self.discover_apis([focus_cat])
        results["discovery"] = discovery

        # 3. Evaluate any APIs in "discovered" status
        discovered_apis = [
            name for name, api in self.apis.items()
            if api.status == "discovered"
        ]
        evaluations = []
        for name in discovered_apis[:2]:  # Max 2 evaluations per cycle
            ev = await self.evaluate_api(name)
            evaluations.append(ev)

            # 4. Auto-integrate high-value APIs
            assessment = ev.get("assessment", {})
            if (assessment.get("recommendation") == "integrate"
                    and assessment.get("integration_score", 0) >= 7):
                try:
                    wrapper = await self.generate_wrapper(name)
                    results.setdefault("integrations", []).append(wrapper)
                except Exception as exc:
                    logger.error("Wrapper generation failed for %s: %s", name, exc)

        results["evaluations"] = evaluations

        # 5. Evolve (retire + replace underperformers)
        evolution = await self.evaluate_and_evolve()
        results["evolution"] = evolution

        self.memory.log(
            f"API Evolution Hourly Cycle\n"
            f"Health: {health['healthy']} ok, {health['degraded']} degraded, {health['down']} down\n"
            f"Discovered: {discovery.get('discovered', 0)} new APIs\n"
            f"Evaluated: {len(evaluations)}\n"
            f"Retired: {len(evolution.get('retired', []))}",
            "API Evolution",
        )

        return results

    # ── Reporting ──────────────────────────────────────────────────

    def format_api_report(self) -> str:
        """Format API status for reports."""
        integrated = [a for a in self.apis.values() if a.status == "integrated"]
        testing = [a for a in self.apis.values() if a.status == "testing"]
        discovered = [a for a in self.apis.values() if a.status == "discovered"]

        lines = [
            f"**API Ecosystem**: {len(integrated)} integrated, "
            f"{len(testing)} testing, {len(discovered)} discovered",
        ]

        if integrated:
            lines.append("Integrated APIs:")
            for api in sorted(integrated, key=lambda a: -a.call_count)[:5]:
                health = "OK" if api.health_score > 0.7 else "DEGRADED" if api.health_score > 0.3 else "DOWN"
                lines.append(
                    f"  [{health}] {api.name} — {api.call_count} calls, "
                    f"{api.avg_latency_ms:.0f}ms avg, {api.error_rate:.1%} errors"
                )

        return "\n".join(lines)

    async def close(self) -> None:
        await self._http.aclose()
