"""ARCANA AI — Proactive Opportunity Scanner.

THIS IS THE MONEY ENGINE. Without this, ARCANA sits idle. With this, ARCANA
hunts 24/7 across every platform, finds people who need its services, responds
with value, and converts them into paying clients.

Scan sources:
1. X/Twitter — People asking for services, expressing pain points, seeking tools
2. Reddit — Subreddits where businesses ask for help
3. Freelance platforms — Upwork, Fiverr, Contra RSS feeds for matching gigs
4. Google Alerts — Industry keywords, competitor mentions, service requests
5. Product Hunt — New launches that need UGC, marketing, chatbots
6. Hacker News — "Ask HN" and "Show HN" with relevant pain points
7. Craigslist/Gigs — Local Portland + remote gigs ARCANA can complete
8. LinkedIn (via X crossposts) — B2B decision makers expressing needs

Scan strategies:
- INBOUND HUNTING: Find people actively asking for what ARCANA sells
- PAIN SIGNAL DETECTION: Find businesses expressing problems ARCANA solves
- COMPETITOR DISPLACEMENT: Find people unhappy with current providers
- TRIGGER EVENT MONITORING: New funding, launches, hiring = budget available
- WATERING HOLE MINING: Monitor communities where ideal clients hang out
- CONTENT ENGAGEMENT: Reply to viral posts with value to build pipeline

Pipeline management:
- Score → Qualify → Auto-respond → Nurture → Convert → Fulfill
- Every opportunity tracked in memory with conversion status
- Nightly analysis: what's working, what to change, pipeline value

Target: 50+ qualified opportunities per day → 5% conversion → 2-3 new clients/day.
At $500 avg MRR per client = $30-45K/mo from scanning alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from datetime import datetime, timezone
from typing import Any

import httpx

from src.llm import LLM, Tier
from src.memory import Memory
from src.notify import Notifier
from src.x_client import XClient

logger = logging.getLogger("arcana.scanner")


# ════════════════════════════════════════════════════════════════════
# HUNT QUERIES — The lifeblood of the scanner. Rotated each cycle.
# ════════════════════════════════════════════════════════════════════

X_HUNT_QUERIES: dict[str, list[str]] = {
    # ── UGC Video Production ($50-3000/mo) ──────────────────────
    "ugc": [
        '"need UGC" OR "looking for UGC" OR "UGC creator" OR "need video ads"',
        '"UGC videos" ("looking for" OR "need" OR "hiring" OR "anyone know")',
        '"product video" ("who can" OR "need help" OR "recommend")',
        '"TikTok ads" ("need" OR "looking for" OR "who makes" OR "creator")',
        '"video testimonial" ("need" OR "looking for" OR "how to get")',
        '"ad creative" ("need" OR "looking for" OR "who does" OR "agency")',
        '"Reels" ("need help" OR "who creates" OR "looking for creator")',
        '"user generated content" ("need" OR "looking for" OR "how to")',
    ],

    # ── AI Chatbot ($300-1000/mo) ───────────────────────────────
    "chatbot": [
        '"need a chatbot" OR "looking for chatbot" OR "chatbot for my"',
        '"customer support bot" OR "AI chatbot" ("build" OR "need" OR "looking")',
        '"website chat" ("need" OR "want" OR "looking for" OR "how to add")',
        '"automate support" OR "support tickets" ("too many" OR "overwhelmed")',
        '"intercom alternative" OR "zendesk alternative" OR "drift alternative"',
        '"chatbot developer" OR "chatbot agency" OR "build me a bot"',
    ],

    # ── Social Media Management ($500-5000/mo) ──────────────────
    "social": [
        '"need social media manager" OR "social media help" OR "manage my social"',
        '"content creator needed" OR "need someone to post" OR "social media management"',
        '"hiring social media" OR "social media for my business"',
        '"posting consistently" ("struggle" OR "hard" OR "don\'t have time")',
        '"social media strategy" ("need" OR "help" OR "looking for")',
        '"Instagram growth" ("need help" OR "struggling" OR "how to")',
        '"Twitter management" OR "X management" ("need" OR "looking for")',
    ],

    # ── Lead Gen / Cold Email ($500-2000/mo) ────────────────────
    "leadgen": [
        '"need leads" OR "lead generation" OR "cold email" OR "need more clients"',
        '"email marketing help" OR "outbound sales" OR "B2B leads"',
        '"need appointments" OR "book more calls" OR "sales pipeline help"',
        '"cold outreach" ("help" OR "need" OR "agency" OR "tool")',
        '"lead gen agency" OR "appointment setting" ("looking for" OR "need")',
        '"not enough clients" OR "need more customers" OR "how to get clients"',
    ],

    # ── Review Management ($29-59/mo/location) ──────────────────
    "reviews": [
        '"bad reviews" OR "negative reviews" OR "review management"',
        '"Google reviews" ("help" OR "need" OR "how to" OR "respond")',
        '"Yelp reviews" ("help" OR "need" OR "respond to")',
        '"reputation management" ("need" OR "looking for" OR "help")',
        '"online reputation" ("damaged" OR "need help" OR "fix")',
    ],

    # ── SEO ($1500-5000/mo) ─────────────────────────────────────
    "seo": [
        '"need SEO" OR "SEO help" OR "website not ranking"',
        '"SEO agency" ("looking for" OR "recommend" OR "need")',
        '"organic traffic" ("need more" OR "dropped" OR "help")',
        '"Google ranking" ("dropped" OR "need help" OR "improve")',
        '"content marketing" ("need" OR "help" OR "agency" OR "looking for")',
    ],

    # ── AI Consulting ($2000-10000/mo) ──────────────────────────
    "consulting": [
        '"AI consulting" OR "business automation" OR "automate my"',
        '"need AI help" OR "AI strategy" OR "implement AI"',
        '"streamline operations" OR "too much manual work"',
        '"AI developer" ("looking for" OR "need" OR "hiring")',
        '"digital transformation" ("need" OR "help" OR "consulting")',
        '"workflow automation" ("need" OR "help" OR "looking for")',
    ],

    # ── Competitor Displacement ─────────────────────────────────
    "displacement": [
        '"terrible agency" OR "fired my agency" OR "bad freelancer"',
        '"waste of money" ("agency" OR "freelancer" OR "marketing")',
        '"looking for new" ("agency" OR "developer" OR "marketer")',
        '"disappointed with" ("agency" OR "freelancer" OR "results")',
        '"overcharged" ("agency" OR "freelancer" OR "developer")',
        '"anyone better than" OR "alternative to my current"',
    ],

    # ── Trigger Events (Budget Just Freed Up) ───────────────────
    "triggers": [
        '"just raised" ("funding" OR "seed" OR "series") AND ("looking for" OR "hiring")',
        '"just launched" AND ("need" OR "looking for" OR "marketing")',
        '"scaling" AND ("need help" OR "looking for" OR "hiring")',
        '"growing fast" AND ("need" OR "overwhelmed" OR "hiring")',
        '"new business" AND ("need" OR "website" OR "marketing" OR "social media")',
    ],

    # ── Watering Holes (Where Ideal Clients Hang Out) ───────────
    "watering_holes": [
        '"small business owner" AND ("struggle" OR "help" OR "advice" OR "tip")',
        '"ecommerce" AND ("struggling" OR "need more sales" OR "conversion")',
        '"DTC brand" AND ("need" OR "looking for" OR "help")',
        '"startup founder" AND ("need" OR "looking for" OR "marketing")',
        '"agency owner" AND ("need" OR "white label" OR "outsource")',
        '"Shopify store" AND ("need" OR "help" OR "marketing" OR "struggling")',
    ],
}

# ── Pain signal queries (outbound prospecting) ──────────────────
PAIN_QUERIES = [
    '"our reviews" ("terrible" OR "help" OR "struggling")',
    '"our social media" ("dead" OR "neglected" OR "need to")',
    '"hate making content" OR "content is so hard" OR "no time for content"',
    '"wish I had" ("chatbot" OR "automation" OR "AI" OR "assistant")',
    '"small business" ("overwhelmed" OR "drowning" OR "need help")',
    '"spending too much time" ("social media" OR "emails" OR "content" OR "support")',
    '"manually" AND ("tired of" OR "wasting time" OR "every day")',
    '"can\'t afford" ("agency" OR "marketing" OR "developer") AND "but need"',
    '"doing everything myself" OR "wearing too many hats" OR "no team"',
    '"losing customers" OR "losing clients" OR "churn" AND ("help" OR "how to")',
]

# ── Reddit subreddits to monitor ────────────────────────────────
REDDIT_SUBREDDITS = [
    "smallbusiness",
    "Entrepreneur",
    "ecommerce",
    "startups",
    "digital_marketing",
    "socialmedia",
    "SEO",
    "PPC",
    "SaaS",
    "freelance",
    "marketing",
    "webdev",
    "artificial",
    "ChatGPT",
]

# ── Freelance platform search terms ─────────────────────────────
FREELANCE_SEARCH_TERMS = [
    "AI chatbot development",
    "UGC video production",
    "social media management",
    "cold email automation",
    "AI content writing",
    "review management",
    "lead generation",
    "AI agent development",
    "business process automation",
    "SEO content writing",
    "TikTok ad creative",
    "email marketing automation",
    "customer support automation",
    "product demo video",
    "brand video content",
]

# ── Service pricing (for qualification) ─────────────────────────
SERVICE_PRICING = {
    "ugc": {"min": 50, "max": 3000, "avg_mrr": 600},
    "chatbot": {"min": 300, "max": 1000, "avg_mrr": 500},
    "social": {"min": 500, "max": 5000, "avg_mrr": 1500},
    "leadgen": {"min": 500, "max": 2000, "avg_mrr": 1000},
    "reviews": {"min": 29, "max": 59, "avg_mrr": 45},
    "seo": {"min": 1500, "max": 5000, "avg_mrr": 2500},
    "consulting": {"min": 2000, "max": 10000, "avg_mrr": 5000},
    "email": {"min": 500, "max": 2000, "avg_mrr": 1000},
    "intel": {"min": 500, "max": 5000, "avg_mrr": 2000},
}


class OpportunityScanner:
    """Proactively hunt for revenue opportunities across all channels.

    This is the engine that makes ARCANA proactive instead of passive.
    Without it, ARCANA waits for mentions. With it, ARCANA finds money.
    """

    def __init__(
        self, llm: LLM, memory: Memory, x: XClient, notifier: Notifier,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.x = x
        self.notifier = notifier
        self._http: httpx.AsyncClient | None = None

        # State tracking
        self._query_rotation: dict[str, int] = {cat: 0 for cat in X_HUNT_QUERIES}
        self._pain_query_index = 0
        self._seen_ids: set[str] = set()  # Dedup across cycles
        self._cycle_count = 0

        # Metrics (reset nightly)
        self.metrics = {
            "opportunities_found": 0,
            "auto_responded": 0,
            "escalated_to_humans": 0,
            "proposals_sent": 0,
            "by_source": {},
            "by_service": {},
        }

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=30,
                headers={"User-Agent": "ARCANA-AI/1.0"},
                follow_redirects=True,
            )
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    def _dedup_key(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()[:12]

    def _is_seen(self, text: str) -> bool:
        key = self._dedup_key(text)
        if key in self._seen_ids:
            return True
        self._seen_ids.add(key)
        return False

    # ════════════════════════════════════════════════════════════════
    # MAIN SCAN CYCLE — runs every 15 minutes
    # ════════════════════════════════════════════════════════════════

    async def scan_cycle(self) -> dict[str, Any]:
        """Run one full scan cycle. This is called by the orchestrator every 15 min."""
        self._cycle_count += 1
        logger.info("=== OPPORTUNITY SCAN CYCLE #%d ===", self._cycle_count)

        results = {
            "cycle": self._cycle_count,
            "x_inbound": 0,
            "x_pain": 0,
            "x_displacement": 0,
            "x_triggers": 0,
            "reddit": 0,
            "freelance": 0,
            "hn": 0,
            "total_found": 0,
            "auto_responded": 0,
            "escalated": 0,
        }

        # ── Phase 1: X Inbound Hunting (pick 2 service categories) ──
        categories = list(X_HUNT_QUERIES.keys())
        random.shuffle(categories)
        for cat in categories[:2]:
            r = await self._hunt_x_category(cat)
            results["x_inbound"] += r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)
            results["escalated"] += r.get("escalated", 0)
            await asyncio.sleep(random.uniform(3, 8))

        # ── Phase 2: Pain Signal Detection ──────────────────────────
        r = await self._hunt_pain_signals()
        results["x_pain"] = r.get("found", 0)
        results["auto_responded"] += r.get("responded", 0)

        # ── Phase 3: Competitor Displacement (every 3rd cycle) ──────
        if self._cycle_count % 3 == 0:
            r = await self._hunt_x_category("displacement")
            results["x_displacement"] = r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ── Phase 4: Trigger Events (every 4th cycle) ───────────────
        if self._cycle_count % 4 == 0:
            r = await self._hunt_x_category("triggers")
            results["x_triggers"] = r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ── Phase 5: Reddit Scanning (every 6th cycle) ──────────────
        if self._cycle_count % 6 == 0:
            r = await self._scan_reddit()
            results["reddit"] = r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ── Phase 6: Freelance Platforms (every 8th cycle) ──────────
        if self._cycle_count % 8 == 0:
            r = await self._scan_freelance()
            results["freelance"] = r.get("found", 0)

        # ── Phase 7: Hacker News (every 12th cycle) ─────────────────
        if self._cycle_count % 12 == 0:
            r = await self._scan_hackernews()
            results["hn"] = r.get("found", 0)

        # ── Tally ───────────────────────────────────────────────────
        results["total_found"] = (
            results["x_inbound"] + results["x_pain"] + results["x_displacement"]
            + results["x_triggers"] + results["reddit"] + results["freelance"]
            + results["hn"]
        )

        # Update metrics
        self.metrics["opportunities_found"] += results["total_found"]
        self.metrics["auto_responded"] += results["auto_responded"]
        self.metrics["escalated_to_humans"] += results["escalated"]

        # Log
        if results["total_found"] > 0:
            self.memory.log(
                f"[Scanner] Cycle #{self._cycle_count}: {results['total_found']} opportunities found, "
                f"{results['auto_responded']} auto-responded, {results['escalated']} escalated\n"
                f"  X inbound: {results['x_inbound']} | Pain: {results['x_pain']} | "
                f"Displacement: {results['x_displacement']} | Triggers: {results['x_triggers']}\n"
                f"  Reddit: {results['reddit']} | Freelance: {results['freelance']} | HN: {results['hn']}",
                "Scanner",
            )

        return results

    # ════════════════════════════════════════════════════════════════
    # X HUNTING
    # ════════════════════════════════════════════════════════════════

    async def _hunt_x_category(self, category: str) -> dict[str, Any]:
        """Hunt X for opportunities in a specific service category."""
        queries = X_HUNT_QUERIES.get(category, [])
        if not queries:
            return {"found": 0, "responded": 0, "escalated": 0}

        # Rotate through queries
        idx = self._query_rotation.get(category, 0)
        query = queries[idx % len(queries)]
        self._query_rotation[category] = idx + 1

        found = 0
        responded = 0
        escalated = 0

        try:
            tweets = await self.x.search_recent(query, max_results=10)
            for tweet in tweets:
                text = tweet.get("text", "")
                if self._is_seen(text):
                    continue

                opp = await self._qualify_opportunity(text, category)
                if not opp or not opp.get("is_opportunity"):
                    continue

                found += 1
                score = opp.get("score", 0)
                service = opp.get("service_match", category)

                # Track by service
                self.metrics["by_service"][service] = self.metrics["by_service"].get(service, 0) + 1
                self.metrics["by_source"]["x_" + category] = self.metrics["by_source"].get("x_" + category, 0) + 1

                # High-value → escalate to Ian/Tan
                if score >= 80:
                    await self.notifier.lead_alert(
                        f"HUNTED [{category}]",
                        f"{text[:100]}",
                        score,
                    )
                    escalated += 1

                # Auto-respond with value
                if opp.get("reply") and score >= 35:
                    await self.x.reply_to(tweet.get("id", ""), opp["reply"])
                    responded += 1
                    await asyncio.sleep(random.uniform(45, 120))

                # Save to pipeline
                self._save_opportunity(
                    source=f"x_{category}",
                    identifier=tweet.get("author_id", "") or self._dedup_key(text),
                    text=text,
                    score=score,
                    service=service,
                    details=opp,
                )

        except Exception as exc:
            logger.debug("X hunt [%s] failed: %s", category, exc)

        return {"found": found, "responded": responded, "escalated": escalated}

    async def _hunt_pain_signals(self) -> dict[str, Any]:
        """Find businesses expressing pain points ARCANA can solve."""
        query = PAIN_QUERIES[self._pain_query_index % len(PAIN_QUERIES)]
        self._pain_query_index += 1

        found = 0
        responded = 0

        try:
            tweets = await self.x.search_recent(query, max_results=10)
            for tweet in tweets:
                text = tweet.get("text", "")
                if self._is_seen(text):
                    continue

                opp = await self._qualify_pain_signal(text)
                if not opp or not opp.get("is_prospect"):
                    continue

                found += 1
                self.metrics["by_source"]["x_pain"] = self.metrics["by_source"].get("x_pain", 0) + 1

                if opp.get("empathy_reply") and opp.get("score", 0) >= 40:
                    await self.x.reply_to(tweet.get("id", ""), opp["empathy_reply"])
                    responded += 1
                    await asyncio.sleep(random.uniform(60, 180))

                self._save_opportunity(
                    source="x_pain",
                    identifier=tweet.get("author_id", "") or self._dedup_key(text),
                    text=text,
                    score=opp.get("score", 0),
                    service=opp.get("service_match", "general"),
                    details=opp,
                )

        except Exception as exc:
            logger.debug("Pain signal hunt failed: %s", exc)

        return {"found": found, "responded": responded}

    # ════════════════════════════════════════════════════════════════
    # REDDIT SCANNING
    # ════════════════════════════════════════════════════════════════

    async def _scan_reddit(self) -> dict[str, Any]:
        """Scan Reddit for business owners asking for help ARCANA can provide."""
        found = 0
        responded = 0

        # Pick 2 subreddits per cycle
        subs = random.sample(REDDIT_SUBREDDITS, min(2, len(REDDIT_SUBREDDITS)))

        for sub in subs:
            try:
                http = await self._get_http()
                resp = await http.get(
                    f"https://www.reddit.com/r/{sub}/new.json?limit=15",
                    headers={"User-Agent": "ARCANA-AI/1.0 (business-bot)"},
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                posts = data.get("data", {}).get("children", [])

                for post in posts:
                    pdata = post.get("data", {})
                    title = pdata.get("title", "")
                    body = pdata.get("selftext", "")[:500]
                    url = pdata.get("url", "")
                    full_text = f"{title} {body}"

                    if self._is_seen(full_text):
                        continue

                    opp = await self._qualify_reddit_post(title, body, sub)
                    if not opp or not opp.get("is_opportunity"):
                        continue

                    found += 1
                    self.metrics["by_source"]["reddit"] = self.metrics["by_source"].get("reddit", 0) + 1

                    # Save opportunity (don't auto-respond on Reddit — save for manual follow-up or DM)
                    self._save_opportunity(
                        source="reddit",
                        identifier=f"r/{sub}/{pdata.get('id', '')}",
                        text=full_text[:300],
                        score=opp.get("score", 0),
                        service=opp.get("service_match", "general"),
                        details={**opp, "url": url, "subreddit": sub},
                    )

                await asyncio.sleep(random.uniform(2, 5))

            except Exception as exc:
                logger.debug("Reddit scan r/%s failed: %s", sub, exc)

        return {"found": found, "responded": responded}

    async def _qualify_reddit_post(self, title: str, body: str, subreddit: str) -> dict[str, Any]:
        """Qualify a Reddit post as an opportunity."""
        result = await self.llm.ask_json(
            f"Evaluate if this Reddit post represents someone who needs ARCANA's services.\n\n"
            f"Subreddit: r/{subreddit}\n"
            f"Title: {title}\n"
            f"Body: {body[:400]}\n\n"
            f"Services: UGC ($50-3K/mo), chatbots ($300-1K/mo), social ($500-5K/mo), "
            f"lead gen ($500-2K/mo), reviews ($29-59/location), SEO ($1.5-5K/mo), "
            f"AI consulting ($2-10K/mo)\n\n"
            f"Return JSON: {{"
            f'"is_opportunity": bool, "score": int (0-100), "service_match": str, '
            f'"estimated_value": int, "comment_reply": str|null '
            f"(helpful Reddit comment — NO sales pitch, pure value)}}",
            tier=Tier.HAIKU,
            max_tokens=200,
        )
        return result

    # ════════════════════════════════════════════════════════════════
    # FREELANCE PLATFORM SCANNING
    # ════════════════════════════════════════════════════════════════

    async def _scan_freelance(self) -> dict[str, Any]:
        """Scan freelance platform RSS feeds and public listings."""
        found = 0

        term = random.choice(FREELANCE_SEARCH_TERMS)
        encoded_term = term.replace(" ", "%20")

        # Try multiple platform feeds
        feeds = [
            f"https://www.upwork.com/ab/feed/jobs/rss?q={encoded_term}&sort=recency",
            f"https://www.upwork.com/ab/feed/jobs/rss?q={encoded_term.replace('%20', '+')}&sort=recency",
        ]

        for feed_url in feeds:
            try:
                http = await self._get_http()
                resp = await http.get(feed_url)
                if resp.status_code != 200:
                    continue

                opps = await self._parse_freelance_feed(resp.text, term)
                for opp in opps:
                    if self._is_seen(opp.get("title", "")):
                        continue
                    found += 1
                    self.metrics["by_source"]["freelance"] = self.metrics["by_source"].get("freelance", 0) + 1

                    self._save_opportunity(
                        source="freelance",
                        identifier=opp.get("link", opp.get("title", "")[:50]),
                        text=f"{opp.get('title', '')} — {opp.get('description', '')[:200]}",
                        score=opp.get("fit_score", 50),
                        service=opp.get("service_match", "general"),
                        details=opp,
                    )
                break  # Stop after first successful feed

            except Exception as exc:
                logger.debug("Freelance feed failed: %s", exc)

        return {"found": found}

    async def _parse_freelance_feed(self, content: str, search_term: str) -> list[dict[str, Any]]:
        """Extract and qualify freelance listings from RSS/HTML content."""
        content = content[:4000]  # Truncate for context

        result = await self.llm.ask_json(
            f"Extract freelance job listings from this feed content.\n\n"
            f"Search: {search_term}\n"
            f"Content:\n{content}\n\n"
            f"ARCANA can complete: content writing, chatbot building, UGC video production, "
            f"workflow automation, lead generation, social media management, cold email campaigns, "
            f"SEO content, AI agent development, review management.\n\n"
            f"Return JSON: {{\"listings\": [\n"
            f'  {{"title": str, "description": str, "budget": str, "link": str, '
            f'"fit_score": int (0-100), "service_match": str, '
            f'"can_auto_complete": bool, "proposal_angle": str}}\n'
            f"]}}",
            tier=Tier.HAIKU,
            max_tokens=500,
        )
        return [l for l in result.get("listings", []) if l.get("fit_score", 0) >= 35]

    # ════════════════════════════════════════════════════════════════
    # HACKER NEWS SCANNING
    # ════════════════════════════════════════════════════════════════

    async def _scan_hackernews(self) -> dict[str, Any]:
        """Scan Hacker News for opportunities — Ask HN, Show HN, relevant discussions."""
        found = 0

        try:
            http = await self._get_http()

            # Check top stories and new stories
            for endpoint in ["topstories", "newstories"]:
                resp = await http.get(f"https://hacker-news.firebaseio.com/v0/{endpoint}.json")
                if resp.status_code != 200:
                    continue

                story_ids = resp.json()[:30]  # Check top 30

                for story_id in random.sample(story_ids, min(10, len(story_ids))):
                    try:
                        item_resp = await http.get(
                            f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                        )
                        if item_resp.status_code != 200:
                            continue

                        item = item_resp.json()
                        title = item.get("title", "")
                        text = item.get("text", "")

                        # Quick filter
                        keywords = ["ai", "automation", "chatbot", "startup", "saas", "marketing",
                                    "seo", "content", "business", "freelance", "agency", "ugc",
                                    "ask hn", "show hn", "hiring"]
                        if not any(kw in title.lower() for kw in keywords):
                            continue

                        full_text = f"{title} {text or ''}"
                        if self._is_seen(full_text):
                            continue

                        opp = await self._qualify_hn_post(title, text or "")
                        if opp and opp.get("is_opportunity"):
                            found += 1
                            self.metrics["by_source"]["hn"] = self.metrics["by_source"].get("hn", 0) + 1

                            self._save_opportunity(
                                source="hackernews",
                                identifier=str(story_id),
                                text=full_text[:300],
                                score=opp.get("score", 0),
                                service=opp.get("service_match", "general"),
                                details={**opp, "hn_id": story_id, "url": item.get("url", "")},
                            )

                    except Exception:
                        continue

                await asyncio.sleep(random.uniform(1, 3))

        except Exception as exc:
            logger.debug("HN scan failed: %s", exc)

        return {"found": found}

    async def _qualify_hn_post(self, title: str, text: str) -> dict[str, Any]:
        """Qualify a Hacker News post."""
        result = await self.llm.ask_json(
            f"Is this Hacker News post an opportunity for ARCANA AI?\n\n"
            f"Title: {title}\nText: {text[:300]}\n\n"
            f"Opportunities include: people building products that need UGC/marketing, "
            f"startups needing automation, businesses asking for AI help, "
            f"founders with pain points ARCANA solves.\n\n"
            f"Return JSON: {{"
            f'"is_opportunity": bool, "score": int (0-100), '
            f'"service_match": str, "opportunity_type": str, '
            f'"estimated_value": int}}',
            tier=Tier.HAIKU,
            max_tokens=100,
        )
        return result

    # ════════════════════════════════════════════════════════════════
    # QUALIFICATION ENGINE
    # ════════════════════════════════════════════════════════════════

    async def _qualify_opportunity(self, text: str, category: str) -> dict[str, Any]:
        """LLM-powered qualification of an opportunity."""
        pricing = SERVICE_PRICING.get(category, {"min": 100, "max": 5000, "avg_mrr": 1000})

        result = await self.llm.ask_json(
            f"You are ARCANA AI, hunting for business opportunities on X.\n"
            f"Category: {category}\n\n"
            f"Tweet: \"{text}\"\n\n"
            f"Services ARCANA delivers autonomously:\n"
            f"- UGC video production ($50-150/video, $400-3000/mo packs)\n"
            f"- AI chatbot setup ($300-1000/mo)\n"
            f"- Social media management ($500-5000/mo)\n"
            f"- Lead gen / cold email ($500-2000/mo)\n"
            f"- Review response management ($29-59/mo/location)\n"
            f"- SEO content at scale ($1500-5000/mo)\n"
            f"- AI consulting ($2000-10000/mo)\n\n"
            f"Reply rules:\n"
            f"- HELP FIRST, pitch never. Give them actual useful advice.\n"
            f"- Reference a specific result or framework you've used.\n"
            f"- Sound like a knowledgeable peer, not a salesperson.\n"
            f"- Under 280 chars.\n"
            f"- Examples:\n"
            f"  'We solved this exact problem for a DTC brand — cut content costs 80%. "
            f"Happy to share the framework.'\n"
            f"  'Running this same setup for 3 clients. The key insight: [specific tip]. "
            f"DM me if you want the full playbook.'\n\n"
            f"Return JSON: {{\n"
            f'  "is_opportunity": bool,\n'
            f'  "score": int (0-100),\n'
            f'  "service_match": str,\n'
            f'  "estimated_value": int ({pricing["min"]}-{pricing["max"]}),\n'
            f'  "urgency": "now"|"soon"|"exploring",\n'
            f'  "buyer_type": "business_owner"|"marketer"|"founder"|"agency"|"other",\n'
            f'  "reply": str|null (value-first, under 280 chars)\n'
            f"}}",
            tier=Tier.HAIKU,
            max_tokens=200,
        )
        return result

    async def _qualify_pain_signal(self, text: str) -> dict[str, Any]:
        """Qualify outbound prospect from pain signal."""
        result = await self.llm.ask_json(
            f"This person is expressing a business pain point on X.\n\n"
            f"Tweet: \"{text}\"\n\n"
            f"Can ARCANA AI help? If so, write an EMPATHY-FIRST reply:\n"
            f"- Lead with understanding their frustration\n"
            f"- Share ONE specific tip that actually helps\n"
            f"- Subtle positioning at the end (optional)\n"
            f"- Under 280 chars\n"
            f"- Sound like a helpful founder, not a bot\n\n"
            f"Return JSON: {{\n"
            f'  "is_prospect": bool,\n'
            f'  "score": int (0-100),\n'
            f'  "pain_point": str,\n'
            f'  "service_match": str,\n'
            f'  "estimated_value": int,\n'
            f'  "empathy_reply": str|null\n'
            f"}}",
            tier=Tier.HAIKU,
            max_tokens=200,
        )
        return result

    # ════════════════════════════════════════════════════════════════
    # AUTO-PROPOSAL GENERATION
    # ════════════════════════════════════════════════════════════════

    async def generate_proposal_for_opportunity(self, opp_key: str) -> dict[str, Any] | None:
        """Generate a proposal for a qualified opportunity."""
        data = self.memory.get_knowledge("projects", opp_key)
        if not data:
            data = self.memory.get_knowledge("resources", opp_key)
        if not data:
            return None

        result = await self.llm.ask_json(
            f"Generate a brief proposal for this opportunity.\n\n"
            f"Opportunity:\n{data}\n\n"
            f"Format:\n"
            f"- Opening line referencing their specific need (1 sentence)\n"
            f"- What ARCANA will deliver (3-4 bullet points)\n"
            f"- Timeline (1 sentence)\n"
            f"- Price range\n"
            f"- Next step (book a call or DM)\n\n"
            f"Keep it under 200 words. Professional but not corporate.\n\n"
            f"Return JSON: {{\n"
            f'  "subject": str,\n'
            f'  "proposal_text": str,\n'
            f'  "price_range": str,\n'
            f'  "service": str,\n'
            f'  "confidence": int (0-100)\n'
            f"}}",
            tier=Tier.SONNET,
        )

        if result:
            self.metrics["proposals_sent"] += 1
            self.memory.log(
                f"[Scanner] Proposal generated for {opp_key}: {result.get('service', 'N/A')} "
                f"@ {result.get('price_range', 'N/A')}",
                "Scanner",
            )

        return result

    # ════════════════════════════════════════════════════════════════
    # PIPELINE MANAGEMENT
    # ════════════════════════════════════════════════════════════════

    def _save_opportunity(
        self, source: str, identifier: str, text: str, score: int,
        service: str, details: dict[str, Any],
    ) -> None:
        """Save opportunity to pipeline in memory."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        safe_id = identifier[:30].replace("/", "-").replace(" ", "-")
        key = f"opp-{source}-{safe_id}"

        # Determine pipeline stage
        category = "projects" if score >= 60 else "resources"

        self.memory.save_knowledge(
            category,
            key,
            f"# Opportunity: {service}\n\n"
            f"- Source: {source}\n"
            f"- Identifier: {identifier}\n"
            f"- Score: {score}/100\n"
            f"- Service: {service}\n"
            f"- Est. value: ${details.get('estimated_value', 0)}/mo\n"
            f"- Urgency: {details.get('urgency', 'unknown')}\n"
            f"- Buyer type: {details.get('buyer_type', 'unknown')}\n"
            f"- Found: {ts}\n"
            f"- Status: {'auto_responded' if details.get('reply') or details.get('empathy_reply') else 'queued'}\n"
            f"- Text: {text[:300]}\n",
        )

    def get_pipeline(self, min_score: int = 0) -> list[str]:
        """Get all opportunities in the pipeline."""
        all_opps = [
            name for name in self.memory.list_knowledge("projects")
            if name.startswith("opp-")
        ]
        all_opps.extend(
            name for name in self.memory.list_knowledge("resources")
            if name.startswith("opp-")
        )
        return all_opps

    def get_pipeline_summary(self) -> dict[str, Any]:
        """Get pipeline summary by source and service."""
        pipeline = self.get_pipeline()
        by_source: dict[str, int] = {}
        by_service: dict[str, int] = {}
        total_value = 0

        for key in pipeline:
            data = self.memory.get_knowledge("projects", key) or self.memory.get_knowledge("resources", key)
            if not data:
                continue

            for line in data.splitlines():
                if line.startswith("- Source:"):
                    src = line.split(":", 1)[1].strip()
                    by_source[src] = by_source.get(src, 0) + 1
                elif line.startswith("- Service:"):
                    svc = line.split(":", 1)[1].strip()
                    by_service[svc] = by_service.get(svc, 0) + 1
                elif "Est. value:" in line and "$" in line:
                    try:
                        val = float(line.split("$")[1].split("/")[0].replace(",", ""))
                        total_value += val
                    except (IndexError, ValueError):
                        pass

        return {
            "total_opportunities": len(pipeline),
            "by_source": by_source,
            "by_service": by_service,
            "estimated_pipeline_value_monthly": total_value,
        }

    # ════════════════════════════════════════════════════════════════
    # PIPELINE NURTURING
    # ════════════════════════════════════════════════════════════════

    async def nurture_pipeline(self) -> dict[str, Any]:
        """Follow up on queued opportunities. Run during weekly ops."""
        pipeline = self.get_pipeline()
        followed_up = 0
        proposals_generated = 0

        # Get high-score opportunities that haven't been followed up
        for key in pipeline[:20]:  # Process top 20
            data = self.memory.get_knowledge("projects", key)
            if not data:
                continue

            # Check if already followed up
            if "followed_up" in data:
                continue

            # Check score
            score = 0
            for line in data.splitlines():
                if line.startswith("- Score:"):
                    try:
                        score = int(line.split(":")[1].strip().split("/")[0])
                    except (ValueError, IndexError):
                        pass

            if score >= 60:
                # Generate and save proposal
                proposal = await self.generate_proposal_for_opportunity(key)
                if proposal:
                    proposals_generated += 1
                    # Update the opportunity with proposal
                    updated = data + f"\n- Proposal: {proposal.get('price_range', 'N/A')}\n- followed_up: true\n"
                    self.memory.save_knowledge("projects", key, updated)
                    followed_up += 1

        return {"followed_up": followed_up, "proposals_generated": proposals_generated}

    # ════════════════════════════════════════════════════════════════
    # NIGHTLY ANALYSIS
    # ════════════════════════════════════════════════════════════════

    async def nightly_analysis(self) -> dict[str, Any]:
        """Analyze hunting performance. What's working? What to change?"""
        summary = self.get_pipeline_summary()

        result = await self.llm.ask_json(
            f"Analyze ARCANA AI's opportunity hunting performance.\n\n"
            f"Today's metrics:\n"
            f"- Opportunities found: {self.metrics['opportunities_found']}\n"
            f"- Auto-responded: {self.metrics['auto_responded']}\n"
            f"- Escalated: {self.metrics['escalated_to_humans']}\n"
            f"- Proposals sent: {self.metrics['proposals_sent']}\n"
            f"- By source: {self.metrics['by_source']}\n"
            f"- By service: {self.metrics['by_service']}\n\n"
            f"Pipeline:\n"
            f"- Total: {summary['total_opportunities']}\n"
            f"- By source: {summary['by_source']}\n"
            f"- By service: {summary['by_service']}\n"
            f"- Est. pipeline value: ${summary['estimated_pipeline_value_monthly']:,.0f}/mo\n\n"
            f"Evaluate:\n"
            f"1. Which sources produce the best ROI?\n"
            f"2. Which services have the most demand?\n"
            f"3. Are replies converting? How to improve?\n"
            f"4. What new queries or sources should we add?\n"
            f"5. What should we stop doing?\n\n"
            f"Return JSON: {{\n"
            f'  "best_source": str,\n'
            f'  "best_service": str,\n'
            f'  "pipeline_health": "strong"|"moderate"|"weak",\n'
            f'  "estimated_monthly_conversion": int ($ from current pipeline),\n'
            f'  "improvements": [str],\n'
            f'  "new_queries_to_try": [str],\n'
            f'  "services_to_promote_more": [str],\n'
            f'  "services_to_deprioritize": [str]\n'
            f"}}",
            tier=Tier.SONNET,
        )

        # Log analysis
        self.memory.log(
            f"[Scanner] Nightly analysis: {result.get('pipeline_health', 'N/A')} pipeline, "
            f"best source: {result.get('best_source', 'N/A')}, "
            f"best service: {result.get('best_service', 'N/A')}, "
            f"est. conversion: ${result.get('estimated_monthly_conversion', 0):,}/mo",
            "Scanner",
        )

        # Reset daily metrics
        self.metrics = {
            "opportunities_found": 0,
            "auto_responded": 0,
            "escalated_to_humans": 0,
            "proposals_sent": 0,
            "by_source": {},
            "by_service": {},
        }

        return result

    def format_scanner_report(self) -> str:
        """Format scanner metrics for morning/nightly report."""
        summary = self.get_pipeline_summary()
        return (
            f"**Opportunity Scanner**\n"
            f"Pipeline: {summary['total_opportunities']} opportunities "
            f"(${summary['estimated_pipeline_value_monthly']:,.0f}/mo est. value)\n"
            f"Today: {self.metrics['opportunities_found']} found, "
            f"{self.metrics['auto_responded']} responded, "
            f"{self.metrics['escalated_to_humans']} escalated\n"
            f"Top sources: {', '.join(f'{k}({v})' for k, v in sorted(self.metrics['by_source'].items(), key=lambda x: -x[1])[:3]) or 'N/A'}\n"
            f"Top services: {', '.join(f'{k}({v})' for k, v in sorted(self.metrics['by_service'].items(), key=lambda x: -x[1])[:3]) or 'N/A'}"
        )
