"""ARCANA AI — Webhook Server.

Lightweight async HTTP server for incoming webhooks and health monitoring.

Endpoints:
- POST /webhooks/stripe    — Stripe payment events (signature-verified)
- POST /webhooks/gumroad   — Gumroad IPN sale notifications
- POST /webhooks/beehiiv   — Beehiiv subscriber events
- POST /webhooks/sendblue  — Inbound iMessages from Ian & Tan via Sendblue
- GET  /health             — Liveness probe with uptime
- GET  /dashboard          — Revenue & ops dashboard as JSON

Runs as a background task alongside the main orchestrator loop.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from src.config import Config, get_config
from src.memory import Memory
from src.payments import PaymentsEngine

logger = logging.getLogger("arcana.webhooks")


class WebhookServer:
    """Async HTTP server handling inbound webhooks and status endpoints."""

    def __init__(
        self,
        memory: Memory,
        payments: PaymentsEngine,
        *,
        port: int | None = None,
        stripe_webhook_secret: str = "",
        config: Config | None = None,
        imessage_callback: Any | None = None,
    ) -> None:
        self.memory = memory
        self.payments = payments
        self.config = config or get_config()
        self.port = port or int(os.getenv("WEBHOOK_PORT", "8080"))
        self.stripe_webhook_secret = stripe_webhook_secret or os.getenv(
            "STRIPE_WEBHOOK_SECRET", ""
        )
        # Called with (sender_number, message_text) when an iMessage arrives
        self._imessage_callback = imessage_callback
        self._start_time: float = time.monotonic()
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    # ── App Factory ──────────────────────────────────────────────────

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._logging_middleware])
        app.router.add_post("/webhooks/stripe", self._handle_stripe)
        app.router.add_post("/webhooks/gumroad", self._handle_gumroad)
        app.router.add_post("/webhooks/beehiiv", self._handle_beehiiv)
        app.router.add_post("/webhooks/sendblue", self._handle_sendblue)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/dashboard", self._handle_dashboard)
        return app

    # ── Middleware ────────────────────────────────────────────────────

    @web.middleware
    async def _logging_middleware(
        self, request: web.Request, handler
    ) -> web.StreamResponse:
        start = time.monotonic()
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning(
                "%s %s -> %s (%.1fms)",
                request.method, request.path, exc.status, elapsed,
            )
            raise
        except Exception:
            elapsed = (time.monotonic() - start) * 1000
            logger.error(
                "%s %s -> 500 (%.1fms)",
                request.method, request.path, elapsed,
            )
            raise
        else:
            elapsed = (time.monotonic() - start) * 1000
            logger.info(
                "%s %s -> %s (%.1fms)",
                request.method, request.path, response.status, elapsed,
            )
            return response

    # ── Stripe Webhook ───────────────────────────────────────────────

    async def _handle_stripe(self, request: web.Request) -> web.Response:
        """Verify Stripe signature and delegate to PaymentsEngine.handle_webhook."""
        raw_body = await request.read()
        signature = request.headers.get("Stripe-Signature", "")

        if self.stripe_webhook_secret and not signature:
            return web.json_response(
                {"error": "Missing Stripe-Signature header"}, status=400,
            )

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # PaymentsEngine.handle_webhook does signature verification internally
        # when a webhook secret is provided.  Pass the raw body (bytes decoded
        # to str) so stripe.Webhook.construct_event can verify against it.
        result = self.payments.handle_webhook(
            payload=raw_body.decode("utf-8") if self.stripe_webhook_secret else payload,
            signature=signature,
            webhook_secret=self.stripe_webhook_secret,
        )

        status_code = 200 if result.get("status") != "error" else 400
        self.memory.log(
            f"[Webhook] Stripe event processed: {result.get('event_type', 'unknown')} "
            f"-> {result.get('status', '?')}",
            "Webhooks",
        )
        return web.json_response(result, status=status_code)

    # ── Gumroad IPN ──────────────────────────────────────────────────

    async def _handle_gumroad(self, request: web.Request) -> web.Response:
        """Parse Gumroad Instant Payment Notification and log the sale."""
        try:
            # Gumroad sends form-encoded data by default
            if request.content_type == "application/json":
                data = await request.json()
            else:
                data = dict(await request.post())
        except Exception:
            return web.json_response({"error": "Invalid payload"}, status=400)

        product_name = data.get("product_name", data.get("product_permalink", "unknown"))
        email = data.get("email", data.get("purchaser_id", "unknown"))
        price = data.get("price", data.get("sale_gross", 0))
        sale_id = data.get("sale_id", data.get("order_number", "unknown"))
        refunded = data.get("refunded", "false")

        # Normalize price to dollars
        try:
            price_dollars = float(price) / 100 if isinstance(price, (int, float)) and float(price) > 100 else float(price)
        except (TypeError, ValueError):
            price_dollars = 0.0

        if str(refunded).lower() in ("true", "1", "yes"):
            self.memory.log(
                f"[Webhook] Gumroad REFUND: {email} — {product_name} "
                f"(${price_dollars:.2f})\n  Sale: {sale_id}",
                "Refunds",
            )
            return web.json_response({"status": "refund_logged", "sale_id": sale_id})

        self.memory.log(
            f"[Webhook] Gumroad SALE: {email} — {product_name} "
            f"${price_dollars:.2f}\n  Sale: {sale_id}",
            "Revenue",
        )

        # Persist sale record for daily/nightly reporting
        self.memory.save_knowledge(
            "projects",
            f"gumroad-sale-{sale_id}",
            f"# Gumroad Sale\n\n"
            f"Product: {product_name}\n"
            f"Customer: {email}\n"
            f"Amount: ${price_dollars:.2f}\n"
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}\n",
        )

        return web.json_response({"status": "sale_logged", "sale_id": sale_id})

    # ── Beehiiv Subscriber Events ────────────────────────────────────

    async def _handle_beehiiv(self, request: web.Request) -> web.Response:
        """Handle Beehiiv webhook events (subscribe, unsubscribe, etc.)."""
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "Invalid JSON"}, status=400)

        event_type = data.get("type", data.get("event", "unknown"))
        subscriber = data.get("data", data.get("subscriber", {}))
        email = subscriber.get("email", "unknown")

        self.memory.log(
            f"[Webhook] Beehiiv {event_type}: {email}",
            "Newsletter",
        )

        if event_type in ("subscriber.created", "subscribe"):
            self.memory.log(
                f"[Newsletter] New subscriber: {email}",
                "Growth",
            )
        elif event_type in ("subscriber.deleted", "unsubscribe"):
            self.memory.log(
                f"[Newsletter] Unsubscribe: {email}",
                "Churn",
            )

        return web.json_response({"status": "received", "event": event_type})

    # ── Sendblue Inbound iMessage ────────────────────────────────────

    async def _handle_sendblue(self, request: web.Request) -> web.Response:
        """Handle inbound iMessages relayed by Sendblue.

        Sendblue sends:
          {
            "accountEmail": "...",
            "content": "message text",
            "media_url": "...",
            "number": "+1XXXXXXXXXX",
            "was_downgraded": false,
            ...
          }

        We identify the sender (Ian or Tan), log the message, and invoke the
        callback so the orchestrator can process the instruction.
        """
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "Invalid JSON"}, status=400)

        sender_number = data.get("number", "")
        content = data.get("content", "")
        media_url = data.get("media_url", "")

        if not content and not media_url:
            return web.json_response({"status": "empty_message"})

        # Identify sender
        sender_name = "unknown"
        if sender_number == self.config.imessage_ian_number:
            sender_name = "Ian"
        elif sender_number == self.config.imessage_tan_number:
            sender_name = "Tan"

        log_entry = (
            f"[iMessage] From {sender_name} ({sender_number}):\n"
            f"  {content[:500]}"
        )
        if media_url:
            log_entry += f"\n  Media: {media_url}"

        self.memory.log(log_entry, "iMessage")
        logger.info("Inbound iMessage from %s: %s", sender_name, content[:100])

        # Fire callback for orchestrator to process the message
        if self._imessage_callback and content:
            try:
                import asyncio
                if asyncio.iscoroutinefunction(self._imessage_callback):
                    await self._imessage_callback(sender_number, sender_name, content)
                else:
                    self._imessage_callback(sender_number, sender_name, content)
            except Exception as exc:
                logger.error("iMessage callback failed for %s: %s", sender_name, exc)

        return web.json_response({
            "status": "received",
            "sender": sender_name,
        })

    # ── Health Check ─────────────────────────────────────────────────

    async def _handle_health(self, request: web.Request) -> web.Response:
        uptime_seconds = round(time.monotonic() - self._start_time, 2)
        return web.json_response({
            "status": "alive",
            "uptime": uptime_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Dashboard ────────────────────────────────────────────────────

    async def _handle_dashboard(self, request: web.Request) -> web.Response:
        """Return a JSON snapshot of revenue and operational state."""
        dashboard: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime": round(time.monotonic() - self._start_time, 2),
        }

        # Revenue data (non-blocking; catch errors so /dashboard stays up)
        try:
            dashboard["stripe"] = self.payments.get_revenue_summary(days=30)
        except Exception as exc:
            dashboard["stripe"] = {"error": str(exc)}

        try:
            dashboard["stripe_mrr"] = self.payments.get_mrr()
        except Exception as exc:
            dashboard["stripe_mrr"] = {"error": str(exc)}

        try:
            dashboard["gumroad"] = await self.payments.get_gumroad_revenue()
        except Exception as exc:
            dashboard["gumroad"] = {"error": str(exc)}

        try:
            dashboard["active_subscriptions"] = self.payments.get_active_subscriptions()
        except Exception as exc:
            dashboard["active_subscriptions"] = {"error": str(exc)}

        try:
            dashboard["upcoming_renewals"] = self.payments.get_upcoming_renewals(days=7)
        except Exception as exc:
            dashboard["upcoming_renewals"] = {"error": str(exc)}

        return web.json_response(dashboard)

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the webhook server as a background task (non-blocking)."""
        self._start_time = time.monotonic()
        self._app = self._build_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        logger.info("Webhook server listening on 0.0.0.0:%s", self.port)
        self.memory.log(
            f"[Webhook] Server started on port {self.port}",
            "System",
        )

    async def stop(self) -> None:
        """Gracefully shut down the server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._app = None
            logger.info("Webhook server stopped")
