"""
News Sentiment Analyzer
=======================
Monitors news sources for catalysts affecting prediction market outcomes.
"""
from __future__ import annotations
import logging
import os
import re
import time
from typing import Optional

import requests

from .models import PolyMarket

logger = logging.getLogger(__name__)


class NewsSentimentAnalyzer:
    """News monitoring + NLP sentiment scoring for prediction markets."""

    def __init__(self):
        self._newsapi_key = os.getenv("NEWSAPI_KEY", "")
        self._session = requests.Session()
        self._cache: dict[str, tuple] = {}
        self._cache_ttl = 300  # 5 min

    def get_relevant_news(self, market: PolyMarket, limit: int = 5) -> list[dict]:
        """Fetch news headlines relevant to a market question."""
        keywords = self._extract_keywords(market.question)
        if not keywords:
            return []

        query = " ".join(keywords[:3])
        cache_key = f"news:{query}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached[:limit]

        articles: list[dict] = []

        # Try NewsAPI first (requires key)
        if self._newsapi_key:
            articles = self._fetch_newsapi(query)

        # Fallback to Google News RSS (no key needed)
        if not articles:
            articles = self._fetch_google_rss(query)

        self._set_cache(cache_key, articles)
        return articles[:limit]

    def get_breaking_news(self, keywords: list[str]) -> list[dict]:
        """Fetch breaking news for given keywords."""
        query = " ".join(keywords[:3])
        return self._fetch_google_rss(query)[:5]

    def score_sentiment(self, headlines: list[str], market_question: str) -> float:
        """
        Score news sentiment relative to market question.
        Returns float -1.0 (bearish) to +1.0 (bullish).
        """
        if not headlines:
            return 0.0

        # Simple keyword-based sentiment
        positive_words = {
            "win", "wins", "winning", "victory", "success", "approve",
            "approved", "pass", "passes", "gain", "rise", "surge",
            "confirmed", "likely", "certain", "agreement", "deal",
        }
        negative_words = {
            "lose", "loss", "losing", "defeat", "fail", "reject",
            "rejected", "block", "blocked", "drop", "fall", "crash",
            "unlikely", "doubt", "denied", "collapse", "cancel",
        }

        total_score = 0.0
        for headline in headlines:
            words = set(headline.lower().split())
            pos = len(words & positive_words)
            neg = len(words & negative_words)
            if pos + neg > 0:
                total_score += (pos - neg) / (pos + neg)

        if headlines:
            total_score /= len(headlines)

        return max(-1.0, min(1.0, total_score))

    def detect_catalyst(self, market: PolyMarket) -> Optional[dict]:
        """
        Detect if breaking news could be a catalyst for market movement.
        Returns catalyst dict if found, None otherwise.
        """
        articles = self.get_relevant_news(market, limit=5)
        if not articles:
            return None

        # Check for very recent articles (< 1 hour old)
        recent = [a for a in articles if a.get("age_minutes", 999) < 60]
        if not recent:
            return None

        headlines = [a.get("title", "") for a in recent]
        sentiment = self.score_sentiment(headlines, market.question)

        if abs(sentiment) < 0.3:
            return None

        return {
            "headlines": headlines,
            "sentiment": sentiment,
            "direction": "YES" if sentiment > 0 else "NO",
            "age_minutes": min(a.get("age_minutes", 999) for a in recent),
            "source": recent[0].get("source", "unknown"),
        }

    # ─── Internal ──────────────────────────────────────────────────────────

    def _extract_keywords(self, question: str) -> list[str]:
        """Extract searchable keywords from a market question."""
        # Remove common question words and short words
        stop_words = {
            "will", "the", "be", "is", "are", "was", "were", "has", "have",
            "had", "do", "does", "did", "can", "could", "would", "should",
            "may", "might", "shall", "this", "that", "these", "those",
            "and", "or", "but", "not", "for", "with", "from", "by",
            "at", "in", "on", "of", "to", "a", "an", "it", "its",
            "before", "after", "than", "more", "less", "what", "when",
            "where", "who", "whom", "which", "how", "any", "all",
        }
        # Remove punctuation
        clean = re.sub(r'[^\w\s]', ' ', question)
        words = [w for w in clean.split() if w.lower() not in stop_words and len(w) > 2]
        return words[:5]

    def _fetch_newsapi(self, query: str) -> list[dict]:
        """Fetch from NewsAPI.org."""
        try:
            resp = self._session.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "apiKey": self._newsapi_key,
                },
                timeout=8,
            )
            if not resp.ok:
                return []
            data = resp.json()
            articles = []
            for a in data.get("articles", []):
                published = a.get("publishedAt", "")
                articles.append({
                    "title": a.get("title", ""),
                    "source": a.get("source", {}).get("name", ""),
                    "url": a.get("url", ""),
                    "published": published,
                    "age_minutes": self._age_minutes(published),
                })
            return articles
        except Exception as e:
            logger.debug("NewsAPI error: %s", e)
            return []

    def _fetch_google_rss(self, query: str) -> list[dict]:
        """Fetch from Google News RSS (no key needed)."""
        try:
            import feedparser
        except ImportError:
            logger.debug("feedparser not installed -- Google News RSS unavailable")
            return []

        try:
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)
            articles = []
            for entry in feed.entries[:10]:
                published = entry.get("published", "")
                articles.append({
                    "title": entry.get("title", ""),
                    "source": entry.get("source", {}).get("title", "Google News"),
                    "url": entry.get("link", ""),
                    "published": published,
                    "age_minutes": self._age_minutes(published),
                })
            return articles
        except Exception as e:
            logger.debug("Google News RSS error: %s", e)
            return []

    def _age_minutes(self, date_str: str) -> int:
        """Calculate age of article in minutes."""
        if not date_str:
            return 9999
        try:
            from datetime import datetime, timezone
            # Try ISO format
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - dt
            return max(0, int(delta.total_seconds() / 60))
        except (ValueError, TypeError):
            return 9999

    def _get_cached(self, key: str):
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return val
        return None

    def _set_cache(self, key: str, val):
        self._cache[key] = (val, time.time())
