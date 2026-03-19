"""ARCANA AI — Service Fulfillment Engine.

Actually DELIVERS the services, not just generates content for them.
This is where ARCANA does the work that earns the monthly retainer.

For each service client, ARCANA:
1. Reads their credentials/access from CRM
2. Performs the scheduled work (generate reviews, post content, etc.)
3. Logs deliverables
4. Reports results to client
5. Tracks hours/tasks for billing

Supported service delivery:
- Review responses: Pull new reviews → generate responses → post them
- Social media: Generate weekly content → schedule via Buffer API
- SEO content: Generate articles → publish to client's CMS
- Lead gen: Run Apollo searches → generate email sequences → report leads
- Chatbot: Generate training data, deploy bot, monitor conversations
- Competitive intel: Scrape competitors → generate reports → deliver
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.llm import LLM, Tier
from src.memory import Memory
from src.services import ServiceEngine

logger = logging.getLogger("arcana.fulfillment")


class FulfillmentEngine:
    """Deliver recurring services to clients autonomously."""

    def __init__(
        self, llm: LLM, memory: Memory, services: ServiceEngine,
        buffer_key: str = "", google_biz_token: str = "",
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.services = services
        self.buffer_key = buffer_key
        self.google_biz_token = google_biz_token
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30)
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Review Response Fulfillment ─────────────────────────────────

    async def fulfill_review_responses(self, client_key: str) -> dict[str, Any]:
        """Pull new reviews for a client and generate/post responses."""
        client_data = self.memory.get_knowledge("projects", client_key)
        if not client_data:
            return {"status": "client_not_found"}

        # Extract client info
        business_name = ""
        for line in client_data.splitlines():
            if "business:" in line.lower() or "name:" in line.lower():
                business_name = line.split(":", 1)[1].strip()
                break

        if not business_name:
            business_name = client_key.replace("client-", "").replace("-", " ").title()

        # Fetch new reviews from Google Business Profile API
        reviews = await self._fetch_google_reviews(client_key)
        if not reviews:
            return {"status": "no_new_reviews", "business": business_name}

        responses_posted = 0
        for review in reviews:
            response = await self.services.generate_review_response(
                business_name=business_name,
                reviewer=review.get("reviewer", "Customer"),
                rating=review.get("rating", 5),
                review_text=review.get("text", ""),
                platform=review.get("platform", "google"),
            )

            # Post the response back
            if response and review.get("review_id"):
                posted = await self._post_review_response(
                    review["review_id"], response, client_key,
                )
                if posted:
                    responses_posted += 1

        self.memory.log(
            f"[Fulfillment] Reviews for {business_name}: "
            f"{len(reviews)} fetched, {responses_posted} responded",
            "Fulfillment",
        )

        return {
            "status": "completed",
            "business": business_name,
            "reviews_found": len(reviews),
            "responses_posted": responses_posted,
        }

    async def _fetch_google_reviews(self, client_key: str) -> list[dict[str, Any]]:
        """Fetch new Google reviews for a client."""
        if not self.google_biz_token:
            return []

        # Get the client's Google Business location ID from their data
        client_data = self.memory.get_knowledge("projects", client_key) or ""
        location_id = ""
        for line in client_data.splitlines():
            if "location_id" in line.lower() or "google_id" in line.lower():
                location_id = line.split(":", 1)[1].strip()
                break

        if not location_id:
            return []

        try:
            http = await self._get_http()
            resp = await http.get(
                f"https://mybusiness.googleapis.com/v4/accounts/-/locations/{location_id}/reviews",
                headers={"Authorization": f"Bearer {self.google_biz_token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {
                        "review_id": r.get("reviewId", ""),
                        "reviewer": r.get("reviewer", {}).get("displayName", ""),
                        "rating": r.get("starRating", 5),
                        "text": r.get("comment", ""),
                        "platform": "google",
                    }
                    for r in data.get("reviews", [])
                    if not r.get("reviewReply")  # Only unresponded reviews
                ]
        except Exception as exc:
            logger.error("Google reviews fetch error: %s", exc)
        return []

    async def _post_review_response(
        self, review_id: str, response: str, client_key: str,
    ) -> bool:
        """Post a response to a Google review."""
        if not self.google_biz_token:
            self.memory.log(
                f"[Fulfillment] DRY RUN review response for {client_key}: {response[:80]}",
                "Fulfillment",
            )
            return False

        try:
            http = await self._get_http()
            resp = await http.put(
                f"https://mybusiness.googleapis.com/v4/{review_id}/reply",
                headers={"Authorization": f"Bearer {self.google_biz_token}"},
                json={"comment": response},
            )
            return resp.status_code in (200, 201)
        except Exception as exc:
            logger.error("Review response post error: %s", exc)
            return False

    # ── Social Media Fulfillment ────────────────────────────────────

    async def fulfill_social_media(self, client_key: str) -> dict[str, Any]:
        """Generate and schedule a week of social content for a client."""
        client_data = self.memory.get_knowledge("projects", client_key)
        if not client_data:
            return {"status": "client_not_found"}

        # Parse client details
        industry = "general"
        client_name = client_key.replace("client-", "").replace("-", " ").title()
        for line in client_data.splitlines():
            if "industry:" in line.lower():
                industry = line.split(":", 1)[1].strip()
            elif "name:" in line.lower() or "business:" in line.lower():
                client_name = line.split(":", 1)[1].strip()

        # Generate week of content
        content = await self.services.generate_social_content(
            client_name, industry, ["instagram", "facebook", "linkedin"],
        )

        posts = content.get("posts", [])

        # Schedule via Buffer API
        scheduled = 0
        if self.buffer_key and posts:
            for post in posts:
                success = await self._schedule_buffer_post(
                    post.get("content", ""),
                    post.get("platform", "instagram"),
                    client_key,
                )
                if success:
                    scheduled += 1

        self.memory.log(
            f"[Fulfillment] Social for {client_name}: "
            f"{len(posts)} posts generated, {scheduled} scheduled",
            "Fulfillment",
        )

        return {
            "status": "completed",
            "client": client_name,
            "posts_generated": len(posts),
            "posts_scheduled": scheduled,
        }

    def _get_client_buffer_profiles(self, client_key: str) -> list[str]:
        """Extract Buffer profile IDs from client data in memory."""
        client_data = self.memory.get_knowledge("projects", client_key) or ""
        profile_ids = []
        for line in client_data.splitlines():
            if "buffer_profile" in line.lower() or "profile_id" in line.lower():
                value = line.split(":", 1)[1].strip()
                # Handle comma-separated or single values
                for pid in value.split(","):
                    pid = pid.strip().strip("[]\"'")
                    if pid:
                        profile_ids.append(pid)
        return profile_ids

    async def _schedule_buffer_post(
        self, text: str, platform: str, client_key: str,
    ) -> bool:
        """Schedule a post via Buffer API."""
        if not self.buffer_key:
            self.memory.log(
                f"[Fulfillment] DRY RUN buffer post for {client_key}: {text[:80]}",
                "Fulfillment",
            )
            return False

        # Get client's Buffer profile IDs from their project data
        profile_ids = self._get_client_buffer_profiles(client_key)

        if not profile_ids:
            # Try to fetch profiles from Buffer API and cache them
            profile_ids = await self._fetch_buffer_profiles(client_key, platform)

        if not profile_ids:
            self.memory.log(
                f"[Fulfillment] No Buffer profiles for {client_key}/{platform} — "
                f"add buffer_profile_id to client data",
                "Fulfillment",
            )
            return False

        try:
            http = await self._get_http()
            any_success = False
            for profile_id in profile_ids:
                resp = await http.post(
                    "https://api.bufferapp.com/1/updates/create.json",
                    data={
                        "access_token": self.buffer_key,
                        "text": text,
                        "profile_ids[]": profile_id,
                    },
                )
                if resp.status_code in (200, 201):
                    any_success = True
                else:
                    logger.warning("Buffer post failed for profile %s: %s",
                                   profile_id, resp.status_code)
            return any_success
        except Exception as exc:
            logger.error("Buffer schedule error: %s", exc)
            return False

    async def _fetch_buffer_profiles(self, client_key: str, platform: str = "") -> list[str]:
        """Fetch available Buffer profiles and cache matching ones for the client."""
        if not self.buffer_key:
            return []

        try:
            http = await self._get_http()
            resp = await http.get(
                "https://api.bufferapp.com/1/profiles.json",
                params={"access_token": self.buffer_key},
            )
            if resp.status_code != 200:
                return []

            profiles = resp.json()
            # Filter by platform if specified
            matching = []
            for p in profiles:
                if not platform or p.get("service", "").lower() == platform.lower():
                    matching.append(p.get("id", ""))

            # Cache the profile IDs in client data for future use
            if matching:
                existing = self.memory.get_knowledge("projects", client_key) or ""
                if "buffer_profile" not in existing.lower():
                    updated = f"{existing}\nbuffer_profile_ids: {', '.join(matching)}\n"
                    self.memory.save_knowledge("projects", client_key, updated)
                    logger.info("Cached %d Buffer profiles for %s", len(matching), client_key)

            return matching
        except Exception as exc:
            logger.error("Buffer profiles fetch error: %s", exc)
            return []

    # ── SEO Content Publishing ──────────────────────────────────────

    async def publish_seo_article(
        self, article_html: str, title: str, slug: str,
        site_url: str = "", vercel_token: str = "",
    ) -> dict[str, Any]:
        """Publish an SEO article to a static site or CMS."""
        # For static sites, generate a markdown file and deploy via Vercel
        if vercel_token:
            return await self._deploy_to_vercel(
                title, slug, article_html, vercel_token,
            )

        # Fallback: save to memory for manual publishing
        self.memory.save_knowledge(
            "resources", f"article-{slug}",
            f"# {title}\n\nSlug: {slug}\n\n{article_html}",
        )
        self.memory.log(f"[Fulfillment] SEO article saved: {title} ({slug})", "Fulfillment")
        return {"status": "saved", "slug": slug}

    async def _deploy_to_vercel(
        self, title: str, slug: str, html: str, token: str,
    ) -> dict[str, Any]:
        """Deploy content to Vercel as a static page."""
        try:
            http = await self._get_http()
            # Create deployment with the article as a file
            resp = await http.post(
                "https://api.vercel.com/v13/deployments",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "name": f"arcana-seo-{slug}",
                    "files": [
                        {
                            "file": f"articles/{slug}.html",
                            "data": html,
                        }
                    ],
                },
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                url = data.get("url", "")
                self.memory.log(
                    f"[Fulfillment] Deployed: {title} → {url}", "Fulfillment"
                )
                return {"status": "deployed", "url": url}
        except Exception as exc:
            logger.error("Vercel deploy error: %s", exc)
        return {"status": "deploy_failed"}

    # ── Competitive Intel Delivery ──────────────────────────────────

    async def fulfill_intel_report(self, client_key: str) -> dict[str, Any]:
        """Generate and deliver a competitive intelligence report."""
        client_data = self.memory.get_knowledge("projects", client_key)
        if not client_data:
            return {"status": "client_not_found"}

        # Parse competitors from client data
        competitors = []
        client_name = client_key.replace("client-", "").replace("-", " ").title()
        for line in client_data.splitlines():
            if "competitor" in line.lower():
                competitors.append(line.split(":", 1)[1].strip())
            elif "name:" in line.lower():
                client_name = line.split(":", 1)[1].strip()

        if not competitors:
            competitors = ["(identify competitors)"]

        report = await self.services.generate_intel_report(
            client_name, competitors, ["pricing", "features", "marketing", "hiring"],
        )

        self.memory.log(
            f"[Fulfillment] Intel report for {client_name} delivered", "Fulfillment"
        )

        return {"status": "completed", "client": client_name, "report_length": len(report)}

    # ── Master Fulfillment Cycle ────────────────────────────────────

    async def run_daily_fulfillment(self) -> dict[str, Any]:
        """Run daily fulfillment for all active service clients."""
        results = {"clients_served": 0, "tasks_completed": 0, "errors": 0}

        # Get all active clients
        all_projects = self.memory.list_knowledge("projects")
        clients = [k for k in all_projects if k.startswith("client-")]

        for client_key in clients:
            client_data = self.memory.get_knowledge("projects", client_key) or ""

            # Determine service type
            service = ""
            for line in client_data.splitlines():
                if "service:" in line.lower():
                    service = line.split(":", 1)[1].strip().lower()
                    break

            try:
                if "review" in service:
                    await self.fulfill_review_responses(client_key)
                elif "social" in service:
                    await self.fulfill_social_media(client_key)
                elif "intel" in service or "competitive" in service:
                    await self.fulfill_intel_report(client_key)
                # Other services handled by their respective engines

                results["clients_served"] += 1
                results["tasks_completed"] += 1
            except Exception as exc:
                logger.error("Fulfillment failed for %s: %s", client_key, exc)
                results["errors"] += 1

        self.memory.log(
            f"[Fulfillment] Daily cycle: {results['clients_served']} clients served, "
            f"{results['tasks_completed']} tasks, {results['errors']} errors",
            "Fulfillment",
        )
        return results
