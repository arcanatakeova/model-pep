"""ARCANA AI — Creator Agent
UGC video production via HeyGen/MakeUGC API.
Generates scripts, produces videos, manages quality gate, handles delivery.

Pricing:
- Single video: $50-150 (cost: $1-5, margin: 93-98%)
- Monthly 10-pack: $400-800/mo
- Enterprise 50+: $1,500-3,000/mo
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

from src.config import ArcanaConfig
from src.utils.db import log_action
from src.utils.llm import LLMClient, ModelTier
from src.utils.memory import MemorySystem

logger = logging.getLogger("arcana.creator")


class VideoJob(BaseModel):
    order_id: str | None = None
    product_url: str | None = None
    target_audience: str = ""
    key_points: list[str] = []
    script: str = ""
    avatar_id: str = ""
    voice_id: str = ""
    video_id: str | None = None
    video_url: str | None = None
    status: str = "pending"


class Creator:
    """UGC video production and digital content creation agent."""

    def __init__(
        self,
        config: ArcanaConfig,
        llm: LLMClient,
        db: Any,
        memory: MemorySystem,
    ) -> None:
        self.config = config
        self.llm = llm
        self.db = db
        self.memory = memory
        self._client = httpx.AsyncClient(timeout=120.0)

    async def get_available_actions(self) -> list:
        """Return available creator actions."""
        from src.orchestrator import Action
        actions = []

        # Check for pending UGC orders
        pending = await self.db.table("ugc_orders").select("*", count="exact").eq("status", "pending").execute()
        if pending.count and pending.count > 0:
            actions.append(
                Action(
                    agent="creator",
                    name="produce_ugc_video",
                    description=f"Produce {pending.count} pending UGC video orders",
                    expected_revenue=75 * pending.count,  # Avg $75/video
                    probability=0.9,
                    time_hours=0.2,
                    risk=1.0,
                    params={"order_ids": [o["id"] for o in (pending.data or [])]},
                )
            )

        # Generate content for X (case files, behind-the-scenes)
        actions.append(
            Action(
                agent="creator",
                name="create_case_file",
                description="Create a Case File thread showcasing Arcana Operations capabilities",
                expected_revenue=20,
                probability=0.3,
                time_hours=0.15,
                risk=1.0,
            )
        )

        return actions

    async def execute_action(self, action: Any) -> dict[str, Any]:
        """Execute a creator action."""
        if action.name == "produce_ugc_video":
            return await self._process_video_orders(action.params.get("order_ids", []))
        elif action.name == "create_case_file":
            return await self._create_case_file()
        return {"status": "unknown_action"}

    async def generate_script(
        self,
        product_url: str,
        target_audience: str,
        key_points: list[str],
    ) -> list[str]:
        """Generate 3 UGC script variants using Claude Sonnet."""
        prompt = (
            f"Generate 3 UGC video script variants for this product.\n\n"
            f"Product URL: {product_url}\n"
            f"Target audience: {target_audience}\n"
            f"Key selling points: {', '.join(key_points)}\n\n"
            f"Each script must follow the Hook → Body → CTA format:\n"
            f"- Hook (3-5 seconds): Attention-grabbing opener\n"
            f"- Body (15-25 seconds): Key benefits with social proof\n"
            f"- CTA (5-8 seconds): Clear call to action\n\n"
            f"Max 1500 characters per script (MakeUGC limit).\n"
            f"Tone: Authentic, conversational, NOT salesy.\n\n"
            f"Return as JSON: {{\"scripts\": [str, str, str]}}"
        )

        result = await self.llm.complete_json(prompt, tier=ModelTier.SONNET)
        return result.get("scripts", [])

    async def produce_video_heygen(self, script: str, avatar_id: str, voice_id: str) -> dict[str, Any]:
        """Generate a video via HeyGen API."""
        if not self.config.content.heygen_api_key:
            return {"status": "error", "message": "HeyGen API key not configured"}

        headers = {"X-Api-Key": self.config.content.heygen_api_key}

        payload = {
            "video_inputs": [
                {
                    "character": {"type": "avatar", "avatar_id": avatar_id},
                    "voice": {"type": "text", "input_text": script, "voice_id": voice_id},
                    "background": {"type": "color", "value": "#FFFFFF"},
                }
            ],
            "dimension": {"width": 1080, "height": 1920},  # Vertical for social
        }

        try:
            resp = await self._client.post(
                "https://api.heygen.com/v2/video/generate",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            video_id = data["data"]["video_id"]

            logger.info("HeyGen video generation started: %s", video_id)
            return {"status": "generating", "video_id": video_id}

        except Exception as exc:
            logger.error("HeyGen video generation failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def check_video_status(self, video_id: str) -> dict[str, Any]:
        """Check HeyGen video generation status."""
        headers = {"X-Api-Key": self.config.content.heygen_api_key}

        try:
            resp = await self._client.get(
                f"https://api.heygen.com/v1/video_status.get?video_id={video_id}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()["data"]

            return {
                "status": data.get("status", "unknown"),
                "video_url": data.get("video_url"),
                "thumbnail_url": data.get("thumbnail_url"),
            }
        except Exception as exc:
            logger.error("Failed to check video status: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def _process_video_orders(self, order_ids: list[str]) -> dict[str, Any]:
        """Process pending UGC video orders."""
        processed = 0
        errors = []

        for order_id in order_ids:
            try:
                order = await self.db.table("ugc_orders").select("*").eq("id", order_id).single().execute()
                if not order.data:
                    continue

                order_data = order.data

                # Generate script if not already provided
                if not order_data.get("script"):
                    scripts = await self.generate_script(
                        product_url=order_data.get("product_url", ""),
                        target_audience="general consumer",
                        key_points=["product benefits"],
                    )
                    if scripts:
                        script = scripts[0]
                        await self.db.table("ugc_orders").update({"script": script}).eq("id", order_id).execute()
                    else:
                        errors.append(f"Order {order_id}: Failed to generate script")
                        continue
                else:
                    script = order_data["script"]

                # Produce video
                avatar_id = order_data.get("avatar_id", "default_avatar")
                result = await self.produce_video_heygen(script, avatar_id, "default_voice")

                if result.get("video_id"):
                    await self.db.table("ugc_orders").update({
                        "status": "generating",
                    }).eq("id", order_id).execute()
                    processed += 1
                else:
                    errors.append(f"Order {order_id}: {result.get('message', 'Unknown error')}")

            except Exception as exc:
                errors.append(f"Order {order_id}: {exc}")

        await log_action(
            self.db, "creator", "produce_ugc_videos",
            details={"processed": processed, "errors": errors},
            revenue_usd=processed * 75,
            cost_usd=processed * 3,
        )

        return {"status": "processed", "videos_started": processed, "errors": errors}

    async def _create_case_file(self) -> dict[str, Any]:
        """Create a Case File thread for X — showcases Arcana Operations expertise."""
        # Rotate between business case studies
        businesses = [
            {
                "name": "Navigate Peptides",
                "type": "e-commerce",
                "topics": ["headless Shopify", "automated compliance", "SEO for regulated products"],
            },
            {
                "name": "Autobahn Collective",
                "type": "marketplace",
                "topics": ["multi-platform listing", "parts CRM", "community building"],
            },
            {
                "name": "AI Consulting",
                "type": "service",
                "topics": ["agent development", "workflow automation", "revenue optimization"],
            },
        ]

        import random
        business = random.choice(businesses)

        prompt = (
            f"Generate a Case File thread for ARCANA AI's X account.\n\n"
            f"Business: {business['name']} ({business['type']})\n"
            f"Topics to cover: {', '.join(business['topics'])}\n\n"
            f"Format: 4-5 tweet thread\n"
            f"- Tweet 1: 🗂️ CASE FILE #{random.randint(1, 99)} — [Title]\n"
            f"- Tweets 2-4: The problem, the solution, the results\n"
            f"- Final tweet: How Arcana Operations can do this for your business\n\n"
            f"Rules:\n"
            f"- Use real-sounding but anonymized metrics\n"
            f"- Be specific about tools and techniques\n"
            f"- End with arcanaoperations.com\n\n"
            f"Return as JSON: {{\"tweets\": [str, str, str, str, str]}}"
        )

        result = await self.llm.complete_json(prompt, tier=ModelTier.SONNET)
        tweets = result.get("tweets", [])

        if tweets:
            await self.memory.store(
                f"Created Case File about {business['name']}: {business['topics']}",
                category="content_performance",
                importance_score=0.4,
            )
            return {"status": "generated", "tweets": len(tweets), "business": business["name"]}

        return {"status": "error", "message": "Failed to generate case file"}
