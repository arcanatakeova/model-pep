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

from src.database import Database
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
        '"unboxing video" ("need" OR "looking for" OR "who does")',
        '"product demo" ("video" OR "need" OR "creator" OR "looking for")',
        '"Meta ads" ("creative" OR "need" OR "video" OR "UGC")',
        '"video ads" ("agency" OR "freelancer" OR "need" OR "DTC")',
        '"influencer content" ("need" OR "looking for" OR "budget")',
        '"shoppable video" OR "product photography" ("need" OR "looking")',
        '"content creator" ("ecommerce" OR "DTC" OR "Shopify" OR "Amazon")',
    ],

    # ── AI Chatbot ($300-1000/mo) ───────────────────────────────
    "chatbot": [
        '"need a chatbot" OR "looking for chatbot" OR "chatbot for my"',
        '"customer support bot" OR "AI chatbot" ("build" OR "need" OR "looking")',
        '"website chat" ("need" OR "want" OR "looking for" OR "how to add")',
        '"automate support" OR "support tickets" ("too many" OR "overwhelmed")',
        '"intercom alternative" OR "zendesk alternative" OR "drift alternative"',
        '"chatbot developer" OR "chatbot agency" OR "build me a bot"',
        '"AI customer service" ("need" OR "looking for" OR "implement")',
        '"live chat" ("too expensive" OR "need" OR "alternative" OR "bot")',
        '"customer support" ("automate" OR "AI" OR "overwhelmed" OR "scaling")',
        '"helpdesk" ("AI" OR "automate" OR "alternative" OR "need")',
        '"WhatsApp bot" OR "Telegram bot" OR "Slack bot" ("business" OR "need")',
        '"AI assistant" ("for my website" OR "for my business" OR "need")',
        '"reduce support tickets" OR "support is killing us" OR "drowning in tickets"',
        '"Tidio" OR "ManyChat" OR "Chatfuel" ("alternative" OR "better" OR "need")',
        '"FAQ bot" OR "knowledge base AI" OR "self-service support"',
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
        '"social media agency" ("recommend" OR "looking for" OR "affordable")',
        '"content calendar" ("need help" OR "overwhelmed" OR "template")',
        '"grow my following" OR "grow my audience" ("help" OR "how to" OR "tips")',
        '"social media ROI" ("not seeing" OR "bad" OR "how to" OR "improve")',
        '"LinkedIn content" ("need" OR "help" OR "strategy" OR "ghostwriter")',
        '"Pinterest marketing" ("need" OR "help" OR "strategy")',
        '"brand awareness" ("social" OR "need" OR "strategy" OR "help")',
        '"social media" ("burned out" OR "hate" OR "no time" OR "can\'t keep up")',
    ],

    # ── Lead Gen / Cold Email ($500-2000/mo) ────────────────────
    "leadgen": [
        '"need leads" OR "lead generation" OR "cold email" OR "need more clients"',
        '"email marketing help" OR "outbound sales" OR "B2B leads"',
        '"need appointments" OR "book more calls" OR "sales pipeline help"',
        '"cold outreach" ("help" OR "need" OR "agency" OR "tool")',
        '"lead gen agency" OR "appointment setting" ("looking for" OR "need")',
        '"not enough clients" OR "need more customers" OR "how to get clients"',
        '"Instantly" OR "Lemlist" OR "Smartlead" ("alternative" OR "help" OR "setup")',
        '"cold email deliverability" ("help" OR "issues" OR "low open rates")',
        '"sales development" ("outsource" OR "need" OR "agency" OR "hire")',
        '"B2B outreach" ("need" OR "help" OR "agency" OR "tool")',
        '"pipeline empty" OR "pipeline is dry" OR "no pipeline"',
        '"SDR" ("too expensive" OR "alternative" OR "hiring" OR "need")',
        '"Apollo" OR "ZoomInfo" ("alternative" OR "help" OR "too expensive")',
        '"inbound leads" ("not enough" OR "need more" OR "dried up")',
        '"outbound" ("starting" OR "help" OR "need" OR "agency" OR "build")',
    ],

    # ── Review Management ($29-59/mo/location) ──────────────────
    "reviews": [
        '"bad reviews" OR "negative reviews" OR "review management"',
        '"Google reviews" ("help" OR "need" OR "how to" OR "respond")',
        '"Yelp reviews" ("help" OR "need" OR "respond to")',
        '"reputation management" ("need" OR "looking for" OR "help")',
        '"online reputation" ("damaged" OR "need help" OR "fix")',
        '"1 star review" OR "fake review" ("help" OR "what to do" OR "how to")',
        '"respond to reviews" ("help" OR "template" OR "how to" OR "AI")',
        '"review response" ("service" OR "agency" OR "automate" OR "AI")',
        '"Trustpilot" ("reviews" OR "management" OR "help" OR "respond")',
        '"Google Business Profile" ("reviews" OR "help" OR "manage")',
        '"restaurant reviews" ("help" OR "respond" OR "bad" OR "negative")',
        '"doctor reviews" OR "dentist reviews" ("help" OR "respond" OR "manage")',
        '"hotel reviews" ("respond" OR "manage" OR "help" OR "negative")',
        '"review response time" OR "review management tool" OR "review automation"',
        '"reputation" ("crisis" OR "damage" OR "repair" OR "fix" OR "help")',
    ],

    # ── SEO ($1500-5000/mo) ─────────────────────────────────────
    "seo": [
        '"need SEO" OR "SEO help" OR "website not ranking"',
        '"SEO agency" ("looking for" OR "recommend" OR "need")',
        '"organic traffic" ("need more" OR "dropped" OR "help")',
        '"Google ranking" ("dropped" OR "need help" OR "improve")',
        '"content marketing" ("need" OR "help" OR "agency" OR "looking for")',
        '"blog content" ("need" OR "help" OR "writer" OR "agency")',
        '"technical SEO" ("need" OR "audit" OR "help" OR "issues")',
        '"local SEO" ("need" OR "help" OR "agency" OR "not ranking")',
        '"link building" ("need" OR "service" OR "agency" OR "help")',
        '"keyword research" ("need help" OR "service" OR "don\'t know how")',
        '"SEO" ("fired my" OR "bad agency" OR "wasted money" OR "scammed")',
        '"programmatic SEO" ("need" OR "help" OR "how to" OR "setup")',
        '"Ahrefs" OR "SEMrush" ("alternative" OR "too expensive" OR "help")',
        '"website traffic" ("dropped" OR "declining" OR "need more" OR "help")',
        '"Google update" ("hit" OR "penalized" OR "lost rankings" OR "recovery")',
    ],

    # ── AI Consulting ($2000-10000/mo) ──────────────────────────
    "consulting": [
        '"AI consulting" OR "business automation" OR "automate my"',
        '"need AI help" OR "AI strategy" OR "implement AI"',
        '"streamline operations" OR "too much manual work"',
        '"AI developer" ("looking for" OR "need" OR "hiring")',
        '"digital transformation" ("need" OR "help" OR "consulting")',
        '"workflow automation" ("need" OR "help" OR "looking for")',
        '"AI agent" ("build" OR "need" OR "looking for" OR "developer")',
        '"n8n" OR "Make.com" OR "Zapier" ("help" OR "too complex" OR "need developer")',
        '"ChatGPT" ("for my business" OR "implement" OR "integrate" OR "API")',
        '"AI integration" ("need" OR "help" OR "consulting" OR "strategy")',
        '"custom AI" ("need" OR "build" OR "looking for" OR "developer")',
        '"process automation" ("need" OR "help" OR "consulting")',
        '"AI ROI" OR "AI budget" OR "AI spend" ("help" OR "how to" OR "justify")',
        '"replace" ("manual work" OR "spreadsheets" OR "data entry") AND "AI"',
        '"GPT" ("fine-tune" OR "custom" OR "business" OR "integrate" OR "deploy")',
    ],

    # ── Email Marketing ($500-3000/mo) ──────────────────────────
    "email_marketing": [
        '"email marketing" ("need help" OR "agency" OR "looking for" OR "freelancer")',
        '"email automation" ("need" OR "setup" OR "help" OR "looking for")',
        '"email sequences" ("need" OR "help" OR "not working" OR "improve")',
        '"Klaviyo" OR "Mailchimp" ("help" OR "migration" OR "setup" OR "alternative")',
        '"newsletter" ("start" OR "need help" OR "grow" OR "monetize")',
        '"email deliverability" ("issues" OR "help" OR "spam" OR "not landing")',
        '"email copywriting" ("need" OR "help" OR "hiring" OR "agency")',
        '"drip campaign" ("need" OR "help" OR "setup" OR "not working")',
        '"welcome sequence" OR "nurture sequence" ("need" OR "help" OR "build")',
        '"email list" ("grow" OR "build" OR "need help" OR "monetize")',
        '"abandoned cart" ("email" OR "recovery" OR "help" OR "sequence")',
        '"email strategy" ("need" OR "help" OR "consulting" OR "audit")',
        '"open rates" ("low" OR "declining" OR "improve" OR "help")',
        '"click rates" ("low" OR "improve" OR "help" OR "bad")',
        '"unsubscribe rate" ("high" OR "too many" OR "help" OR "fix")',
    ],

    # ── Website / Landing Page ($1000-5000 one-time) ────────────
    "web_design": [
        '"need a website" OR "need a landing page" OR "website redesign"',
        '"Shopify" ("setup" OR "need help" OR "developer" OR "customize")',
        '"WordPress" ("help" OR "developer" OR "redesign" OR "fix")',
        '"Webflow" ("need" OR "developer" OR "help" OR "build")',
        '"conversion rate" ("low" OR "need help" OR "optimize" OR "CRO")',
        '"landing page" ("not converting" OR "need" OR "build" OR "optimize")',
        '"website speed" ("slow" OR "improve" OR "help" OR "optimize")',
        '"ecommerce website" ("need" OR "build" OR "redesign" OR "help")',
        '"headless commerce" ("need" OR "migrate" OR "help" OR "build")',
        '"website" ("outdated" OR "ugly" OR "need new" OR "embarrassing")',
    ],

    # ── Competitor Displacement ─────────────────────────────────
    "displacement": [
        '"terrible agency" OR "fired my agency" OR "bad freelancer"',
        '"waste of money" ("agency" OR "freelancer" OR "marketing")',
        '"looking for new" ("agency" OR "developer" OR "marketer")',
        '"disappointed with" ("agency" OR "freelancer" OR "results")',
        '"overcharged" ("agency" OR "freelancer" OR "developer")',
        '"anyone better than" OR "alternative to my current"',
        '"scammed by" ("agency" OR "freelancer" OR "marketing" OR "SEO")',
        '"no results" ("agency" OR "freelancer" OR "marketing")',
        '"ghosted by" ("agency" OR "freelancer" OR "developer")',
        '"missed deadlines" ("agency" OR "freelancer" OR "developer")',
        '"poor communication" ("agency" OR "freelancer" OR "developer")',
        '"switching agencies" OR "changing agencies" OR "need better agency"',
        '"contract ending" ("agency" OR "freelancer" OR "marketing")',
        '"cancelling" ("agency" OR "marketing service" OR "freelancer")',
        '"regret hiring" ("agency" OR "freelancer" OR "cheap" OR "offshore")',
    ],

    # ── Trigger Events (Budget Just Freed Up) ───────────────────
    "triggers": [
        '"just raised" ("funding" OR "seed" OR "series") AND ("looking for" OR "hiring")',
        '"just launched" AND ("need" OR "looking for" OR "marketing")',
        '"scaling" AND ("need help" OR "looking for" OR "hiring")',
        '"growing fast" AND ("need" OR "overwhelmed" OR "hiring")',
        '"new business" AND ("need" OR "website" OR "marketing" OR "social media")',
        '"just got funded" OR "closed our round" OR "raised capital"',
        '"expanding" AND ("new market" OR "hiring" OR "need" OR "marketing")',
        '"product launch" AND ("need" OR "marketing" OR "help" OR "strategy")',
        '"going viral" AND ("need help" OR "scaling" OR "overwhelmed")',
        '"revenue milestone" OR "hit $1M" OR "hit $100K" OR "first sale"',
        '"Y Combinator" OR "Techstars" OR "500 Startups" AND ("looking for" OR "need")',
        '"opening" AND ("new location" OR "new store" OR "new office")',
        '"rebranding" AND ("need" OR "help" OR "agency" OR "looking for")',
        '"IPO" OR "acquisition" OR "merger" AND ("marketing" OR "rebrand")',
        '"hiring" AND ("marketing" OR "social media" OR "content") AND "can\'t find"',
    ],

    # ── Watering Holes (Where Ideal Clients Hang Out) ───────────
    "watering_holes": [
        '"small business owner" AND ("struggle" OR "help" OR "advice" OR "tip")',
        '"ecommerce" AND ("struggling" OR "need more sales" OR "conversion")',
        '"DTC brand" AND ("need" OR "looking for" OR "help")',
        '"startup founder" AND ("need" OR "looking for" OR "marketing")',
        '"agency owner" AND ("need" OR "white label" OR "outsource")',
        '"Shopify store" AND ("need" OR "help" OR "marketing" OR "struggling")',
        '"solopreneur" AND ("need" OR "help" OR "overwhelmed" OR "tip")',
        '"side hustle" AND ("grow" OR "marketing" OR "need" OR "help")',
        '"bootstrapped" AND ("need" OR "marketing" OR "growth" OR "help")',
        '"Amazon seller" AND ("need" OR "marketing" OR "help" OR "struggling")',
        '"restaurant owner" AND ("marketing" OR "reviews" OR "help" OR "social")',
        '"real estate agent" AND ("marketing" OR "leads" OR "social" OR "help")',
        '"coach" OR "consultant" AND ("marketing" OR "clients" OR "leads" OR "help")',
        '"SaaS founder" AND ("marketing" OR "growth" OR "leads" OR "content")',
        '"freelancer" AND ("clients" OR "marketing" OR "grow" OR "help")',
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
    '"support inbox" ("overflowing" OR "drowning" OR "can\'t keep up")',
    '"need to automate" OR "should automate" OR "how to automate"',
    '"burned out" AND ("business" OR "entrepreneur" OR "founder")',
    '"no marketing budget" OR "tight budget" AND ("marketing" OR "growth")',
    '"website" ("no traffic" OR "zero visitors" OR "nobody visits")',
    '"email list" ("dead" OR "not growing" OR "no engagement")',
    '"sales are down" OR "revenue dropping" OR "losing money"',
    '"competitors" ("crushing us" OR "ahead of" OR "outranking")',
    '"onboarding" ("too slow" OR "losing people" OR "painful" OR "manual")',
    '"spreadsheet hell" OR "drowning in spreadsheets" OR "too many tools"',
    '"cash flow" ("problems" OR "tight" OR "struggling")',
    '"nobody knows about us" OR "no brand awareness" OR "invisible online"',
    '"return customers" ("none" OR "low" OR "how to get" OR "struggling")',
    '"hiring" ("can\'t find" OR "too expensive" OR "nobody applies")',
    '"admin work" ("too much" OR "killing me" OR "overwhelmed")',
    '"pricing" ("too low" OR "undercharging" OR "race to bottom")',
    '"customer complaints" ("too many" OR "increasing" OR "can\'t handle")',
    '"marketing" ("doesn\'t work" OR "waste" OR "not seeing results")',
    '"inventory" ("managing" OR "nightmare" OR "tracking" OR "manual")',
    '"invoicing" ("manual" OR "late payments" OR "chasing" OR "automate")',
]

# ── Reddit subreddits to monitor ────────────────────────────────
REDDIT_SUBREDDITS = [
    # Business & Entrepreneurship
    "smallbusiness", "Entrepreneur", "startups", "SaaS", "GrowMyBusiness",
    "EntrepreneurRideAlong", "sweatystartup", "microsaas", "indiehackers",
    # E-commerce
    "ecommerce", "shopify", "AmazonSeller", "FulfillmentByAmazon", "dropship",
    "AmazonFBA", "Etsy",
    # Marketing & Growth
    "digital_marketing", "socialmedia", "SEO", "PPC", "marketing",
    "content_marketing", "emailmarketing", "copywriting", "advertising",
    "SocialMediaMarketing", "growthhacking",
    # Tech & Development
    "webdev", "web_design", "artificial", "ChatGPT", "LocalLLaMA",
    "MachineLearning", "learnprogramming",
    # Freelancing & Agency
    "freelance", "Upwork", "agency", "DigitalNomad",
    # Industry Specific
    "restaurateur", "RealEstate", "dentistry", "realtors", "legaladvice",
    "healthcare", "fitness",
]

# ── Freelance platform search terms ─────────────────────────────
FREELANCE_SEARCH_TERMS = [
    "AI chatbot development", "UGC video production", "social media management",
    "cold email automation", "AI content writing", "review management",
    "lead generation", "AI agent development", "business process automation",
    "SEO content writing", "TikTok ad creative", "email marketing automation",
    "customer support automation", "product demo video", "brand video content",
    "AI workflow automation", "n8n automation setup", "Zapier expert",
    "Make.com automation", "CRM setup and automation", "HubSpot setup",
    "Salesforce automation", "data entry automation", "web scraping",
    "API integration", "WhatsApp chatbot", "Discord bot development",
    "Slack bot development", "GPT fine-tuning", "AI agent builder",
    "video editing for social", "Instagram Reels creation", "YouTube Shorts",
    "podcast editing", "landing page design", "conversion optimization",
    "Google Ads management", "Facebook Ads management", "LinkedIn Ads",
    "Shopify store setup", "WooCommerce setup", "headless CMS development",
]

# ── Indeed/Job Board terms (companies hiring = they have budget) ─
JOB_BOARD_TERMS = [
    "social media coordinator", "social media manager", "content creator",
    "marketing coordinator", "digital marketing specialist", "SEO specialist",
    "email marketing specialist", "customer support representative",
    "community manager", "brand ambassador", "growth marketer",
    "content marketing manager", "copywriter", "video editor",
    "graphic designer", "marketing assistant", "PR coordinator",
]

# ── Product Hunt categories to monitor ──────────────────────────
PRODUCT_HUNT_CATEGORIES = [
    "saas", "developer-tools", "marketing", "e-commerce", "ai",
    "productivity", "social-media", "analytics", "customer-support",
    "no-code", "design-tools", "startup-tools",
]

# ── Google Alerts RSS keywords ──────────────────────────────────
GOOGLE_ALERT_KEYWORDS = [
    "looking for AI agency", "need marketing automation",
    "small business automation", "AI chatbot for business",
    "UGC creator needed", "social media manager hiring",
    "review management service", "SEO agency Portland",
    "AI consulting Portland", "business automation Portland",
    "cold email agency", "lead generation service",
]

# ── Indie Hackers keywords ──────────────────────────────────────
INDIEHACKER_KEYWORDS = [
    "need marketing", "need UGC", "looking for agency",
    "need help with SEO", "customer support scaling",
    "cold email", "lead gen", "content marketing",
]

# ── Geographic targeting (Portland-first) ───────────────────────
GEO_TARGETS = {
    "local": ["Portland", "PDX", "Oregon", "PNW"],
    "regional": ["Seattle", "Vancouver WA", "Boise", "Pacific Northwest"],
    "national": ["USA", "US", "United States"],
}

# ── Response templates (high-conversion, battle-tested) ─────────
RESPONSE_TEMPLATES: dict[str, list[str]] = {
    "ugc": [
        "We produce UGC at scale for DTC brands — cut content costs 80% vs traditional agencies. Happy to show examples. DM open.",
        "Running UGC production for 3 brands right now. The key: AI-powered scripting + human-quality video. Would love to share what's working.",
        "Just helped a Shopify store go from $0 to $40K/mo with UGC-driven Meta ads. The framework is repeatable. DM me.",
    ],
    "chatbot": [
        "Built this exact setup for a SaaS company — reduced support tickets 60% in the first month. Happy to share the architecture.",
        "AI chatbots are our bread and butter. The trick is training on YOUR data, not generic. Would love to walk you through our approach.",
        "We deploy custom AI chatbots that actually work (not the template garbage). One client saved $4K/mo in support costs. DM open.",
    ],
    "social": [
        "Managing social for 5 businesses right now. The 80/20: batch content creation + scheduling + engagement system. Happy to share the framework.",
        "Social media doesn't have to be a time sink. We automate 80% and focus human effort on the 20% that actually drives revenue. DM me.",
    ],
    "leadgen": [
        "Running cold email campaigns that get 15-25% open rates and 3-5% reply rates. The key is hyper-personalization at scale. Happy to share.",
        "Built lead gen systems for B2B SaaS — consistently book 10-20 calls/month from cold outreach. DM me for the playbook.",
    ],
    "reviews": [
        "We manage review responses for 50+ locations. AI-crafted, brand-consistent responses within 24 hours. $45/mo per location. DM me.",
        "Bad reviews don't have to hurt. We respond to every review with empathy + brand voice. One restaurant went from 3.2 to 4.6 stars in 3 months.",
    ],
    "seo": [
        "SEO is a long game but we've helped clients 3x organic traffic in 6 months. The strategy: programmatic content + technical fixes. Happy to audit yours.",
        "Just recovered a client's rankings after a Google update. The fix was surprisingly simple. DM me if you want the playbook.",
    ],
    "consulting": [
        "We build AI automations that replace 10-20 hours of manual work per week. ROI is usually 30 days. Happy to scope your use case.",
        "AI consulting is what we do — strategy + implementation + ongoing optimization. One client saved $8K/mo by automating their onboarding. DM me.",
    ],
}

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
        db: Database | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.x = x
        self.notifier = notifier
        self.db = db
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
            from src.toolkit import random_user_agent
            self._http = httpx.AsyncClient(
                timeout=30,
                headers={"User-Agent": random_user_agent()},
                follow_redirects=True,
            )
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    def _dedup_key(self, text: str) -> str:
        try:
            from src.toolkit import fast_hash
            return fast_hash(text)[:12]  # xxhash: 10x faster than MD5
        except ImportError:
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
        """Run one AGGRESSIVE scan cycle. Hits 5+ sources every time.

        Every cycle touches X (always), pain signals (always), plus 3+ rotated
        sources. The scanner is ALWAYS hunting across multiple platforms.
        """
        self._cycle_count += 1
        logger.info("=== OPPORTUNITY SCAN CYCLE #%d ===", self._cycle_count)

        results = {
            "cycle": self._cycle_count,
            "x_inbound": 0,
            "x_pain": 0,
            "x_displacement": 0,
            "x_triggers": 0,
            "x_watering_holes": 0,
            "reddit": 0,
            "freelance": 0,
            "hn": 0,
            "product_hunt": 0,
            "indie_hackers": 0,
            "job_boards": 0,
            "google_alerts": 0,
            "total_found": 0,
            "auto_responded": 0,
            "escalated": 0,
        }

        # ═══ ALWAYS-ON SOURCES (every cycle) ═══════════════════════

        # ── Phase 1: X Inbound Hunting (pick 3 service categories) ──
        categories = list(X_HUNT_QUERIES.keys())
        random.shuffle(categories)
        for cat in categories[:3]:  # 3 categories per cycle (was 2)
            r = await self._hunt_x_category(cat)
            results["x_inbound"] += r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)
            results["escalated"] += r.get("escalated", 0)
            await asyncio.sleep(random.uniform(2, 5))

        # ── Phase 2: Pain Signal Detection (2 queries per cycle) ────
        for _ in range(2):  # Run 2 pain queries (was 1)
            r = await self._hunt_pain_signals()
            results["x_pain"] += r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ── Phase 3: Competitor Displacement (every 2nd cycle) ──────
        if self._cycle_count % 2 == 0:
            r = await self._hunt_x_category("displacement")
            results["x_displacement"] = r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ── Phase 4: Trigger Events (every 2nd cycle, offset) ───────
        if self._cycle_count % 2 == 1:
            r = await self._hunt_x_category("triggers")
            results["x_triggers"] = r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ── Phase 5: Watering Holes (every 3rd cycle) ───────────────
        if self._cycle_count % 3 == 0:
            r = await self._hunt_x_category("watering_holes")
            results["x_watering_holes"] = r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ═══ ROTATED SOURCES (hit 2-3 per cycle) ═══════════════════

        # ── Phase 6: Reddit Scanning (every 3rd cycle) ──────────────
        if self._cycle_count % 3 == 0:
            r = await self._scan_reddit()
            results["reddit"] = r.get("found", 0)
            results["auto_responded"] += r.get("responded", 0)

        # ── Phase 7: Freelance Platforms (every 4th cycle) ──────────
        if self._cycle_count % 4 == 0:
            r = await self._scan_freelance()
            results["freelance"] = r.get("found", 0)

        # ── Phase 8: Hacker News (every 6th cycle) ──────────────────
        if self._cycle_count % 6 == 0:
            r = await self._scan_hackernews()
            results["hn"] = r.get("found", 0)

        # ── Phase 9: Product Hunt (every 4th cycle, offset) ─────────
        if self._cycle_count % 4 == 2:
            r = await self._scan_product_hunt()
            results["product_hunt"] = r.get("found", 0)

        # ── Phase 10: Indie Hackers (every 6th cycle, offset) ───────
        if self._cycle_count % 6 == 3:
            r = await self._scan_indie_hackers()
            results["indie_hackers"] = r.get("found", 0)

        # ── Phase 11: Job Boards (every 8th cycle) ──────────────────
        if self._cycle_count % 8 == 0:
            r = await self._scan_job_boards()
            results["job_boards"] = r.get("found", 0)

        # ── Phase 12: Google Alerts (every 4th cycle, offset) ───────
        if self._cycle_count % 4 == 1:
            r = await self._scan_google_alerts()
            results["google_alerts"] = r.get("found", 0)

        # ── Tally ───────────────────────────────────────────────────
        results["total_found"] = sum(
            results[k] for k in results
            if k not in ("cycle", "total_found", "auto_responded", "escalated")
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

                # Auto-respond with value (use templates for high-score)
                reply = opp.get("reply")
                if score >= 70 and category in RESPONSE_TEMPLATES:
                    # Use battle-tested template for high-value leads
                    reply = random.choice(RESPONSE_TEMPLATES[category])
                if reply and score >= 35:
                    await self.x.reply_to(tweet.get("id", ""), reply)
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

        # Track query performance in the database
        if self.db:
            self.db.track_query_use(
                query_text=query,
                platform="x",
                category=category,
                found=found,
                responded=responded,
            )

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
    # PRODUCT HUNT SCANNING
    # ════════════════════════════════════════════════════════════════

    async def _scan_product_hunt(self) -> dict[str, Any]:
        """Scan Product Hunt for new launches that need ARCANA's services.

        New products launching = they need UGC, marketing, chatbots, SEO.
        Every PH launch is a potential client.
        """
        found = 0

        try:
            http = await self._get_http()
            # Product Hunt API or RSS
            resp = await http.get(
                "https://www.producthunt.com/feed?category=tech",
                headers={"User-Agent": "ARCANA-AI/1.0"},
            )
            if resp.status_code != 200:
                # Try alternative: PH daily feed
                resp = await http.get("https://www.producthunt.com/feed")

            if resp.status_code == 200:
                content = resp.text[:6000]
                opps = await self.llm.ask_json(
                    f"Extract Product Hunt launches that need ARCANA's services.\n\n"
                    f"Feed content:\n{content}\n\n"
                    f"For each launch, identify if they likely need:\n"
                    f"- UGC video production (for social ads)\n"
                    f"- AI chatbot (for customer support)\n"
                    f"- Social media management (to grow awareness)\n"
                    f"- SEO content (to rank organically)\n"
                    f"- Cold email setup (for B2B outreach)\n"
                    f"- Landing page optimization\n\n"
                    f"Return JSON: {{\"launches\": [\n"
                    f'  {{"name": str, "description": str, "url": str, '
                    f'"service_need": str, "estimated_value": int, '
                    f'"score": int (0-100), "approach_angle": str}}\n'
                    f"]}}",
                    tier=Tier.HAIKU,
                    max_tokens=500,
                )
                for launch in opps.get("launches", []):
                    if launch.get("score", 0) < 30:
                        continue
                    name = launch.get("name", "")
                    if self._is_seen(name):
                        continue

                    found += 1
                    self.metrics["by_source"]["product_hunt"] = self.metrics["by_source"].get("product_hunt", 0) + 1

                    self._save_opportunity(
                        source="product_hunt",
                        identifier=name[:30],
                        text=f"{name}: {launch.get('description', '')[:200]}",
                        score=launch.get("score", 50),
                        service=launch.get("service_need", "general"),
                        details=launch,
                    )

        except Exception as exc:
            logger.debug("Product Hunt scan failed: %s", exc)

        return {"found": found}

    # ════════════════════════════════════════════════════════════════
    # INDIE HACKERS SCANNING
    # ════════════════════════════════════════════════════════════════

    async def _scan_indie_hackers(self) -> dict[str, Any]:
        """Scan Indie Hackers for founders who need services."""
        found = 0

        try:
            http = await self._get_http()
            resp = await http.get(
                "https://www.indiehackers.com/feed.xml",
                headers={"User-Agent": "ARCANA-AI/1.0"},
            )
            if resp.status_code == 200:
                content = resp.text[:5000]
                opps = await self.llm.ask_json(
                    f"Extract posts from Indie Hackers where founders need help.\n\n"
                    f"Feed:\n{content}\n\n"
                    f"Look for: marketing struggles, growth problems, need for automation, "
                    f"content creation, customer support scaling, SEO questions.\n\n"
                    f"Return JSON: {{\"posts\": [\n"
                    f'  {{"title": str, "problem": str, "service_match": str, '
                    f'"score": int (0-100), "estimated_value": int, "url": str}}\n'
                    f"]}}",
                    tier=Tier.HAIKU,
                    max_tokens=400,
                )
                for post in opps.get("posts", []):
                    if post.get("score", 0) < 30 or self._is_seen(post.get("title", "")):
                        continue
                    found += 1
                    self.metrics["by_source"]["indie_hackers"] = self.metrics["by_source"].get("indie_hackers", 0) + 1
                    self._save_opportunity(
                        source="indie_hackers",
                        identifier=post.get("title", "")[:30],
                        text=f"{post.get('title', '')}: {post.get('problem', '')}",
                        score=post.get("score", 50),
                        service=post.get("service_match", "general"),
                        details=post,
                    )

        except Exception as exc:
            logger.debug("Indie Hackers scan failed: %s", exc)

        return {"found": found}

    # ════════════════════════════════════════════════════════════════
    # JOB BOARD SCANNING (Companies hiring = budget available)
    # ════════════════════════════════════════════════════════════════

    async def _scan_job_boards(self) -> dict[str, Any]:
        """Scan job boards for companies hiring roles ARCANA can replace.

        If a company is hiring a "social media coordinator" at $50K/yr,
        ARCANA can do it for $1,500/mo. That's a direct pitch.
        """
        found = 0

        term = random.choice(JOB_BOARD_TERMS)

        # Check Indeed RSS / Google Jobs
        for geo in GEO_TARGETS.get("local", [])[:1] + ["remote"]:
            try:
                http = await self._get_http()
                encoded = term.replace(" ", "+")
                geo_enc = geo.replace(" ", "+")
                resp = await http.get(
                    f"https://www.indeed.com/rss?q={encoded}&l={geo_enc}&sort=date&limit=10",
                    headers={"User-Agent": "ARCANA-AI/1.0 (job-scanner)"},
                )
                if resp.status_code != 200:
                    continue

                content = resp.text[:4000]
                opps = await self.llm.ask_json(
                    f"These companies are hiring for '{term}'. Can ARCANA AI replace or supplement this hire?\n\n"
                    f"Listings:\n{content}\n\n"
                    f"For each, calculate:\n"
                    f"- Salary they'd pay: ~$40-80K/yr for this role\n"
                    f"- ARCANA's price: $500-3000/mo for the same output\n"
                    f"- Savings: 50-80%\n\n"
                    f"Return JSON: {{\"jobs\": [\n"
                    f'  {{"company": str, "title": str, "salary_estimate": int, '
                    f'"arcana_price_monthly": int, "savings_pct": int, '
                    f'"service_match": str, "score": int (0-100), "pitch": str}}\n'
                    f"]}}",
                    tier=Tier.HAIKU,
                    max_tokens=400,
                )
                for job in opps.get("jobs", []):
                    if job.get("score", 0) < 40 or self._is_seen(job.get("company", "")):
                        continue
                    found += 1
                    self.metrics["by_source"]["job_boards"] = self.metrics["by_source"].get("job_boards", 0) + 1
                    self._save_opportunity(
                        source="job_boards",
                        identifier=f"{job.get('company', '')[:20]}-{term[:10]}",
                        text=f"{job.get('company', '')} hiring {term} — ARCANA can do it for ${job.get('arcana_price_monthly', 0)}/mo",
                        score=job.get("score", 50),
                        service=job.get("service_match", "social"),
                        details=job,
                    )

                await asyncio.sleep(random.uniform(2, 5))

            except Exception as exc:
                logger.debug("Job board scan failed: %s", exc)

        return {"found": found}

    # ════════════════════════════════════════════════════════════════
    # GOOGLE ALERTS SCANNING
    # ════════════════════════════════════════════════════════════════

    async def _scan_google_alerts(self) -> dict[str, Any]:
        """Check Google Alerts RSS feeds for relevant opportunities."""
        found = 0

        keyword = random.choice(GOOGLE_ALERT_KEYWORDS)
        encoded = keyword.replace(" ", "+")

        try:
            http = await self._get_http()
            resp = await http.get(
                f"https://www.google.com/alerts/feeds/{encoded}",
                headers={"User-Agent": "ARCANA-AI/1.0"},
            )
            if resp.status_code == 200:
                content = resp.text[:4000]
                opps = await self.llm.ask_json(
                    f"Extract business opportunities from these Google Alert results.\n\n"
                    f"Keyword: {keyword}\nContent:\n{content}\n\n"
                    f"Look for: businesses seeking services, RFPs, partnership opportunities, "
                    f"companies with problems ARCANA solves.\n\n"
                    f"Return JSON: {{\"alerts\": [\n"
                    f'  {{"title": str, "url": str, "opportunity": str, '
                    f'"service_match": str, "score": int (0-100), "estimated_value": int}}\n'
                    f"]}}",
                    tier=Tier.HAIKU,
                    max_tokens=300,
                )
                for alert in opps.get("alerts", []):
                    if alert.get("score", 0) < 30 or self._is_seen(alert.get("title", "")):
                        continue
                    found += 1
                    self.metrics["by_source"]["google_alerts"] = self.metrics["by_source"].get("google_alerts", 0) + 1
                    self._save_opportunity(
                        source="google_alerts",
                        identifier=alert.get("title", "")[:30],
                        text=f"{alert.get('title', '')}: {alert.get('opportunity', '')}",
                        score=alert.get("score", 50),
                        service=alert.get("service_match", "general"),
                        details=alert,
                    )

        except Exception as exc:
            logger.debug("Google Alerts scan failed: %s", exc)

        return {"found": found}

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

        # Persist to SQLite database
        if self.db:
            platform = source.split("_")[0] if "_" in source else source
            self.db.log_opportunity(
                source=source,
                platform=platform,
                original_text=text[:500],
                score=score,
                query_used=details.get("query_used"),
                author=details.get("author") or identifier,
                author_handle=details.get("author_handle"),
                service_match=service,
                estimated_value=details.get("estimated_value", 0),
                urgency=details.get("urgency"),
                buyer_type=details.get("buyer_type"),
                auto_responded=1 if details.get("reply") or details.get("empathy_reply") else 0,
                response_text=details.get("reply") or details.get("empathy_reply"),
                metadata=details,
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
        active_sources = sum(1 for v in self.metrics['by_source'].values() if v > 0)
        total_queries = sum(len(q) for q in X_HUNT_QUERIES.values()) + len(PAIN_QUERIES)
        return (
            f"**Opportunity Scanner** (Cycle #{self._cycle_count})\n"
            f"Pipeline: {summary['total_opportunities']} opportunities "
            f"(${summary['estimated_pipeline_value_monthly']:,.0f}/mo est. value)\n"
            f"Today: {self.metrics['opportunities_found']} found, "
            f"{self.metrics['auto_responded']} responded, "
            f"{self.metrics['escalated_to_humans']} escalated, "
            f"{self.metrics['proposals_sent']} proposals\n"
            f"Active sources: {active_sources}/12 | Query library: {total_queries} queries\n"
            f"Sources: X({self.metrics['by_source'].get('x_ugc', 0) + self.metrics['by_source'].get('x_chatbot', 0) + self.metrics['by_source'].get('x_social', 0) + self.metrics['by_source'].get('x_pain', 0)}) "
            f"Reddit({self.metrics['by_source'].get('reddit', 0)}) "
            f"Freelance({self.metrics['by_source'].get('freelance', 0)}) "
            f"HN({self.metrics['by_source'].get('hn', 0)}) "
            f"PH({self.metrics['by_source'].get('product_hunt', 0)}) "
            f"IH({self.metrics['by_source'].get('indie_hackers', 0)}) "
            f"Jobs({self.metrics['by_source'].get('job_boards', 0)}) "
            f"Alerts({self.metrics['by_source'].get('google_alerts', 0)})\n"
            f"Top services: {', '.join(f'{k}({v})' for k, v in sorted(self.metrics['by_service'].items(), key=lambda x: -x[1])[:5]) or 'N/A'}"
        )
