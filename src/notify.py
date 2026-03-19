"""ARCANA AI — Discord/Telegram notifications to Ian & Tan."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import Config, get_config

logger = logging.getLogger("arcana.notify")


class Notifier:
    """Send alerts to Discord and Telegram."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()
        self._client = httpx.AsyncClient(timeout=30.0)

    async def send(self, message: str, level: str = "info") -> None:
        """Send to all configured channels."""
        prefix = {"info": "ℹ️", "lead": "🎯", "sale": "💰", "error": "🚨", "report": "📊"}.get(level, "")
        text = f"{prefix} **ARCANA AI** — {message}"

        if self.config.discord_webhook_url:
            try:
                await self._client.post(
                    self.config.discord_webhook_url,
                    json={"content": text[:2000]},
                )
            except Exception as exc:
                logger.error("Discord send failed: %s", exc)

        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            try:
                await self._client.post(
                    f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": self.config.telegram_chat_id,
                        "text": text[:4096],
                        "parse_mode": "Markdown",
                    },
                )
            except Exception as exc:
                logger.error("Telegram send failed: %s", exc)

    async def lead_alert(self, handle: str, need: str, score: int) -> None:
        await self.send(f"New lead: @{handle} — {need[:100]} (score: {score}/100)", "lead")

    async def sale_alert(self, product: str, amount: float, source: str) -> None:
        await self.send(f"Sale: {product} — ${amount:.2f} via {source}", "sale")

    async def morning_report(self, report: str) -> None:
        await self.send(f"Morning Report:\n{report}", "report")

    async def error_alert(self, context: str, error: str) -> None:
        await self.send(f"Error in {context}: {error}", "error")

    async def close(self) -> None:
        await self._client.aclose()
