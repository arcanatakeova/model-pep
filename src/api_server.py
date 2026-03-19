"""ARCANA AI -- REST API Server for external integrations and monitoring.

Lightweight async HTTP API (aiohttp) on port 8081.
Provides read access to system status, pipeline, revenue, content, contacts,
and write access for triggering scans, content generation, and lead qualification.

Auth: X-API-Key header.
CORS enabled for web dashboard access.
Rate limited: 100 requests/minute per IP.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from src.config import HEARTBEAT_PATH, STOP_FILE, get_config

logger = logging.getLogger("arcana.api")

API_PORT = 8081
RATE_LIMIT = 100  # requests per minute per IP
API_VERSION = "v1"


# ============================================================================
# Rate limiter
# ============================================================================

class RateLimiter:
    """Simple sliding-window rate limiter keyed by IP address."""

    def __init__(self, max_requests: int = RATE_LIMIT, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now = time.monotonic()
        hits = self._hits[ip]
        # Prune old entries
        self._hits[ip] = [t for t in hits if now - t < self.window]
        if len(self._hits[ip]) >= self.max_requests:
            return False
        self._hits[ip].append(now)
        return True


# ============================================================================
# Response helpers
# ============================================================================

def _ok(data: Any, meta: dict[str, Any] | None = None) -> web.Response:
    body: dict[str, Any] = {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    if meta:
        body["meta"] = meta
    return web.json_response(body)


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response(
        {
            "ok": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": message,
        },
        status=status,
    )


# ============================================================================
# Middleware
# ============================================================================

@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Add CORS headers to every response."""
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            resp = exc
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Validate X-API-Key header against ARCANA_API_KEY env var."""
    # Skip auth for OPTIONS (preflight) and health check
    if request.method == "OPTIONS" or request.path == "/health":
        return await handler(request)

    expected_key = os.getenv("ARCANA_API_KEY", "")
    if not expected_key:
        # No key configured -- reject all requests until one is set
        return _error("API key not configured on server", status=503)

    provided_key = request.headers.get("X-API-Key", "")
    if provided_key != expected_key:
        return _error("Invalid or missing API key", status=401)

    return await handler(request)


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    """Enforce per-IP rate limiting."""
    limiter: RateLimiter = request.app["rate_limiter"]
    ip = request.remote or "unknown"
    if not limiter.is_allowed(ip):
        return _error("Rate limit exceeded (100 req/min)", status=429)
    return await handler(request)


# ============================================================================
# Route handlers
# ============================================================================

async def handle_health(request: web.Request) -> web.Response:
    """GET /health -- unauthenticated liveness check."""
    return _ok({"status": "healthy"})


async def handle_status(request: web.Request) -> web.Response:
    """GET /api/v1/status -- system status, uptime, component health."""
    orchestrator = request.app.get("orchestrator")
    start_time: float = request.app["start_time"]
    uptime_seconds = time.monotonic() - start_time

    # Heartbeat
    heartbeat_text = ""
    if HEARTBEAT_PATH.exists():
        heartbeat_text = HEARTBEAT_PATH.read_text()

    kill_switch = STOP_FILE.exists()

    components: dict[str, str] = {}
    if orchestrator:
        for name in (
            "llm", "memory", "notifier", "x", "content", "leads",
            "scanner", "crm", "revenue", "analytics", "email",
            "payments_engine", "scheduler", "newsletter", "services",
            "ugc", "fulfillment", "distributor",
        ):
            obj = getattr(orchestrator, name, None)
            components[name] = "online" if obj is not None else "offline"
    else:
        components["note"] = "orchestrator not attached"

    return _ok({
        "uptime_seconds": round(uptime_seconds, 1),
        "uptime_human": _format_uptime(uptime_seconds),
        "kill_switch_active": kill_switch,
        "heartbeat": heartbeat_text,
        "components": components,
    })


async def handle_pipeline(request: web.Request) -> web.Response:
    """GET /api/v1/pipeline -- full pipeline data from scanner + CRM."""
    orch = request.app.get("orchestrator")
    data: dict[str, Any] = {}

    if orch and orch.scanner:
        data["scanner_pipeline"] = orch.scanner.get_pipeline_summary()
        data["scanner_metrics"] = orch.scanner.metrics
        data["scanner_cycle"] = orch.scanner._cycle_count

    if orch and orch.crm:
        data["crm_pipeline"] = orch.crm.get_pipeline_value()
        data["crm_deals"] = orch.crm.get_pipeline()

    if not data:
        return _error("Pipeline components not available", status=503)

    return _ok(data)


async def handle_revenue(request: web.Request) -> web.Response:
    """GET /api/v1/revenue -- revenue breakdown by channel and period."""
    orch = request.app.get("orchestrator")
    if not orch or not orch.revenue:
        return _error("Revenue engine not available", status=503)

    try:
        snapshot = await orch.revenue.get_full_revenue_snapshot()
        formatted = orch.revenue.format_revenue_report(snapshot)
        return _ok({
            "snapshot": snapshot,
            "formatted": formatted,
        })
    except Exception as exc:
        logger.error("Revenue endpoint failed: %s", exc)
        return _error(f"Failed to fetch revenue: {exc}", status=500)


async def handle_opportunities(request: web.Request) -> web.Response:
    """GET /api/v1/opportunities -- recent opportunities found by scanner."""
    orch = request.app.get("orchestrator")
    if not orch or not orch.scanner:
        return _error("Scanner not available", status=503)

    pipeline_keys = orch.scanner.get_pipeline()
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))

    opportunities: list[dict[str, Any]] = []
    for key in pipeline_keys[offset : offset + limit]:
        raw = (
            orch.scanner.memory.get_knowledge("projects", key)
            or orch.scanner.memory.get_knowledge("resources", key)
        )
        if raw:
            opportunities.append({"key": key, "data": _parse_markdown_fields(raw)})

    return _ok(
        opportunities,
        meta={
            "total": len(pipeline_keys),
            "limit": limit,
            "offset": offset,
        },
    )


async def handle_content(request: web.Request) -> web.Response:
    """GET /api/v1/content -- content calendar and recent posts from memory."""
    orch = request.app.get("orchestrator")
    if not orch or not orch.memory:
        return _error("Memory not available", status=503)

    # Pull recent content-related log entries from daily notes
    recent_days = orch.memory.get_recent_days(7)
    content_entries: list[dict[str, str]] = []
    for date, notes in recent_days:
        for line in notes.splitlines():
            lower = line.lower()
            if any(kw in lower for kw in ("content", "tweet", "thread", "posted", "briefing", "case file")):
                content_entries.append({"date": date, "entry": line.strip()})

    # Include today's completed content from orchestrator
    completed = []
    if orch:
        completed = [c for c in getattr(orch, "_completed_today", []) if "ontent" in c or "tweet" in c.lower() or "post" in c.lower()]

    return _ok({
        "recent_posts": content_entries[:100],
        "completed_today": completed,
    })


async def handle_metrics(request: web.Request) -> web.Response:
    """GET /api/v1/metrics/:name -- time-series data for a named metric."""
    metric_name = request.match_info.get("name", "")
    orch = request.app.get("orchestrator")

    if not orch or not orch.analytics:
        return _error("Analytics not available", status=503)

    known_metrics = {
        "funnel": lambda: orch.analytics.get_funnel_metrics(),
        "channels": lambda: orch.analytics.get_channel_attribution(),
        "pipeline_value": lambda: orch.crm.get_pipeline_value() if orch.crm else {},
        "scanner": lambda: orch.scanner.metrics if orch.scanner else {},
        "scanner_pipeline": lambda: orch.scanner.get_pipeline_summary() if orch.scanner else {},
    }

    if metric_name not in known_metrics:
        return _error(
            f"Unknown metric '{metric_name}'. Available: {', '.join(known_metrics.keys())}",
            status=404,
        )

    try:
        result = known_metrics[metric_name]()
        return _ok({"metric": metric_name, "values": result})
    except Exception as exc:
        logger.error("Metric '%s' failed: %s", metric_name, exc)
        return _error(f"Failed to compute metric: {exc}", status=500)


async def handle_contacts(request: web.Request) -> web.Response:
    """GET /api/v1/contacts -- list/search contacts from CRM."""
    orch = request.app.get("orchestrator")
    if not orch or not orch.memory:
        return _error("Memory not available", status=503)

    query = request.query.get("q", "").lower()
    limit = int(request.query.get("limit", "50"))

    all_keys = orch.memory.list_knowledge("resources")
    contact_keys = [k for k in all_keys if k.startswith("contact-")]

    contacts: list[dict[str, Any]] = []
    for key in contact_keys:
        raw = orch.memory.get_knowledge("resources", key)
        if not raw:
            continue
        if query and query not in raw.lower():
            continue
        contacts.append({"key": key, "data": _parse_markdown_fields(raw)})
        if len(contacts) >= limit:
            break

    return _ok(
        contacts,
        meta={"total_contacts": len(contact_keys), "returned": len(contacts)},
    )


async def handle_dashboard(request: web.Request) -> web.Response:
    """GET /api/v1/dashboard -- full dashboard JSON (aggregates multiple endpoints)."""
    orch = request.app.get("orchestrator")
    start_time: float = request.app["start_time"]
    uptime_seconds = time.monotonic() - start_time

    dashboard: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": round(uptime_seconds, 1),
        "kill_switch_active": STOP_FILE.exists(),
    }

    if orch:
        # Revenue
        if orch.revenue:
            try:
                dashboard["revenue"] = await orch.revenue.get_full_revenue_snapshot()
            except Exception as exc:
                logger.error("Dashboard revenue fetch failed: %s", exc)
                dashboard["revenue"] = {"error": "failed to fetch"}

        # Analytics funnel
        if orch.analytics:
            dashboard["funnel"] = orch.analytics.get_funnel_metrics()
            dashboard["channel_attribution"] = orch.analytics.get_channel_attribution()

        # Scanner
        if orch.scanner:
            dashboard["scanner"] = {
                "metrics": orch.scanner.metrics,
                "pipeline_summary": orch.scanner.get_pipeline_summary(),
                "cycle_count": orch.scanner._cycle_count,
            }

        # CRM
        if orch.crm:
            dashboard["crm"] = orch.crm.get_pipeline_value()

        # Heartbeat
        if HEARTBEAT_PATH.exists():
            dashboard["heartbeat"] = HEARTBEAT_PATH.read_text()

        # Today's completed actions
        dashboard["completed_today"] = getattr(orch, "_completed_today", [])
        dashboard["priorities"] = getattr(orch, "_priorities", [])

    return _ok(dashboard)


async def handle_scan(request: web.Request) -> web.Response:
    """POST /api/v1/scan -- trigger a manual opportunity scan cycle."""
    orch = request.app.get("orchestrator")
    if not orch or not orch.scanner:
        return _error("Scanner not available", status=503)

    try:
        results = await orch.scanner.scan_cycle()
        return _ok(results)
    except Exception as exc:
        logger.error("Manual scan failed: %s", exc)
        return _error(f"Scan failed: {exc}", status=500)


async def handle_content_generate(request: web.Request) -> web.Response:
    """POST /api/v1/content/generate -- generate and optionally post content.

    Body JSON:
        {"type": "analysis"|"morning_briefing"|"case_file"|"bts", "post": true|false}
    """
    orch = request.app.get("orchestrator")
    if not orch or not orch.content:
        return _error("Content engine not available", status=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    content_type = body.get("type", "analysis")
    should_post = body.get("post", False)

    try:
        if should_post and orch.content.x_client:
            result = await orch.content.post_and_distribute(content_type)
        else:
            # Generate only, do not post
            if content_type == "morning_briefing":
                content = await orch.content.morning_briefing()
            elif content_type == "case_file":
                content = await orch.content.case_file()
            elif content_type == "bts":
                content = await orch.content.bts_tweet()
            elif content_type == "analysis":
                content = await orch.content.analysis_tweet()
            else:
                return _error(f"Unknown content type: {content_type}")
            result = {"content_type": content_type, "content": content, "posted": False}

        return _ok(result)
    except Exception as exc:
        logger.error("Content generation failed: %s", exc)
        return _error(f"Content generation failed: {exc}", status=500)


async def handle_leads_qualify(request: web.Request) -> web.Response:
    """POST /api/v1/leads/qualify -- manually qualify a lead.

    Body JSON:
        {"handle": "@someone", "text": "their message", "source": "manual"}
    """
    orch = request.app.get("orchestrator")
    if not orch or not orch.leads:
        return _error("Lead pipeline not available", status=503)

    try:
        body = await request.json()
    except Exception:
        return _error("Request body must be valid JSON")

    handle = body.get("handle", "")
    text = body.get("text", "")
    source = body.get("source", "api_manual")

    if not handle or not text:
        return _error("Both 'handle' and 'text' are required")

    try:
        result = await orch.leads.qualify(handle, text, source)
        return _ok(result)
    except Exception as exc:
        logger.error("Lead qualification failed: %s", exc)
        return _error(f"Qualification failed: {exc}", status=500)


async def handle_search(request: web.Request) -> web.Response:
    """GET /api/v1/search?q=query -- full-text search across all memory."""
    orch = request.app.get("orchestrator")
    if not orch or not orch.memory:
        return _error("Memory not available", status=503)

    query = request.query.get("q", "")
    scope = request.query.get("scope", "all")
    if not query:
        return _error("Query parameter 'q' is required")

    results = orch.memory.search(query, scope=scope)
    formatted = [{"path": path, "match": line} for path, line in results]

    return _ok(
        formatted,
        meta={"query": query, "scope": scope, "total_results": len(formatted)},
    )


# ============================================================================
# Helpers
# ============================================================================

def _format_uptime(seconds: float) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _parse_markdown_fields(text: str) -> dict[str, str]:
    """Parse simple '- Key: Value' lines from a markdown knowledge file."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and ":" in stripped:
            key, _, value = stripped[2:].partition(":")
            fields[key.strip().lower().replace(" ", "_")] = value.strip()
        elif stripped.startswith("# "):
            fields["title"] = stripped[2:].strip()
    return fields


# ============================================================================
# App factory
# ============================================================================

def create_app(orchestrator: Any = None) -> web.Application:
    """Create and configure the aiohttp application.

    Args:
        orchestrator: An initialized Orchestrator instance. If None, status
            endpoints still work but data endpoints return 503.
    """
    app = web.Application(
        middlewares=[cors_middleware, auth_middleware, rate_limit_middleware],
    )

    app["orchestrator"] = orchestrator
    app["rate_limiter"] = RateLimiter()
    app["start_time"] = time.monotonic()

    # Health (unauthenticated)
    app.router.add_get("/health", handle_health)

    # Read endpoints
    prefix = f"/api/{API_VERSION}"
    app.router.add_get(f"{prefix}/status", handle_status)
    app.router.add_get(f"{prefix}/pipeline", handle_pipeline)
    app.router.add_get(f"{prefix}/revenue", handle_revenue)
    app.router.add_get(f"{prefix}/opportunities", handle_opportunities)
    app.router.add_get(f"{prefix}/content", handle_content)
    app.router.add_get(f"{prefix}/metrics/{{name}}", handle_metrics)
    app.router.add_get(f"{prefix}/contacts", handle_contacts)
    app.router.add_get(f"{prefix}/dashboard", handle_dashboard)
    app.router.add_get(f"{prefix}/search", handle_search)

    # Write endpoints
    app.router.add_post(f"{prefix}/scan", handle_scan)
    app.router.add_post(f"{prefix}/content/generate", handle_content_generate)
    app.router.add_post(f"{prefix}/leads/qualify", handle_leads_qualify)

    logger.info("API server configured on port %d", API_PORT)
    return app


async def start_api_server(orchestrator: Any = None) -> web.AppRunner:
    """Start the API server as a background task (non-blocking).

    Returns the AppRunner so the caller can clean it up on shutdown.

    Usage inside the orchestrator main loop::

        runner = await start_api_server(self)
        # ... run forever ...
        await runner.cleanup()
    """
    app = create_app(orchestrator)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", API_PORT)
    await site.start()
    logger.info("API server listening on 0.0.0.0:%d", API_PORT)
    return runner


def run_standalone() -> None:
    """Run the API server standalone (without orchestrator) for testing."""
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=API_PORT)


if __name__ == "__main__":
    run_standalone()
