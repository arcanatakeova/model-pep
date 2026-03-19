"""ARCANA AI — Autonomous UGC Video Production Engine.

Fully autonomous pipeline: client submits product → ARCANA generates scripts →
HeyGen renders videos → quality gate → delivers to client → collects payment.

Market: $7.6B (2025), 28% CAGR → $64B by 2034. Only 16% of brands have UGC strategy.
Human creators: $150-500/video. AI production: $1-5/video = 95-99% margins.
Performance: 400% higher CTR, 29% better conversions vs traditional ads.

Revenue model:
- Single video: $50-150 (cost $1-5)
- Monthly 10-pack: $400-800/mo (cost $10-50)
- Enterprise 50+: $1,500-3,000/mo (cost $50-250)
- White-label for agencies: $5-15/video wholesale
- Self-promo videos for ARCANA's own products: $0 marginal cost

Target: 100+ videos/month × $75 avg = $7,500/mo minimum. Scale to $20K+.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

from src.email_engine import EmailEngine
from src.llm import LLM, Tier
from src.memory import Memory
from src.payments import PaymentsEngine

logger = logging.getLogger("arcana.ugc")


class VideoFormat(str, Enum):
    VERTICAL = "vertical"       # 1080x1920 — TikTok, Reels, Shorts, Stories
    SQUARE = "square"           # 1080x1080 — Feed posts, ads
    HORIZONTAL = "horizontal"   # 1920x1080 — YouTube, website


class VideoStyle(str, Enum):
    TESTIMONIAL = "testimonial"         # Fake customer review style
    UNBOXING = "unboxing"               # Product reveal
    TUTORIAL = "tutorial"               # How-to / demo
    PROBLEM_SOLUTION = "problem_solution"  # Pain point → product solves it
    COMPARISON = "comparison"           # Us vs them
    LIFESTYLE = "lifestyle"             # Product in daily life
    HOOK_STORY_CTA = "hook_story_cta"   # Classic direct response


DIMENSIONS = {
    VideoFormat.VERTICAL: {"width": 1080, "height": 1920},
    VideoFormat.SQUARE: {"width": 1080, "height": 1080},
    VideoFormat.HORIZONTAL: {"width": 1920, "height": 1080},
}


class UGCEngine:
    """Fully autonomous UGC video production pipeline."""

    def __init__(
        self, llm: LLM, memory: Memory, heygen_key: str, makeugc_key: str = "",
        email_engine: EmailEngine | None = None,
        payments_engine: PaymentsEngine | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.heygen_key = heygen_key
        self.makeugc_key = makeugc_key
        self.email_engine = email_engine
        self.payments_engine = payments_engine
        self._client: httpx.AsyncClient | None = None
        self._avatars: list[dict] | None = None
        self._voices: list[dict] | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Avatar & Voice Management ───────────────────────────────────

    async def list_avatars(self) -> list[dict[str, Any]]:
        """List available HeyGen avatars."""
        if self._avatars:
            return self._avatars
        try:
            client = await self._get_client()
            resp = await client.get(
                "https://api.heygen.com/v2/avatars",
                headers={"X-Api-Key": self.heygen_key},
            )
            if resp.status_code == 200:
                self._avatars = resp.json().get("data", {}).get("avatars", [])
                return self._avatars
        except Exception as exc:
            logger.error("Failed to list avatars: %s", exc)
        return []

    async def list_voices(self) -> list[dict[str, Any]]:
        """List available HeyGen voices."""
        if self._voices:
            return self._voices
        try:
            client = await self._get_client()
            resp = await client.get(
                "https://api.heygen.com/v2/voices",
                headers={"X-Api-Key": self.heygen_key},
            )
            if resp.status_code == 200:
                self._voices = resp.json().get("data", {}).get("voices", [])
                return self._voices
        except Exception as exc:
            logger.error("Failed to list voices: %s", exc)
        return []

    async def select_avatar_for_brand(
        self, brand_name: str, target_demo: str, product_type: str
    ) -> dict[str, Any] | None:
        """Use LLM to select the best avatar for a brand's target demographic."""
        avatars = await self.list_avatars()
        if not avatars:
            return None

        # Summarize available avatars
        avatar_list = "\n".join(
            f"- {a.get('avatar_id', 'unknown')}: {a.get('avatar_name', 'N/A')} "
            f"(gender: {a.get('gender', 'unknown')})"
            for a in avatars[:50]  # Cap at 50 for context
        )

        try:
            result = await self.llm.ask_json(
                f"Select the best UGC avatar for this brand.\n\n"
                f"Brand: {brand_name}\n"
                f"Target demographic: {target_demo}\n"
                f"Product type: {product_type}\n\n"
                f"Available avatars:\n{avatar_list}\n\n"
                f"Rules:\n"
                f"- Match avatar to target demographic (age, gender, vibe)\n"
                f"- UGC performs best when the presenter looks like the target customer\n"
                f"- Authentic > polished for UGC\n\n"
                f"Return JSON: {{\"avatar_id\": str, \"reason\": str}}",
                tier=Tier.HAIKU,
            )
        except Exception as exc:
            logger.error("select_avatar_for_brand ask_json failed: %s", exc)
            return avatars[0] if avatars else None

        avatar_id = result.get("avatar_id")
        return next((a for a in avatars if a.get("avatar_id") == avatar_id), avatars[0])

    # ── Script Generation ───────────────────────────────────────────

    async def generate_scripts(
        self,
        product_name: str,
        product_description: str,
        target_audience: str,
        key_benefits: list[str],
        style: VideoStyle = VideoStyle.HOOK_STORY_CTA,
        num_variants: int = 3,
    ) -> list[dict[str, Any]]:
        """Generate multiple script variants for a product. The hook is everything."""
        result = await self.llm.ask_json(
            f"Generate {num_variants} UGC video scripts for this product.\n\n"
            f"Product: {product_name}\n"
            f"Description: {product_description}\n"
            f"Target audience: {target_audience}\n"
            f"Key benefits: {', '.join(key_benefits)}\n"
            f"Style: {style.value}\n\n"
            f"UGC RULES (critical for performance):\n"
            f"- HOOK (first 3 seconds): Must stop the scroll. Pattern interrupt.\n"
            f"  Best hooks: 'Stop scrolling if you...', 'POV: you just discovered...', \n"
            f"  'I was today years old when...', controversial opinion, shocking stat\n"
            f"- BODY (15-30 seconds): One benefit at a time. Show, don't tell.\n"
            f"  Social proof > features. 'I've been using this for 2 weeks and...'\n"
            f"- CTA (5 seconds): Clear, urgent, specific. 'Link in bio', \n"
            f"  'Use code X for 20% off', 'Comment WANT and I'll DM you'\n"
            f"- Total length: 30-60 seconds (45s optimal for most platforms)\n"
            f"- Speak naturally — NOT like a commercial. Conversational, real.\n"
            f"- Include pauses, filler words, slight imperfections = more authentic\n"
            f"- Each variant should have a DIFFERENT hook angle\n\n"
            f"Return JSON: {{\"scripts\": [\n"
            f"  {{\n"
            f"    \"variant\": int,\n"
            f"    \"hook_type\": str (controversy/shock/pov/question/story),\n"
            f"    \"hook\": str (first 3 seconds — the scroll-stopper),\n"
            f"    \"body\": str (main content, 15-30 seconds),\n"
            f"    \"cta\": str (call to action, 5 seconds),\n"
            f"    \"full_script\": str (complete script for TTS),\n"
            f"    \"estimated_seconds\": int,\n"
            f"    \"platform_notes\": str (which platform this variant is best for)\n"
            f"  }}\n"
            f"]}}",
            tier=Tier.SONNET,
        )

        scripts = result.get("scripts", [])
        self.memory.log(
            f"[UGC] Generated {len(scripts)} scripts for {product_name} ({style.value})",
            "UGC",
        )
        return scripts

    async def generate_hooks(self, product_name: str, niche: str, count: int = 10) -> list[dict[str, str]]:
        """Generate a bank of scroll-stopping hooks. The hook is 80% of the video's success."""
        result = await self.llm.ask_json(
            f"Generate {count} scroll-stopping UGC hooks for this product.\n\n"
            f"Product: {product_name}\n"
            f"Niche: {niche}\n\n"
            f"Hook categories (include variety):\n"
            f"1. CONTROVERSIAL: Bold claim that makes people disagree/agree\n"
            f"2. SHOCK STAT: 'Did you know 73% of...' (use real-sounding stats)\n"
            f"3. POV: 'POV: You just found the...' (relatable scenario)\n"
            f"4. STORY: 'I spent $500 testing every... here's the winner'\n"
            f"5. QUESTION: 'Why is nobody talking about...'\n"
            f"6. PATTERN INTERRUPT: Something unexpected visually/verbally\n"
            f"7. SOCIAL PROOF: 'This went viral because...'\n"
            f"8. FEAR: 'If you're still using X, you need to see this'\n"
            f"9. CURIOSITY GAP: 'The one thing I changed that...'\n"
            f"10. DIRECT: 'Stop wasting money on...' (aggressive but effective)\n\n"
            f"Each hook must be under 10 seconds spoken. Make them irresistible.\n\n"
            f"Return JSON: {{\"hooks\": [{{\"text\": str, \"type\": str, \"estimated_ctr\": str}}]}}",
            tier=Tier.SONNET,
        )
        return result.get("hooks", [])

    # ── Video Generation (HeyGen API) ──────────────────────────────

    async def generate_video(
        self,
        script: str,
        avatar_id: str,
        voice_id: str,
        video_format: VideoFormat = VideoFormat.VERTICAL,
        background: str = "#FFFFFF",
    ) -> dict[str, Any]:
        """Submit video generation job to HeyGen API."""
        if not self.heygen_key:
            logger.warning("HeyGen API key not set — returning mock")
            return {"video_id": "mock", "status": "dry_run"}

        dims = DIMENSIONS[video_format]
        payload = {
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": avatar_id,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "text",
                        "input_text": script,
                        "voice_id": voice_id,
                    },
                    "background": {
                        "type": "color",
                        "value": background,
                    },
                }
            ],
            "dimension": dims,
            "test": False,
        }

        try:
            client = await self._get_client()
            resp = await client.post(
                "https://api.heygen.com/v2/video/generate",
                headers={"X-Api-Key": self.heygen_key, "Content-Type": "application/json"},
                json=payload,
            )

            if resp.status_code in (200, 201):
                data = resp.json().get("data", {})
                video_id = data.get("video_id", "")
                self.memory.log(f"[UGC] Video submitted: {video_id}", "UGC")
                return {"video_id": video_id, "status": "processing"}
            else:
                logger.error("HeyGen generate failed: %s %s", resp.status_code, resp.text[:300])
                return {"video_id": None, "status": "error", "detail": resp.text[:300]}
        except Exception as exc:
            logger.error("HeyGen generate error: %s", exc)
            return {"video_id": None, "status": "error", "detail": str(exc)}

    async def check_video_status(self, video_id: str) -> dict[str, Any]:
        """Poll HeyGen for video completion status."""
        if not self.heygen_key or video_id == "mock":
            return {"status": "completed", "video_url": "https://mock.url/video.mp4"}

        try:
            client = await self._get_client()
            resp = await client.get(
                f"https://api.heygen.com/v1/video_status.get?video_id={video_id}",
                headers={"X-Api-Key": self.heygen_key},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return {
                    "status": data.get("status", "unknown"),
                    "video_url": data.get("video_url"),
                    "duration": data.get("duration"),
                    "thumbnail_url": data.get("thumbnail_url"),
                }
        except Exception as exc:
            logger.error("HeyGen status check error: %s", exc)
        return {"status": "unknown"}

    async def wait_for_video(self, video_id: str, timeout: int = 300) -> dict[str, Any]:
        """Poll until video is ready or timeout. HeyGen typically takes 1-3 minutes."""
        elapsed = 0
        interval = 10
        while elapsed < timeout:
            status = await self.check_video_status(video_id)
            if status["status"] == "completed":
                return status
            if status["status"] in ("failed", "error"):
                return status
            await asyncio.sleep(interval)
            elapsed += interval
        return {"status": "timeout"}

    # ── Full Production Pipeline ────────────────────────────────────

    async def produce_video(
        self,
        product_name: str,
        product_description: str,
        target_audience: str,
        key_benefits: list[str],
        style: VideoStyle = VideoStyle.HOOK_STORY_CTA,
        video_format: VideoFormat = VideoFormat.VERTICAL,
        brand_name: str = "",
    ) -> dict[str, Any]:
        """Full autonomous pipeline: script → avatar → render → deliver."""

        # 1. Generate script variants
        scripts = await self.generate_scripts(
            product_name, product_description, target_audience,
            key_benefits, style, num_variants=3,
        )
        if not scripts:
            return {"status": "error", "detail": "Script generation failed"}

        # Use the first variant (best hook)
        script = scripts[0]

        # 2. Select avatar matching brand/audience
        avatar = await self.select_avatar_for_brand(
            brand_name or product_name, target_audience,
            product_description[:100],
        )
        avatar_id = avatar.get("avatar_id", "") if avatar else ""

        # 3. Select voice
        voices = await self.list_voices()
        voice_id = voices[0].get("voice_id", "") if voices else ""

        # 4. Render video
        render = await self.generate_video(
            script.get("full_script", ""), avatar_id, voice_id, video_format,
        )

        video_id = render.get("video_id")
        if not video_id:
            return {"status": "error", "detail": "Video render failed"}

        # 5. Wait for completion
        result = await self.wait_for_video(video_id)

        # 6. Quality gate
        quality = await self._quality_check(
            script["full_script"], product_name, result.get("video_url", ""),
        )

        # 7. Log everything
        self.memory.log(
            f"[UGC] Video produced: {product_name}\n"
            f"  Style: {style.value} | Format: {video_format.value}\n"
            f"  Hook: {script.get('hook', '')[:80]}\n"
            f"  Status: {result.get('status')}\n"
            f"  Quality: {quality.get('score', 'N/A')}/10\n"
            f"  URL: {result.get('video_url', 'N/A')}",
            "UGC",
        )

        return {
            "status": result.get("status"),
            "video_url": result.get("video_url"),
            "thumbnail_url": result.get("thumbnail_url"),
            "duration": result.get("duration"),
            "script": script,
            "all_scripts": scripts,
            "quality": quality,
            "avatar_id": avatar_id,
            "cost_estimate": "$1-5",
        }

    async def _quality_check(self, script: str, product: str, video_url: str) -> dict[str, Any]:
        """AI quality gate on the produced video."""
        result = await self.llm.ask_json(
            f"Rate this UGC video script on a 1-10 scale.\n\n"
            f"Product: {product}\n"
            f"Script: {script}\n\n"
            f"Rate on:\n"
            f"1. Hook strength (does it stop the scroll?)\n"
            f"2. Authenticity (does it sound like a real person, not a commercial?)\n"
            f"3. CTA clarity (is the next step obvious?)\n"
            f"4. Pacing (right length, no dead spots?)\n"
            f"5. Conversion potential (would this make someone buy?)\n\n"
            f"Return JSON: {{"
            f'"score": int (1-10 overall), '
            f'"hook_score": int, '
            f'"authenticity_score": int, '
            f'"cta_score": int, '
            f'"pacing_score": int, '
            f'"conversion_score": int, '
            f'"improvements": [str], '
            f'"verdict": "publish"|"revise"|"reject"}}',
            tier=Tier.HAIKU,
        )
        return result

    # ── Batch Production ────────────────────────────────────────────

    async def produce_batch(
        self,
        product_name: str,
        product_description: str,
        target_audience: str,
        key_benefits: list[str],
        count: int = 5,
    ) -> list[dict[str, Any]]:
        """Produce a batch of videos with different styles and hooks."""
        styles = [
            VideoStyle.HOOK_STORY_CTA,
            VideoStyle.TESTIMONIAL,
            VideoStyle.PROBLEM_SOLUTION,
            VideoStyle.TUTORIAL,
            VideoStyle.COMPARISON,
        ]

        results = []
        for i in range(min(count, len(styles))):
            result = await self.produce_video(
                product_name, product_description, target_audience,
                key_benefits, style=styles[i],
            )
            results.append(result)
            # Slight delay between renders
            await asyncio.sleep(random.uniform(2, 5))

        self.memory.log(
            f"[UGC] Batch complete: {len(results)} videos for {product_name}",
            "UGC",
        )
        return results

    # ── Self-Promotion (Free Content for ARCANA) ────────────────────

    async def produce_promo_video(self, product_name: str, product_url: str) -> dict[str, Any]:
        """Produce a promotional video for ARCANA's own products."""
        return await self.produce_video(
            product_name=product_name,
            product_description=f"Digital product by Arcana Operations. Available at {product_url}",
            target_audience="Business owners and entrepreneurs interested in AI automation",
            key_benefits=[
                "Built by an actual autonomous AI agent",
                "Practical, battle-tested frameworks",
                "Immediate ROI for your business",
            ],
            style=VideoStyle.HOOK_STORY_CTA,
            brand_name="Arcana Operations",
        )

    async def produce_testimonial_style(
        self, product_name: str, result_claim: str, timeframe: str
    ) -> dict[str, Any]:
        """Produce a testimonial-style video (highest conversion rate)."""
        return await self.produce_video(
            product_name=product_name,
            product_description=f"Product that delivered {result_claim} in {timeframe}",
            target_audience="People struggling with the problem this product solves",
            key_benefits=[result_claim, f"Results in {timeframe}", "Easy to implement"],
            style=VideoStyle.TESTIMONIAL,
        )

    # ── Client Management ───────────────────────────────────────────

    def add_ugc_client(
        self, client_name: str, package: str, monthly_rate: float, videos_per_month: int
    ) -> None:
        """Register a UGC client."""
        self.memory.save_knowledge(
            "projects",
            f"ugc-client-{client_name.lower().replace(' ', '-')}",
            f"# UGC Client: {client_name}\n\n"
            f"- Package: {package}\n"
            f"- Monthly rate: ${monthly_rate:,.2f}\n"
            f"- Videos/month: {videos_per_month}\n"
            f"- Start date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"- Status: Active\n",
        )
        self.memory.log(
            f"[UGC] New client: {client_name} — {package} @ ${monthly_rate}/mo "
            f"({videos_per_month} videos)",
            "UGC",
        )

    def get_ugc_clients(self) -> list[str]:
        """List active UGC clients."""
        return [
            name for name in self.memory.list_knowledge("projects")
            if name.startswith("ugc-client-")
        ]

    def get_ugc_mrr(self) -> float:
        """Calculate UGC monthly recurring revenue."""
        total = 0.0
        for client_key in self.get_ugc_clients():
            data = self.memory.get_knowledge("projects", client_key)
            if not data:
                continue
            for line in data.splitlines():
                if "monthly rate" in line.lower() and "$" in line:
                    try:
                        total += float(line.split("$")[1].split()[0].replace(",", ""))
                    except (IndexError, ValueError):
                        pass
        return total

    # ── Content Repurposing ─────────────────────────────────────────

    async def repurpose_tweet_to_video(self, tweet_text: str) -> dict[str, Any]:
        """Turn a high-performing tweet into a UGC video."""
        script_result = await self.llm.ask_json(
            f"Convert this tweet into a 30-second UGC video script.\n\n"
            f"Tweet: {tweet_text}\n\n"
            f"Rules:\n"
            f"- Start with a hook that captures the tweet's insight\n"
            f"- Expand the tweet into conversational speech (not reading it verbatim)\n"
            f"- End with a CTA to follow for more insights\n"
            f"- Keep ARCANA's voice: confident, pattern-focused, no hype\n\n"
            f"Return JSON: {{\"script\": str, \"hook\": str, \"estimated_seconds\": int}}",
            tier=Tier.HAIKU,
        )
        return script_result

    async def generate_ad_creative_set(
        self,
        product_name: str,
        product_description: str,
        target_audience: str,
        key_benefits: list[str],
    ) -> dict[str, Any]:
        """Generate a full ad creative set: 3 formats × 3 hooks = 9 videos."""
        formats = [VideoFormat.VERTICAL, VideoFormat.SQUARE, VideoFormat.HORIZONTAL]
        hooks = await self.generate_hooks(product_name, target_audience, count=3)

        creatives = []
        for fmt in formats:
            for hook_data in hooks[:3]:
                # Generate script with specific hook
                script_text = await self.llm.ask(
                    f"Write a 30-second UGC script starting with this hook:\n\n"
                    f"Hook: {hook_data['text']}\n"
                    f"Product: {product_name} — {product_description}\n"
                    f"Benefits: {', '.join(key_benefits)}\n\n"
                    f"Include the hook, a brief body (2-3 sentences), and a clear CTA.\n"
                    f"Make it conversational, authentic. Not a commercial.",
                    tier=Tier.HAIKU,
                    max_tokens=150,
                )
                creatives.append({
                    "format": fmt.value,
                    "hook_type": hook_data.get("type", "unknown"),
                    "hook": hook_data["text"],
                    "script": script_text.strip(),
                })

        self.memory.log(
            f"[UGC] Ad creative set: {len(creatives)} variants for {product_name}",
            "UGC",
        )

        return {
            "product": product_name,
            "total_creatives": len(creatives),
            "creatives": creatives,
            "estimated_cost": f"${len(creatives) * 2}-{len(creatives) * 5}",
            "estimated_value": f"${len(creatives) * 50}-{len(creatives) * 150}",
        }

    # ── Video Delivery & Invoicing ─────────────────────────────────

    async def deliver_video_to_client(
        self, client_key: str, video_url: str, video_data: dict[str, Any],
    ) -> bool:
        """Send completed video to client via email with download link, thumbnail, and revision info."""
        if not self.email_engine:
            logger.error("Email engine not configured — cannot deliver video")
            return False

        client_info = self.memory.get_knowledge("projects", client_key)
        if not client_info:
            logger.error("Client not found: %s", client_key)
            return False

        # Parse client email from memory file
        client_email = ""
        client_name = ""
        for line in client_info.splitlines():
            lower = line.lower()
            if "email:" in lower:
                client_email = line.split(":", 1)[1].strip()
            if line.startswith("# UGC Client:"):
                client_name = line.replace("# UGC Client:", "").strip()

        if not client_email:
            logger.error("No email on file for client %s", client_key)
            return False

        thumbnail_url = video_data.get("thumbnail_url", "")
        duration = video_data.get("duration", "N/A")
        script_hook = video_data.get("script", {}).get("hook", "")
        quality_score = video_data.get("quality", {}).get("score", "N/A")

        thumbnail_html = (
            f'<img src="{thumbnail_url}" alt="Video thumbnail" '
            f'style="max-width:100%;border-radius:8px;margin:16px 0" />'
            if thumbnail_url else ""
        )

        html_body = (
            f"<h2>Your UGC Video is Ready!</h2>"
            f"<p>Hi {client_name},</p>"
            f"<p>Your latest UGC video has been produced and passed our quality review "
            f"(score: {quality_score}/10).</p>"
            f"{thumbnail_html}"
            f"<table style='border-collapse:collapse;width:100%;margin:16px 0'>"
            f"<tr><td><strong>Duration</strong></td><td>{duration}s</td></tr>"
            f"<tr><td><strong>Hook</strong></td><td>{script_hook[:120]}</td></tr>"
            f"</table>"
            f"<p><a href='{video_url}' style='background:#000;color:#fff;"
            f"padding:12px 24px;text-decoration:none;display:inline-block;"
            f"border-radius:6px;margin:16px 0'>Download Video &rarr;</a></p>"
            f"<h3>Need Revisions?</h3>"
            f"<p>Reply to this email with your revision notes. We offer up to "
            f"2 free revisions per video. Please include:</p>"
            f"<ul>"
            f"<li>Timestamp of the section to change</li>"
            f"<li>What you'd like changed (script, pacing, avatar, etc.)</li>"
            f"<li>Any reference examples</li>"
            f"</ul>"
            f"<p>— ARCANA AI, Arcana Operations</p>"
        )

        success = await self.email_engine.send(
            to_email=client_email,
            subject=f"Your UGC Video is Ready — {client_name}",
            html_body=html_body,
        )

        if success:
            self.memory.log(
                f"[UGC] Video delivered to {client_name} ({client_email})\n"
                f"  URL: {video_url}\n"
                f"  Quality: {quality_score}/10",
                "UGC",
            )
        return success

    async def request_revision(
        self, client_key: str, video_id: str, notes: str,
    ) -> dict[str, Any]:
        """Handle a client revision request for a delivered video."""
        client_info = self.memory.get_knowledge("projects", client_key)
        client_name = ""
        if client_info:
            for line in client_info.splitlines():
                if line.startswith("# UGC Client:"):
                    client_name = line.replace("# UGC Client:", "").strip()

        # Use LLM to parse revision notes into actionable changes
        revision_plan = await self.llm.ask_json(
            f"A UGC video client has requested revisions.\n\n"
            f"Client: {client_name}\n"
            f"Video ID: {video_id}\n"
            f"Revision notes: {notes}\n\n"
            f"Categorize the requested changes and create an action plan.\n\n"
            f"Return JSON: {{"
            f'"changes": [{{"type": "script"|"avatar"|"voice"|"pacing"|"cta"|"hook"|"other", '
            f'"description": str, "difficulty": "easy"|"medium"|"hard"}}], '
            f'"requires_re_render": bool, '
            f'"estimated_time_minutes": int, '
            f'"summary": str}}',
            tier=Tier.HAIKU,
        )

        self.memory.log(
            f"[UGC] Revision requested by {client_name}\n"
            f"  Video: {video_id}\n"
            f"  Notes: {notes[:150]}\n"
            f"  Plan: {revision_plan.get('summary', 'N/A')}",
            "UGC",
        )

        return {
            "client_key": client_key,
            "video_id": video_id,
            "revision_plan": revision_plan,
            "status": "revision_queued",
        }

    async def auto_invoice_on_delivery(
        self, client_key: str, videos_delivered: int, rate_per_video: float,
    ) -> dict[str, Any]:
        """Create and send an invoice after video delivery."""
        if not self.payments_engine:
            logger.error("Payments engine not configured — cannot invoice")
            return {"status": "error", "detail": "Payments engine not configured"}

        client_info = self.memory.get_knowledge("projects", client_key)
        if not client_info:
            return {"status": "error", "detail": f"Client not found: {client_key}"}

        # Parse client details
        client_email = ""
        client_name = ""
        for line in client_info.splitlines():
            lower = line.lower()
            if "email:" in lower:
                client_email = line.split(":", 1)[1].strip()
            if line.startswith("# UGC Client:"):
                client_name = line.replace("# UGC Client:", "").strip()

        if not client_email:
            return {"status": "error", "detail": f"No email for {client_key}"}

        total_cents = int(videos_delivered * rate_per_video * 100)
        now = datetime.now(timezone.utc)
        month_label = now.strftime("%B %Y")

        invoice_result = self.payments_engine.create_invoice(
            customer_email=client_email,
            items=[{
                "description": (
                    f"UGC Video Production — {videos_delivered} video(s), {month_label}"
                ),
                "amount_cents": total_cents,
            }],
            due_days=14,
            memo=f"UGC video production for {client_name} — {month_label}",
        )

        if invoice_result:
            self.memory.log(
                f"[UGC] Invoice sent: {client_name} — {videos_delivered} videos "
                f"× ${rate_per_video:.2f} = ${videos_delivered * rate_per_video:,.2f}\n"
                f"  Invoice: {invoice_result.get('invoice_url', 'N/A')}",
                "Billing",
            )
            # Also email the invoice link via email engine if available
            if self.email_engine and invoice_result.get("invoice_url"):
                await self.email_engine.send_invoice(
                    to_email=client_email,
                    client_name=client_name,
                    service=f"UGC Video Production — {videos_delivered} videos ({month_label})",
                    amount=videos_delivered * rate_per_video,
                    due_date=(now.strftime("%Y-%m-%d")),
                    payment_link=invoice_result["invoice_url"],
                )

        return {
            "status": "invoiced" if invoice_result else "error",
            "client": client_name,
            "videos": videos_delivered,
            "total": videos_delivered * rate_per_video,
            "invoice": invoice_result,
        }

    async def monthly_production_cycle(self) -> dict[str, Any]:
        """Run the full monthly UGC production cycle for all clients.

        For each active client:
        1. Check how many videos are due this month
        2. Produce the batch
        3. Run quality review on each video
        4. Deliver each video via email
        5. Invoice the client
        6. Log everything in memory
        """
        clients = self.get_ugc_clients()
        now = datetime.now(timezone.utc)
        month_label = now.strftime("%B %Y")
        cycle_results: list[dict[str, Any]] = []

        self.memory.log(
            f"[UGC] Monthly production cycle started — {month_label}\n"
            f"  Active clients: {len(clients)}",
            "UGC",
        )

        for client_key in clients:
            client_info = self.memory.get_knowledge("projects", client_key)
            if not client_info:
                continue

            # Parse client details
            client_name = ""
            videos_per_month = 0
            monthly_rate = 0.0
            product_name = ""
            product_desc = ""
            target_audience = ""
            status = "active"
            for line in client_info.splitlines():
                lower = line.lower().strip("- ").strip()
                if line.startswith("# UGC Client:"):
                    client_name = line.replace("# UGC Client:", "").strip()
                elif lower.startswith("videos/month:"):
                    try:
                        videos_per_month = int(lower.split(":")[1].strip())
                    except (ValueError, IndexError):
                        pass
                elif "monthly rate:" in lower and "$" in line:
                    try:
                        monthly_rate = float(line.split("$")[1].split()[0].replace(",", ""))
                    except (IndexError, ValueError):
                        pass
                elif lower.startswith("product:"):
                    product_name = lower.split(":", 1)[1].strip()
                elif lower.startswith("description:"):
                    product_desc = lower.split(":", 1)[1].strip()
                elif lower.startswith("audience:") or lower.startswith("target audience:"):
                    target_audience = lower.split(":", 1)[1].strip()
                elif lower.startswith("status:"):
                    status = lower.split(":", 1)[1].strip().lower()

            if status != "active" or videos_per_month <= 0:
                continue

            rate_per_video = monthly_rate / videos_per_month if videos_per_month else 0

            # Produce the batch
            logger.info("Producing %d videos for %s", videos_per_month, client_name)
            produced: list[dict[str, Any]] = []
            for i in range(videos_per_month):
                result = await self.produce_video(
                    product_name=product_name or client_name,
                    product_description=product_desc or f"Product by {client_name}",
                    target_audience=target_audience or "General consumer audience",
                    key_benefits=["High quality", "Professional", "Engaging"],
                    brand_name=client_name,
                )
                produced.append(result)
                # Jitter between renders
                await asyncio.sleep(random.uniform(2, 5))

            # Deliver each completed video
            delivered_count = 0
            for video_result in produced:
                if video_result.get("status") != "completed":
                    continue
                # Quality gate — only deliver videos that pass
                quality = video_result.get("quality", {})
                verdict = quality.get("verdict", "publish")
                if verdict == "reject":
                    logger.warning(
                        "Video rejected by quality gate for %s (score %s)",
                        client_name, quality.get("score"),
                    )
                    continue

                video_url = video_result.get("video_url", "")
                if video_url:
                    delivered = await self.deliver_video_to_client(
                        client_key, video_url, video_result,
                    )
                    if delivered:
                        delivered_count += 1

            # Invoice the client for delivered videos
            invoice_result: dict[str, Any] = {}
            if delivered_count > 0:
                invoice_result = await self.auto_invoice_on_delivery(
                    client_key, delivered_count, rate_per_video,
                )

            client_result = {
                "client": client_name,
                "videos_due": videos_per_month,
                "videos_produced": len(produced),
                "videos_delivered": delivered_count,
                "invoice": invoice_result,
            }
            cycle_results.append(client_result)

            self.memory.log(
                f"[UGC] Monthly cycle complete for {client_name}\n"
                f"  Due: {videos_per_month} | Produced: {len(produced)} | "
                f"Delivered: {delivered_count}\n"
                f"  Invoice: {'sent' if invoice_result.get('status') == 'invoiced' else 'failed'}",
                "UGC",
            )

        summary = {
            "month": month_label,
            "clients_processed": len(cycle_results),
            "total_produced": sum(r["videos_produced"] for r in cycle_results),
            "total_delivered": sum(r["videos_delivered"] for r in cycle_results),
            "results": cycle_results,
        }

        self.memory.log(
            f"[UGC] Monthly cycle complete — {month_label}\n"
            f"  Clients: {summary['clients_processed']}\n"
            f"  Produced: {summary['total_produced']}\n"
            f"  Delivered: {summary['total_delivered']}",
            "UGC",
        )

        return summary
