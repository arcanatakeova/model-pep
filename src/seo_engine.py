"""ARCANA AI — Programmatic SEO Content Engine.

Auto-generates SEO-optimized articles. Publishes to CMS/static sites.
Monetizes via AdSense/Mediavine ($15-40 RPM).

One documented case: $3,400/mo to $250K/mo in 3 months with programmatic SEO.
Conservative target: 500 articles × $15 RPM × 1K views/article = $7,500/mo.

Content niches aligned with Arcana Operations:
- AI tool comparisons ("Best AI agents for [industry]")
- Automation guides ("How to automate [business process]")
- Business tool reviews with affiliate links
- Industry-specific AI use cases
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.llm import LLM, Tier
from src.memory import Memory

if TYPE_CHECKING:
    from src.fulfillment import FulfillmentEngine

logger = logging.getLogger("arcana.seo")


class SEOEngine:
    """Generate SEO-optimized content at scale."""

    def __init__(
        self,
        llm: LLM,
        memory: Memory,
        fulfillment: "FulfillmentEngine | None" = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.fulfillment = fulfillment
        self._published: list[dict[str, Any]] = []

    async def generate_article(
        self, keyword: str, intent: str = "informational", word_count: int = 1500
    ) -> dict[str, Any]:
        """Generate a full SEO article targeting a keyword."""
        result = await self.llm.ask_json(
            f"Write a comprehensive, SEO-optimized article.\n\n"
            f"Target keyword: {keyword}\n"
            f"Search intent: {intent}\n"
            f"Target word count: {word_count}\n\n"
            f"Requirements:\n"
            f"- Title tag (under 60 chars, keyword near front)\n"
            f"- Meta description (under 155 chars, compelling)\n"
            f"- H1, H2, H3 structure with keyword variants\n"
            f"- Introduction with hook (address the reader's problem)\n"
            f"- 4-6 main sections with actionable content\n"
            f"- FAQ section (3-5 questions, schema-ready)\n"
            f"- Conclusion with CTA\n"
            f"- Natural keyword usage (no stuffing)\n"
            f"- Internal link placeholders: [INTERNAL: topic]\n"
            f"- Where relevant, mention Arcana Operations services naturally\n\n"
            f"Return JSON: {{"
            f'"title": str, '
            f'"meta_description": str, '
            f'"slug": str, '
            f'"content_html": str, '
            f'"word_count": int, '
            f'"faqs": [{{"question": str, "answer": str}}], '
            f'"related_keywords": [str], '
            f'"internal_links_suggested": [str]}}',
            tier=Tier.SONNET,
        )

        # Auto-generate clean slug if missing
        if result and not result.get("slug"):
            from src.toolkit import slugify
            result["slug"] = slugify(result.get("title", keyword))

        # Sanitize the HTML content
        if result.get("content_html"):
            from src.toolkit import sanitize_html
            result["content_html"] = sanitize_html(result["content_html"])

        self.memory.log(
            f"[SEO] Article generated: {keyword} ({result.get('word_count', 0)} words)",
            "SEO",
        )
        return result

    async def generate_keyword_cluster(self, seed_topic: str, niche: str) -> dict[str, Any]:
        """Generate a cluster of related keywords to target."""
        result = await self.llm.ask_json(
            f"Generate a keyword cluster for programmatic SEO.\n\n"
            f"Seed topic: {seed_topic}\n"
            f"Niche: {niche}\n\n"
            f"Create 20 keyword variations using these templates:\n"
            f"- 'Best [tool] for [industry]'\n"
            f"- 'How to [action] with AI'\n"
            f"- '[Industry] [process] automation guide'\n"
            f"- '[Tool A] vs [Tool B]'\n"
            f"- '[Industry] [pain point] solution'\n\n"
            f"For each keyword, estimate:\n"
            f"- Search volume tier (low/medium/high)\n"
            f"- Competition (low/medium/high)\n"
            f"- Monetization potential (adsense/affiliate/consulting lead)\n\n"
            f"Return JSON: {{"
            f'"cluster_name": str, '
            f'"keywords": [{{"keyword": str, "volume": str, "competition": str, '
            f'"monetization": str, "priority": int}}]}}',
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[SEO] Keyword cluster: {seed_topic} — {len(result.get('keywords', []))} keywords",
            "SEO",
        )
        return result

    async def generate_batch(self, keywords: list[str], intent: str = "informational") -> list[dict[str, Any]]:
        """Generate articles for a batch of keywords."""
        articles = []
        for kw in keywords:
            try:
                article = await self.generate_article(kw, intent)
                articles.append(article)
            except Exception as exc:
                logger.error("Failed to generate article for '%s': %s", kw, exc)
        return articles

    async def generate_product_review(
        self, product_name: str, category: str, affiliate_link: str = ""
    ) -> dict[str, Any]:
        """Generate a product review article with affiliate link."""
        result = await self.llm.ask_json(
            f"Write a detailed, honest product review article.\n\n"
            f"Product: {product_name}\n"
            f"Category: {category}\n"
            f"Affiliate link: {affiliate_link or 'N/A'}\n\n"
            f"Requirements:\n"
            f"- Balanced review (not a sales page)\n"
            f"- Pros and cons lists\n"
            f"- Use case scenarios (who it's best for)\n"
            f"- Pricing breakdown\n"
            f"- Alternatives mentioned\n"
            f"- Verdict section\n"
            f"- If affiliate link provided, include naturally (1-2 times)\n"
            f"- 1000-1500 words\n\n"
            f"Return JSON: {{"
            f'"title": str, '
            f'"meta_description": str, '
            f'"slug": str, '
            f'"content_html": str, '
            f'"rating": float (1-5), '
            f'"pros": [str], '
            f'"cons": [str], '
            f'"verdict": str}}',
            tier=Tier.SONNET,
        )

        self.memory.log(
            f"[SEO] Product review: {product_name} ({result.get('rating', 0)}/5)",
            "SEO",
        )
        return result

    # ── Publishing ──────────────────────────────────────────────────

    async def generate_and_publish(
        self,
        keyword: str,
        intent: str = "informational",
        word_count: int = 1500,
        site_url: str = "",
        vercel_token: str = "",
    ) -> dict[str, Any]:
        """Generate an SEO article and publish it via the fulfillment engine."""
        article = await self.generate_article(keyword, intent, word_count)

        if self.fulfillment is None:
            logger.warning("No fulfillment engine configured — article generated but not published")
            return {"status": "not_published", "reason": "no_fulfillment_engine", "article": article}

        publish_result = await self.fulfillment.publish_seo_article(
            article_html=article.get("content_html", ""),
            title=article.get("title", keyword),
            slug=article.get("slug", keyword.lower().replace(" ", "-")),
            site_url=site_url,
            vercel_token=vercel_token,
        )

        record = {
            "keyword": keyword,
            "title": article.get("title", ""),
            "slug": article.get("slug", ""),
            "word_count": article.get("word_count", 0),
            "published_at": datetime.now(timezone.utc).isoformat(),
            "publish_status": publish_result.get("status", "unknown"),
            "url": publish_result.get("url", ""),
        }
        self._published.append(record)

        self.memory.log(
            f"[SEO] Published: {record['title']} → {publish_result.get('status')}",
            "SEO",
        )
        return {"status": "published", "article": article, "publish_result": publish_result}

    async def batch_publish(
        self,
        keywords: list[str],
        intent: str = "informational",
        site_url: str = "",
        vercel_token: str = "",
    ) -> dict[str, Any]:
        """Generate and publish a batch of SEO articles (weekly content batch)."""
        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0

        for kw in keywords:
            try:
                result = await self.generate_and_publish(
                    kw, intent, site_url=site_url, vercel_token=vercel_token,
                )
                results.append({"keyword": kw, **result})
                if result.get("status") == "published":
                    succeeded += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.error("Batch publish failed for '%s': %s", kw, exc)
                results.append({"keyword": kw, "status": "error", "error": str(exc)})
                failed += 1

        self.memory.log(
            f"[SEO] Batch publish: {succeeded}/{len(keywords)} succeeded, {failed} failed",
            "SEO",
        )
        return {"total": len(keywords), "succeeded": succeeded, "failed": failed, "results": results}

    def get_published_articles(self) -> list[dict[str, Any]]:
        """Return the list of articles that have been published this session."""
        return list(self._published)

    def generate_sitemap(self, base_url: str = "https://arcana.operations") -> str:
        """Generate a sitemap XML string from all published articles."""
        urls_xml = []
        for article in self._published:
            slug = article.get("slug", "")
            published_at = article.get("published_at", datetime.now(timezone.utc).isoformat())
            loc = article.get("url") or f"{base_url.rstrip('/')}/articles/{slug}"
            urls_xml.append(
                f"  <url>\n"
                f"    <loc>{loc}</loc>\n"
                f"    <lastmod>{published_at[:10]}</lastmod>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"    <priority>0.7</priority>\n"
                f"  </url>"
            )

        sitemap = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls_xml) + "\n"
            "</urlset>"
        )

        self.memory.log(
            f"[SEO] Sitemap generated with {len(self._published)} URLs",
            "SEO",
        )
        return sitemap

    async def research_keywords(self, niche: str) -> dict[str, Any]:
        """Use LLM to research keyword clusters with search volume estimates for a niche."""
        result = await self.llm.ask_json(
            f"You are an expert SEO keyword researcher.\n\n"
            f"Research the niche: {niche}\n\n"
            f"Generate 5 keyword clusters (themes), each with 5-8 specific keywords.\n"
            f"For each keyword, estimate:\n"
            f"- Monthly search volume (numeric estimate)\n"
            f"- Keyword difficulty (1-100)\n"
            f"- CPC estimate (USD)\n"
            f"- Search intent (informational/commercial/transactional/navigational)\n"
            f"- Content format recommendation (guide/listicle/comparison/review/tutorial)\n\n"
            f"Also provide:\n"
            f"- Top 3 quick-win keywords (low difficulty, decent volume)\n"
            f"- Top 3 high-value keywords (high CPC or transactional intent)\n"
            f"- Suggested content calendar (which to publish first)\n\n"
            f"Return JSON: {{"
            f'"niche": str, '
            f'"clusters": [{{"theme": str, "keywords": [{{'
            f'"keyword": str, "volume": int, "difficulty": int, '
            f'"cpc": float, "intent": str, "format": str}}]}}], '
            f'"quick_wins": [str], '
            f'"high_value": [str], '
            f'"content_calendar": [{{"week": int, "keywords": [str], "rationale": str}}]}}',
            tier=Tier.SONNET,
        )

        total_keywords = sum(len(c.get("keywords", [])) for c in result.get("clusters", []))
        self.memory.log(
            f"[SEO] Keyword research for '{niche}': "
            f"{len(result.get('clusters', []))} clusters, {total_keywords} keywords",
            "SEO",
        )
        return result
