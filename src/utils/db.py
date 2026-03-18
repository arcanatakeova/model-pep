"""Supabase database client and logging utilities."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from supabase import AsyncClient, acreate_client

from src.config import ArcanaConfig, get_config

logger = logging.getLogger("arcana.db")


async def create_supabase_client(config: ArcanaConfig | None = None) -> AsyncClient:
    """Create an async Supabase client."""
    config = config or get_config()
    return await acreate_client(
        config.database.supabase_url,
        config.database.supabase_service_key,
    )


async def log_action(
    db: AsyncClient,
    agent: str,
    action: str,
    details: dict[str, Any] | None = None,
    cost_usd: float = 0.0,
    revenue_usd: float = 0.0,
    status: str = "success",
    error: str | None = None,
) -> None:
    """Log an agent action to the agent_log table."""
    row = {
        "agent": agent,
        "action": action,
        "details": details or {},
        "cost_usd": cost_usd,
        "revenue_usd": revenue_usd,
        "status": status,
        "error": error,
    }
    try:
        await db.table("agent_log").insert(row).execute()
    except Exception as exc:
        logger.error("Failed to log action %s.%s: %s", agent, action, exc)


async def get_portfolio(db: AsyncClient) -> dict[str, Any]:
    """Get the current portfolio state."""
    result = await db.table("portfolio").select("*").order("updated_at", desc=True).limit(1).execute()
    if result.data:
        return result.data[0]
    return {"total_value": 0, "cash_available": 0, "daily_pnl": 0, "all_time_pnl": 0}


async def update_portfolio(
    db: AsyncClient,
    total_value: float | None = None,
    cash_available: float | None = None,
    daily_pnl: float | None = None,
    all_time_pnl: float | None = None,
    positions: dict | None = None,
) -> None:
    """Update the portfolio state."""
    current = await get_portfolio(db)
    updates = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if total_value is not None:
        updates["total_value"] = total_value
    if cash_available is not None:
        updates["cash_available"] = cash_available
    if daily_pnl is not None:
        updates["daily_pnl"] = daily_pnl
    if all_time_pnl is not None:
        updates["all_time_pnl"] = all_time_pnl
    if positions is not None:
        updates["positions"] = positions

    await db.table("portfolio").update(updates).eq("id", current["id"]).execute()


async def get_daily_stats(db: AsyncClient) -> dict[str, Any]:
    """Get today's activity stats for daily summary."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trades = await db.table("trades").select("*", count="exact").gte("created_at", today).execute()
    posts = await db.table("content_posts").select("*", count="exact").gte("posted_at", today).execute()
    leads = await db.table("leads").select("*", count="exact").gte("created_at", today).execute()
    logs = await db.table("agent_log").select("cost_usd, revenue_usd").gte("created_at", today).execute()

    total_cost = sum(row.get("cost_usd", 0) or 0 for row in logs.data)
    total_revenue = sum(row.get("revenue_usd", 0) or 0 for row in logs.data)

    return {
        "trades": trades.count or 0,
        "posts": posts.count or 0,
        "leads": leads.count or 0,
        "total_cost": total_cost,
        "total_revenue": total_revenue,
    }
