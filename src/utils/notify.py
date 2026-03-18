"""Discord/Telegram notification system.
Alerts Ian & Tan on trades, leads, errors, and daily summaries."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import httpx

from src.config import ArcanaConfig, get_config

logger = logging.getLogger("arcana.notify")


class AlertLevel(str, Enum):
    INFO = "info"
    TRADE = "trade"
    LEAD = "lead"
    ERROR = "error"
    DAILY_SUMMARY = "daily_summary"


LEVEL_EMOJI = {
    AlertLevel.INFO: "ℹ️",
    AlertLevel.TRADE: "📈",
    AlertLevel.LEAD: "🎯",
    AlertLevel.ERROR: "🚨",
    AlertLevel.DAILY_SUMMARY: "📊",
}


class Notifier:
    """Send notifications to Discord and Telegram."""

    def __init__(self, config: ArcanaConfig | None = None) -> None:
        self.config = config or get_config()
        self._client = httpx.AsyncClient(timeout=30.0)

    async def send(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Send a notification to all configured channels."""
        emoji = LEVEL_EMOJI.get(level, "")
        formatted = f"{emoji} **ARCANA AI — {level.value.upper()}**\n{message}"

        if details:
            detail_lines = "\n".join(f"• {k}: {v}" for k, v in details.items())
            formatted += f"\n\n{detail_lines}"

        errors: list[str] = []

        if self.config.notifications.discord_webhook_url:
            try:
                await self._send_discord(formatted)
            except Exception as exc:
                errors.append(f"Discord: {exc}")

        if self.config.notifications.telegram_bot_token and self.config.notifications.telegram_chat_id:
            try:
                await self._send_telegram(formatted)
            except Exception as exc:
                errors.append(f"Telegram: {exc}")

        if errors:
            logger.error("Notification errors: %s", "; ".join(errors))
        else:
            logger.info("Notification sent: %s [%s]", message[:80], level.value)

    async def _send_discord(self, message: str) -> None:
        """Send message via Discord webhook."""
        url = self.config.notifications.discord_webhook_url
        # Discord has 2000 char limit
        for chunk in _chunk_message(message, 2000):
            resp = await self._client.post(url, json={"content": chunk})
            resp.raise_for_status()

    async def _send_telegram(self, message: str) -> None:
        """Send message via Telegram Bot API."""
        token = self.config.notifications.telegram_bot_token
        chat_id = self.config.notifications.telegram_chat_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        # Telegram has 4096 char limit
        for chunk in _chunk_message(message, 4096):
            resp = await self._client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                },
            )
            resp.raise_for_status()

    async def trade_alert(
        self,
        market: str,
        direction: str,
        size_usd: float,
        entry_price: float,
        pnl_usd: float | None = None,
        pnl_pct: float | None = None,
    ) -> None:
        """Send a trade notification."""
        msg = f"Trade: {direction.upper()} {market} — ${size_usd:.2f}"
        details: dict[str, Any] = {"Entry": f"${entry_price:.6f}"}
        if pnl_usd is not None:
            details["P&L"] = f"${pnl_usd:+.2f} ({pnl_pct:+.1f}%)"
        await self.send(msg, AlertLevel.TRADE, details)

    async def lead_alert(self, handle: str, industry: str, need: str, score: float) -> None:
        """Send a lead notification — highest priority."""
        msg = f"New lead from @{handle}!"
        details = {
            "Industry": industry,
            "Need": need,
            "Score": f"{score:.0f}/100",
        }
        await self.send(msg, AlertLevel.LEAD, details)

    async def error_alert(self, agent: str, action: str, error: str) -> None:
        """Send an error notification."""
        msg = f"Error in {agent}.{action}"
        await self.send(msg, AlertLevel.ERROR, {"Error": error})

    async def daily_summary(
        self,
        total_revenue: float,
        total_cost: float,
        trades: int,
        posts: int,
        leads: int,
    ) -> None:
        """Send the daily summary to Ian & Tan."""
        msg = "Daily Summary"
        details = {
            "Revenue": f"${total_revenue:.2f}",
            "Costs": f"${total_cost:.2f}",
            "Net": f"${total_revenue - total_cost:.2f}",
            "Trades": str(trades),
            "Posts": str(posts),
            "New Leads": str(leads),
        }
        await self.send(msg, AlertLevel.DAILY_SUMMARY, details)

    async def close(self) -> None:
        await self._client.aclose()


def _chunk_message(message: str, max_len: int) -> list[str]:
    """Split a message into chunks respecting max length."""
    if len(message) <= max_len:
        return [message]
    chunks = []
    while message:
        if len(message) <= max_len:
            chunks.append(message)
            break
        split_at = message.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(message[:split_at])
        message = message[split_at:].lstrip("\n")
    return chunks
