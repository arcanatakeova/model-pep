"""Discord/Telegram notification system.
Alerts Ian & Tan on: trades, leads, errors, daily summaries."""

import os
import httpx
from datetime import datetime
from typing import Optional

async def discord(message: str, title: Optional[str] = None, color: int = 0x7B2D8E) -> None:
    """Send Discord webhook notification."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return
    payload = {"embeds": [{"title": title or "ARCANA AI", "description": message, "color": color, "timestamp": datetime.utcnow().isoformat()}]}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(webhook_url, json=payload)
        except Exception:
            pass  # Notification failure should never crash the agent

async def telegram(message: str) -> None:
    """Send Telegram message."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
        except Exception:
            pass

async def alert(message: str, title: Optional[str] = None, level: str = "info") -> None:
    """Send to both Discord and Telegram. Level: info, warning, error, trade, lead."""
    color_map = {"info": 0x7B2D8E, "warning": 0xD4A843, "error": 0xFF0000, "trade": 0x00FF00, "lead": 0x00BFFF}
    prefix = {"info": "", "warning": "WARNING: ", "error": "ERROR: ", "trade": "TRADE: ", "lead": "NEW LEAD: "}
    full_msg = f"{prefix.get(level, '')}{message}"
    await discord(full_msg, title=title, color=color_map.get(level, 0x7B2D8E))
    await telegram(full_msg)
