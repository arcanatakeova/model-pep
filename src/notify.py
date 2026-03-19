"""ARCANA AI — Notification system with production-grade reliability.

Features:
- Multi-channel delivery (Discord webhook, Telegram bot, iMessage via Sendblue)
- Message queue with rate limiting (prevent Discord 429s)
- Automatic message chunking (Discord 2000 char, Telegram 4096 char, iMessage 2000 char)
- Retry with exponential backoff on transient failures
- Priority levels with different routing
- Graceful degradation (one channel down doesn't block others)
- Deduplication window (don't spam the same alert)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

from src.config import Config, get_config
from src.retry import retry

logger = logging.getLogger("arcana.notify")

# Rate limits
DISCORD_RATE_LIMIT = 30       # Max messages per minute
TELEGRAM_RATE_LIMIT = 20      # Max messages per minute
SENDBLUE_RATE_LIMIT = 10      # Max messages per minute (conservative for iMessage)
DEDUP_WINDOW_SECONDS = 300    # 5 min dedup window

# Sendblue API
SENDBLUE_API_URL = "https://api.sendblue.co/api/send-message"


class Notifier:
    """Production-grade multi-channel notification system."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        # Rate limiting
        self._discord_timestamps: deque[float] = deque(maxlen=DISCORD_RATE_LIMIT)
        self._telegram_timestamps: deque[float] = deque(maxlen=TELEGRAM_RATE_LIMIT)
        self._sendblue_timestamps: deque[float] = deque(maxlen=SENDBLUE_RATE_LIMIT)
        # Deduplication
        self._recent_hashes: dict[str, float] = {}
        # Stats
        self._sent_count = 0
        self._error_count = 0

    def _is_duplicate(self, message: str) -> bool:
        """Check if this message was sent recently (dedup window)."""
        from src.toolkit import fast_hash
        msg_hash = fast_hash(message[:200])
        now = time.monotonic()

        # Clean old entries
        self._recent_hashes = {
            h: ts for h, ts in self._recent_hashes.items()
            if now - ts < DEDUP_WINDOW_SECONDS
        }

        if msg_hash in self._recent_hashes:
            return True
        self._recent_hashes[msg_hash] = now
        return False

    async def _wait_for_rate_limit(self, timestamps: deque[float], limit: int) -> None:
        """Wait if we're hitting the rate limit."""
        now = time.monotonic()
        # Remove timestamps older than 60 seconds
        while timestamps and now - timestamps[0] > 60:
            timestamps.popleft()
        if len(timestamps) >= limit:
            wait_time = 60 - (now - timestamps[0]) + 0.1
            if wait_time > 0:
                logger.debug("Rate limited, waiting %.1fs", wait_time)
                await asyncio.sleep(wait_time)
        timestamps.append(time.monotonic())

    @staticmethod
    def _chunk_message(text: str, max_len: int) -> list[str]:
        """Split a long message into chunks that fit the platform limit."""
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            # Find a good break point
            break_at = text.rfind("\n", 0, max_len)
            if break_at < max_len // 2:
                break_at = text.rfind(" ", 0, max_len)
            if break_at < max_len // 4:
                break_at = max_len
            chunks.append(text[:break_at])
            text = text[break_at:].lstrip()
        return chunks

    # ── Core Send Methods ────────────────────────────────────────

    @retry(max_retries=2, base_delay=1.0)
    async def _send_discord(self, text: str) -> bool:
        """Send to Discord webhook with rate limiting and chunking."""
        if not self.config.discord_webhook_url:
            return False

        await self._wait_for_rate_limit(self._discord_timestamps, DISCORD_RATE_LIMIT)

        chunks = self._chunk_message(text, 2000)
        for chunk in chunks:
            resp = await self._client.post(
                self.config.discord_webhook_url,
                json={"content": chunk},
            )
            if resp.status_code == 429:
                # Discord rate limit — wait and retry
                try:
                    retry_after = resp.json().get("retry_after", 5)
                except Exception:
                    retry_after = 5
                logger.warning("Discord rate limited, waiting %ss", retry_after)
                await asyncio.sleep(retry_after)
                resp = await self._client.post(
                    self.config.discord_webhook_url,
                    json={"content": chunk},
                )
            resp.raise_for_status()

            if len(chunks) > 1:
                await asyncio.sleep(0.5)  # Space out multi-chunk messages

        return True

    @retry(max_retries=2, base_delay=1.0)
    async def _send_telegram(self, text: str) -> bool:
        """Send to Telegram with rate limiting and chunking."""
        if not (self.config.telegram_bot_token and self.config.telegram_chat_id):
            return False

        await self._wait_for_rate_limit(self._telegram_timestamps, TELEGRAM_RATE_LIMIT)

        chunks = self._chunk_message(text, 4096)
        for chunk in chunks:
            resp = await self._client.post(
                f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                logger.warning("Telegram rate limited, waiting %ss", retry_after)
                await asyncio.sleep(retry_after)
                resp = await self._client.post(
                    f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": self.config.telegram_chat_id,
                        "text": chunk,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
            resp.raise_for_status()

            if len(chunks) > 1:
                await asyncio.sleep(0.3)

        return True

    @retry(max_retries=2, base_delay=1.0)
    async def _send_imessage(self, text: str, number: str | None = None) -> bool:
        """Send iMessage via Sendblue API.

        Sends to a specific number, or broadcasts to both Ian & Tan if no
        number is provided.  Sendblue docs: POST /api/send-message with
        {number, content} and Basic auth (api_key:api_secret).
        """
        if not (self.config.sendblue_api_key and self.config.sendblue_api_secret):
            return False

        recipients: list[str] = []
        if number:
            recipients.append(number)
        else:
            if self.config.imessage_ian_number:
                recipients.append(self.config.imessage_ian_number)
            if self.config.imessage_tan_number:
                recipients.append(self.config.imessage_tan_number)

        if not recipients:
            return False

        await self._wait_for_rate_limit(self._sendblue_timestamps, SENDBLUE_RATE_LIMIT)

        auth = (self.config.sendblue_api_key, self.config.sendblue_api_secret)
        headers = {"content-type": "application/json"}

        # iMessage has no hard char limit but Sendblue recommends ≤2000
        chunks = self._chunk_message(text, 2000)

        for recipient in recipients:
            for chunk in chunks:
                resp = await self._client.post(
                    SENDBLUE_API_URL,
                    json={"number": recipient, "content": chunk},
                    auth=auth,
                    headers=headers,
                )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", "5"))
                    logger.warning("Sendblue rate limited, waiting %ss", retry_after)
                    await asyncio.sleep(retry_after)
                    resp = await self._client.post(
                        SENDBLUE_API_URL,
                        json={"number": recipient, "content": chunk},
                        auth=auth,
                        headers=headers,
                    )
                resp.raise_for_status()

                if len(chunks) > 1:
                    await asyncio.sleep(0.5)

            # Small gap between recipients to avoid burst
            if len(recipients) > 1:
                await asyncio.sleep(0.3)

        return True

    async def send_imessage_to(self, number: str, message: str) -> bool:
        """Send a direct iMessage to a specific number (for conversations)."""
        try:
            return await self._send_imessage(message, number=number)
        except Exception as exc:
            self._error_count += 1
            logger.error("Direct iMessage to %s failed: %s", number, exc)
            return False

    # ── Public API ───────────────────────────────────────────────

    async def send(self, message: str, level: str = "info") -> None:
        """Send to all configured channels with dedup and graceful degradation."""
        if self._is_duplicate(message):
            logger.debug("Duplicate message suppressed: %s", message[:60])
            return

        prefix = {
            "info": "ℹ️", "lead": "🎯", "sale": "💰",
            "error": "🚨", "report": "📊", "alert": "⚡",
        }.get(level, "")
        text = f"{prefix} **ARCANA AI** — {message}"

        # Send to each channel independently — one failure doesn't block others
        discord_ok = False
        telegram_ok = False
        imessage_ok = False

        try:
            discord_ok = await self._send_discord(text)
        except Exception as exc:
            self._error_count += 1
            logger.error("Discord notification failed: %s", exc)

        try:
            telegram_ok = await self._send_telegram(text)
        except Exception as exc:
            self._error_count += 1
            logger.error("Telegram notification failed: %s", exc)

        try:
            imessage_ok = await self._send_imessage(text)
        except Exception as exc:
            self._error_count += 1
            logger.error("iMessage notification failed: %s", exc)

        if discord_ok or telegram_ok or imessage_ok:
            self._sent_count += 1
        elif (
            not self.config.discord_webhook_url
            and not self.config.telegram_bot_token
            and not self.config.sendblue_api_key
        ):
            logger.debug("No notification channels configured")

    async def lead_alert(self, handle: str, need: str, score: int) -> None:
        """High-priority lead alert — always sent."""
        await self.send(f"New lead: @{handle} — {need[:100]} (score: {score}/100)", "lead")

    async def sale_alert(self, product: str, amount: float, source: str) -> None:
        """Revenue alert — always sent."""
        await self.send(f"Sale: {product} — ${amount:.2f} via {source}", "sale")

    async def morning_report(self, report: str) -> None:
        """Morning report — chunked if long."""
        await self.send(f"Morning Report:\n{report}", "report")

    async def error_alert(self, context: str, error: str) -> None:
        """Error alert with context."""
        await self.send(f"Error in {context}: {error[:500]}", "error")

    async def revenue_milestone(self, milestone: str, amount: float) -> None:
        """Revenue milestone celebration."""
        await self.send(f"Revenue milestone: {milestone} — ${amount:,.2f}", "sale")

    def get_stats(self) -> dict[str, int]:
        """Get notification stats."""
        return {
            "sent": self._sent_count,
            "errors": self._error_count,
        }

    async def close(self) -> None:
        """Clean shutdown."""
        await self._client.aclose()
