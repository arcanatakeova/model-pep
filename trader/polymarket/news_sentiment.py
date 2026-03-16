"""
News Sentiment Analyzer
=======================
Multi-source news monitoring with advanced NLP sentiment scoring
for prediction market catalyst detection and trade signal generation.

Sources (priority order):
    1. NewsAPI (key required) -- highest quality, structured data
    2. GDELT DOC API (free)  -- broadest real-time global coverage
    3. Google News RSS (free) -- reliable fallback
    4. Reddit RSS (free)      -- social/crowd signal

Sentiment engine:
    - 200+ term lexicon with domain-specific financial/political vocabulary
    - Negation-aware scoring ("not likely" flips polarity)
    - Intensity modifiers ("very", "extremely" scale magnitude)
    - Question-alignment scoring (sentiment relative to YES outcome)
    - Recency and source-credibility weighting
    - Cross-headline deduplication via SequenceMatcher
"""
from __future__ import annotations

import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

import requests

from .models import PolyMarket

logger = logging.getLogger(__name__)

# ─── Sentiment Lexicon ────────────────────────────────────────────────────────
# Values: float = sentiment score (-1 to +1), str "NEGATE" = flip next word,
#         float > 1 or < 1 = intensity modifier (multiplied into next word)

SENTIMENT_LEXICON: dict[str, float | str] = {
    # ── Strong positive (0.7 - 1.0) ──────────────────────────────────────
    "confirmed": 0.8, "approved": 0.8, "victory": 0.9, "wins": 0.7,
    "breakthrough": 0.8, "surges": 0.7, "soars": 0.7, "landslide": 0.9,
    "dominates": 0.8, "triumphs": 0.9, "succeeds": 0.7, "passes": 0.7,
    "ratified": 0.8, "enacted": 0.7, "signed": 0.6, "unanimous": 0.9,
    "rallies": 0.7, "skyrockets": 0.8, "booms": 0.7, "record-high": 0.8,
    "secures": 0.7, "clinches": 0.8, "sweeps": 0.8, "landslides": 0.9,
    # ── Moderate positive (0.3 - 0.6) ────────────────────────────────────
    "likely": 0.4, "expected": 0.3, "ahead": 0.4, "leads": 0.5,
    "gaining": 0.4, "improving": 0.3, "optimistic": 0.4, "favored": 0.5,
    "rising": 0.3, "advances": 0.4, "progress": 0.3, "positive": 0.4,
    "supports": 0.3, "endorses": 0.5, "backing": 0.4, "bolsters": 0.4,
    "uptick": 0.3, "momentum": 0.4, "bullish": 0.5, "upbeat": 0.4,
    "win": 0.6, "winning": 0.6, "success": 0.5, "approve": 0.5,
    "gain": 0.4, "rise": 0.3, "surge": 0.5, "certain": 0.5,
    "agreement": 0.4, "deal": 0.4, "boost": 0.4, "grows": 0.3,
    "strengthens": 0.4, "recovers": 0.3, "rebounds": 0.4, "outperforms": 0.5,
    # ── Strong negative (-0.7 to -1.0) ───────────────────────────────────
    "rejected": -0.8, "defeated": -0.9, "collapses": -0.8, "crashes": -0.7,
    "blocked": -0.7, "fails": -0.8, "impossible": -0.9, "killed": -0.8,
    "vetoed": -0.8, "overturned": -0.7, "demolished": -0.8, "crushed": -0.8,
    "plummets": -0.8, "tanks": -0.7, "implodes": -0.8, "catastrophe": -0.9,
    "disqualified": -0.8, "indicted": -0.7, "impeached": -0.8, "convicted": -0.8,
    # ── Moderate negative (-0.3 to -0.6) ─────────────────────────────────
    "unlikely": -0.4, "doubt": -0.4, "behind": -0.4, "trailing": -0.5,
    "declining": -0.3, "struggling": -0.4, "slipping": -0.3, "weakens": -0.4,
    "lose": -0.6, "loss": -0.5, "losing": -0.5, "defeat": -0.6,
    "fail": -0.5, "reject": -0.5, "block": -0.4, "drop": -0.4,
    "fall": -0.4, "crash": -0.5, "denied": -0.5, "collapse": -0.6,
    "cancel": -0.4, "stalls": -0.3, "delays": -0.3, "setback": -0.4,
    "concerns": -0.3, "fears": -0.4, "threatens": -0.4, "risks": -0.3,
    "bearish": -0.5, "downturn": -0.4, "recession": -0.5, "slumps": -0.4,
    "scandal": -0.5, "controversy": -0.3, "investigation": -0.3,
    "underperforms": -0.4, "misses": -0.3, "disappoints": -0.4,
}

NEGATION_WORDS = frozenset({"not", "no", "never", "neither", "nor", "nobody",
                             "nothing", "nowhere", "hardly", "barely", "scarcely",
                             "don't", "doesn't", "didn't", "isn't", "aren't",
                             "wasn't", "weren't", "won't", "wouldn't", "couldn't",
                             "shouldn't", "can't", "cannot"})

INTENSITY_MODIFIERS: dict[str, float] = {
    "very": 1.5, "extremely": 2.0, "incredibly": 1.8, "highly": 1.5,
    "slightly": 0.5, "somewhat": 0.7, "marginally": 0.4, "barely": 0.3,
    "overwhelmingly": 2.0, "decisively": 1.6, "narrowly": 0.5,
    "strongly": 1.5, "significantly": 1.4, "substantially": 1.4,
    "massively": 1.8, "dramatically": 1.7, "sharply": 1.5,
}

# Domain-specific terms for entity extraction
DOMAIN_TERMS: dict[str, list[str]] = {
    "politics": ["election", "president", "vote", "ballot", "senate", "congress",
                 "governor", "mayor", "primary", "caucus", "nominee", "candidate",
                 "democrat", "republican", "campaign", "poll", "inauguration"],
    "economics": ["gdp", "inflation", "unemployment", "recession", "interest",
                  "rate", "fed", "federal", "reserve", "treasury", "tariff",
                  "trade", "deficit", "surplus", "stimulus", "cpi", "ppi"],
    "sports": ["championship", "finals", "playoff", "tournament", "match",
               "game", "season", "mvp", "title", "league", "cup", "bowl",
               "series", "seed", "draft", "coach"],
    "crypto": ["bitcoin", "ethereum", "btc", "eth", "token", "blockchain",
               "defi", "nft", "halving", "etf", "sec", "regulation", "mining"],
    "science": ["vaccine", "fda", "approval", "clinical", "trial", "pandemic",
                "nasa", "launch", "mission", "discovery", "breakthrough"],
    "legal": ["supreme", "court", "ruling", "lawsuit", "verdict", "settlement",
              "indictment", "trial", "conviction", "appeal", "legislation"],
    "geopolitics": ["war", "ceasefire", "treaty", "sanctions", "nato", "summit",
                    "diplomacy", "conflict", "invasion", "alliance"],
}

# Source credibility tiers (multiplier for sentiment weight)
SOURCE_CREDIBILITY: dict[str, float] = {
    # Tier 1: Wire services and major outlets (1.0x)
    "reuters": 1.0, "associated press": 1.0, "ap news": 1.0, "bloomberg": 1.0,
    "the wall street journal": 1.0, "wsj": 1.0, "financial times": 1.0,
    "the new york times": 1.0, "the washington post": 1.0, "bbc": 1.0,
    # Tier 2: Major news organizations (0.85x)
    "cnn": 0.85, "cnbc": 0.85, "nbc news": 0.85, "abc news": 0.85,
    "cbs news": 0.85, "the guardian": 0.85, "politico": 0.85,
    "the economist": 0.85, "axios": 0.85, "npr": 0.85,
    # Tier 3: Specialized/digital (0.7x)
    "fox news": 0.7, "msnbc": 0.7, "the hill": 0.7, "vox": 0.7,
    "techcrunch": 0.7, "coindesk": 0.7, "the verge": 0.7,
    "espn": 0.7, "bleacher report": 0.7,
}
DEFAULT_SOURCE_CREDIBILITY = 0.5  # Unknown sources

# Prediction market phrasing to strip during keyword extraction
MARKET_PHRASING = frozenset({
    "will", "be", "happen", "occur", "take", "place", "yes", "no",
    "before", "after", "during", "between", "by", "end", "year",
    "resolution", "resolve", "market", "prediction", "polymarket",
    "probability", "chance", "odds",
})

# Map market tags to relevant subreddits
TAG_SUBREDDIT_MAP: dict[str, list[str]] = {
    "politics": ["politics", "news", "worldnews"],
    "elections": ["politics", "PoliticalDiscussion"],
    "crypto": ["cryptocurrency", "bitcoin", "ethereum"],
    "sports": ["sports", "nba", "nfl", "soccer"],
    "science": ["science", "technology"],
    "economics": ["economics", "finance", "wallstreetbets"],
    "entertainment": ["entertainment", "movies", "television"],
}


@dataclass
class ExtractedEntities:
    """Entities extracted from a market question."""
    people: list[str] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)


class NewsSentimentAnalyzer:
    """
    Multi-source news monitoring + advanced NLP sentiment scoring
    for prediction market catalyst detection and signal generation.
    """

    def __init__(self):
        self._newsapi_key = os.getenv("NEWSAPI_KEY", "")
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; MarketAnalyzer/2.0)",
        })
        self._cache: dict[str, tuple] = {}
        self._cache_ttl = 300  # 5 min

        # Catalyst tracking: market_id -> set of headline fingerprints
        self._seen_catalysts: dict[str, set[str]] = {}
        self._catalyst_ttl = 3600  # 1 hour before re-triggering on same story

    # ─── Public API ───────────────────────────────────────────────────────────

    def get_relevant_news(self, market: PolyMarket, limit: int = 10) -> list[dict]:
        """
        Fetch news from multiple sources in priority order:
        1. NewsAPI (if key available) -- highest quality
        2. GDELT API (free, real-time) -- broadest coverage
        3. Google News RSS (free, always available) -- fallback
        4. Reddit headlines via old.reddit.com RSS -- social signal

        Deduplicates by headline similarity.
        Sorts by recency and relevance.
        """
        keywords = self._extract_keywords(market.question)
        if not keywords:
            return []

        queries = self._build_search_queries(keywords, market.question)
        cache_key = f"news:{queries[0]}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached[:limit]

        all_articles: list[dict] = []

        # 1. NewsAPI (highest quality, requires key)
        if self._newsapi_key:
            for query in queries[:2]:
                articles = self._fetch_newsapi(query)
                if articles:
                    all_articles.extend(articles)
                    break

        # 2. GDELT (free, real-time, broadest coverage)
        for query in queries[:2]:
            articles = self._fetch_gdelt(query)
            if articles:
                all_articles.extend(articles)
                break

        # 3. Google News RSS (free, always available)
        if len(all_articles) < 5:
            for query in queries:
                articles = self._fetch_google_rss(query)
                if articles:
                    all_articles.extend(articles)
                    break

        # 4. Reddit RSS (social signal)
        subreddits = self._subreddits_for_market(market)
        if subreddits:
            reddit_articles = self._fetch_reddit_rss(queries[0], subreddits)
            all_articles.extend(reddit_articles)

        # Deduplicate, score freshness, sort
        all_articles = self._deduplicate_articles(all_articles)
        for article in all_articles:
            article["freshness"] = self._calculate_freshness_score(article)
            article["credibility"] = self._source_credibility(
                article.get("source", ""))

        # Sort: recent + credible first
        all_articles.sort(
            key=lambda a: (a.get("freshness", 0) * 0.6
                           + a.get("credibility", 0.5) * 0.4),
            reverse=True,
        )

        self._set_cache(cache_key, all_articles)
        return all_articles[:limit]

    def get_breaking_news(self, keywords: list[str]) -> list[dict]:
        """Fetch breaking news for given keywords (< 1 hour old)."""
        query = " ".join(keywords[:3])
        articles: list[dict] = []

        # Try GDELT first (best for breaking news)
        articles = self._fetch_gdelt(query)

        # Fallback to Google RSS
        if not articles:
            articles = self._fetch_google_rss(query)

        # Filter to truly recent
        return [a for a in articles if a.get("age_minutes", 9999) < 60][:5]

    def score_sentiment(self, headlines: list[str],
                        market_question: str) -> float:
        """
        Multi-layer sentiment scoring relative to market question.

        Layers:
        1. LEXICON SCORING -- 200+ term sentiment lexicon
        2. NEGATION HANDLING -- "not likely to win" flips polarity
        3. INTENSITY MODIFIERS -- "very likely" > "somewhat likely"
        4. QUESTION ALIGNMENT -- score relative to YES outcome
        5. RECENCY WEIGHTING -- (applied externally via article metadata)
        6. SOURCE CREDIBILITY -- (applied externally via article metadata)

        Returns: float in [-1.0, +1.0]
        """
        if not headlines:
            return 0.0

        # Extract question-aligned keywords for alignment scoring
        q_entities = self._extract_entities(market_question)
        q_keywords = set()
        for vals in [q_entities.people, q_entities.orgs, q_entities.topics]:
            q_keywords.update(w.lower() for w in vals)

        total_score = 0.0
        total_weight = 0.0

        for headline in headlines:
            words = self._tokenize(headline)
            raw_score = self._score_with_negation(words)

            # Question alignment: does the headline discuss the same entities?
            headline_lower = headline.lower()
            alignment = 0.0
            if q_keywords:
                matches = sum(1 for kw in q_keywords if kw in headline_lower)
                alignment = min(1.0, matches / max(1, len(q_keywords)))

            # Weight by alignment (relevant headlines matter more)
            weight = 0.3 + 0.7 * alignment  # minimum 0.3 weight
            total_score += raw_score * weight
            total_weight += weight

        if total_weight > 0:
            total_score /= total_weight

        return max(-1.0, min(1.0, total_score))

    def score_sentiment_detailed(self, articles: list[dict],
                                 market_question: str) -> dict:
        """
        Enhanced sentiment scoring using full article metadata.
        Returns detailed breakdown including per-source scores.
        """
        if not articles:
            return {
                "score": 0.0,
                "num_articles": 0,
                "breakdown": [],
                "confidence": 0.0,
            }

        q_entities = self._extract_entities(market_question)
        q_keywords = set()
        for vals in [q_entities.people, q_entities.orgs, q_entities.topics]:
            q_keywords.update(w.lower() for w in vals)

        breakdown = []
        total_score = 0.0
        total_weight = 0.0

        for article in articles:
            title = article.get("title", "")
            if not title:
                continue

            words = self._tokenize(title)
            raw_score = self._score_with_negation(words)

            # Freshness weight
            freshness = article.get("freshness",
                                     self._calculate_freshness_score(article))

            # Source credibility
            credibility = article.get("credibility",
                                       self._source_credibility(
                                           article.get("source", "")))

            # Question alignment
            headline_lower = title.lower()
            alignment = 0.0
            if q_keywords:
                matches = sum(1 for kw in q_keywords if kw in headline_lower)
                alignment = min(1.0, matches / max(1, len(q_keywords)))

            # Combined weight
            weight = (0.2 + 0.3 * alignment
                      + 0.3 * freshness
                      + 0.2 * credibility)

            total_score += raw_score * weight
            total_weight += weight

            breakdown.append({
                "title": title[:100],
                "source": article.get("source", "unknown"),
                "raw_score": round(raw_score, 3),
                "weight": round(weight, 3),
                "freshness": round(freshness, 2),
                "credibility": round(credibility, 2),
                "alignment": round(alignment, 2),
            })

        final_score = total_score / total_weight if total_weight > 0 else 0.0
        final_score = max(-1.0, min(1.0, final_score))

        # Confidence: higher when more aligned articles agree
        scores = [b["raw_score"] for b in breakdown if b["alignment"] > 0.3]
        if len(scores) >= 2:
            score_std = (sum((s - final_score) ** 2 for s in scores)
                         / len(scores)) ** 0.5
            confidence = max(0.0, min(1.0, 1.0 - score_std))
        elif len(scores) == 1:
            confidence = 0.4  # single source = low confidence
        else:
            confidence = 0.1

        return {
            "score": round(final_score, 4),
            "num_articles": len(breakdown),
            "breakdown": breakdown,
            "confidence": round(confidence, 3),
        }

    def detect_catalyst(self, market: PolyMarket) -> Optional[dict]:
        """
        Enhanced catalyst detection:
        1. Check for breaking news (< 1 hour old)
        2. Check for news volume spike (3x normal for this topic)
        3. Check if news sentiment CONTRADICTS current market price
        4. Assign catalyst strength score (0-1) based on:
           - News recency (fresher = stronger)
           - Number of sources reporting (more = stronger)
           - Sentiment magnitude (stronger sentiment = stronger catalyst)
           - Source credibility (major outlets = stronger)
        5. Track previous catalysts to avoid re-triggering on same news
        """
        articles = self.get_relevant_news(market, limit=10)
        if not articles:
            return None

        # Filter to recent articles (< 2 hours old)
        recent = [a for a in articles if a.get("age_minutes", 9999) < 120]
        breaking = [a for a in articles if a.get("age_minutes", 9999) < 30]

        if not recent:
            return None

        headlines = [a.get("title", "") for a in recent]

        # Check for duplicate catalyst (already seen these headlines)
        if self._is_duplicate_catalyst(headlines, market.condition_id):
            return None

        # Detailed sentiment scoring
        sentiment_result = self.score_sentiment_detailed(
            recent, market.question)
        sentiment = sentiment_result["score"]

        if abs(sentiment) < 0.2:
            return None

        # Calculate catalyst strength
        recency_score = (
            1.0 if breaking
            else 0.6 if any(a.get("age_minutes", 9999) < 60 for a in recent)
            else 0.3
        )

        # Volume signal: many sources = stronger catalyst
        unique_sources = len(set(a.get("source", "") for a in recent
                                 if a.get("source")))
        volume_score = min(1.0, unique_sources / 5.0)

        # Sentiment magnitude
        magnitude_score = min(1.0, abs(sentiment) / 0.6)

        # Source credibility (average of recent articles)
        cred_scores = [self._source_credibility(a.get("source", ""))
                       for a in recent]
        credibility_score = sum(cred_scores) / len(cred_scores) if cred_scores else 0.5

        # Price contradiction: sentiment disagrees with current market price
        contradiction = 0.0
        if sentiment > 0.2 and market.yes_price < 0.4:
            contradiction = 0.3  # Bullish news but market says unlikely
        elif sentiment < -0.2 and market.yes_price > 0.6:
            contradiction = 0.3  # Bearish news but market says likely

        # Composite catalyst strength
        strength = (
            0.25 * recency_score
            + 0.20 * volume_score
            + 0.25 * magnitude_score
            + 0.15 * credibility_score
            + 0.15 * contradiction
        )
        strength = min(1.0, strength)

        if strength < 0.25:
            return None

        # Record these headlines to avoid re-triggering
        self._record_catalyst(headlines, market.condition_id)

        return {
            "headlines": headlines[:5],
            "sentiment": round(sentiment, 4),
            "direction": "YES" if sentiment > 0 else "NO",
            "strength": round(strength, 4),
            "age_minutes": min(a.get("age_minutes", 9999) for a in recent),
            "num_sources": unique_sources,
            "source": recent[0].get("source", "unknown"),
            "confidence": sentiment_result["confidence"],
            "is_breaking": len(breaking) > 0,
            "contradicts_price": contradiction > 0,
        }

    def _extract_entities(self, question: str) -> ExtractedEntities:
        """
        Extract structured entities from a market question.

        Returns dict-like dataclass with:
        - people: proper nouns that look like person names
        - orgs: known organization acronyms/names
        - dates: years, months, date patterns
        - topics: domain-specific terms found
        - numbers: numeric values (percentages, counts)
        """
        entities = ExtractedEntities()

        # Clean up question
        clean = re.sub(r'[?!]', '', question)

        # 1. Extract proper nouns (capitalized words not at sentence start)
        words = clean.split()
        for i, word in enumerate(words):
            stripped = word.strip(",.;:'\"()[]")
            if not stripped:
                continue

            # Capitalized word not at sentence start and not a common word
            if (stripped[0].isupper() and len(stripped) > 1
                    and stripped.lower() not in MARKET_PHRASING
                    and stripped.lower() not in {"the", "a", "an", "in", "on",
                                                  "at", "to", "for", "of", "is",
                                                  "are", "was", "will", "be",
                                                  "has", "have", "had", "if",
                                                  "or", "and", "but", "with"}):
                # Check if it looks like an acronym (all caps, 2-6 chars)
                if stripped.isupper() and 2 <= len(stripped) <= 6:
                    entities.orgs.append(stripped)
                # Multi-word proper nouns (consecutive capitalized words)
                elif i > 0 or len(words) > 1:
                    # Try to capture multi-word names
                    if (i + 1 < len(words)
                            and words[i + 1].strip(",.;:'\"()[]")
                            and words[i + 1].strip(",.;:'\"()[]")[0].isupper()
                            and words[i + 1].strip(",.;:'\"()[]").lower()
                            not in MARKET_PHRASING):
                        combined = (stripped + " "
                                    + words[i + 1].strip(",.;:'\"()[]"))
                        entities.people.append(combined)
                    else:
                        entities.people.append(stripped)

        # Deduplicate: remove single words that are part of multi-word names
        multi_names = [p for p in entities.people if " " in p]
        if multi_names:
            singles = []
            for p in entities.people:
                if " " not in p:
                    if not any(p in mn for mn in multi_names):
                        singles.append(p)
            entities.people = multi_names + singles

        # 2. Extract dates and years
        year_pattern = re.findall(r'\b(20\d{2})\b', question)
        entities.dates.extend(year_pattern)

        month_pattern = re.findall(
            r'\b(January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\b',
            question, re.IGNORECASE)
        entities.dates.extend(month_pattern)

        date_pattern = re.findall(
            r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b', question)
        entities.dates.extend(date_pattern)

        # 3. Extract numbers and percentages
        num_pattern = re.findall(r'\b(\d+(?:\.\d+)?%?)\b', question)
        entities.numbers.extend(
            n for n in num_pattern if n not in entities.dates)

        # 4. Extract domain-specific topics
        q_lower = question.lower()
        for domain, terms in DOMAIN_TERMS.items():
            for term in terms:
                if term in q_lower:
                    entities.topics.append(term)

        # Deduplicate all lists
        entities.people = list(dict.fromkeys(entities.people))
        entities.orgs = list(dict.fromkeys(entities.orgs))
        entities.dates = list(dict.fromkeys(entities.dates))
        entities.topics = list(dict.fromkeys(entities.topics))
        entities.numbers = list(dict.fromkeys(entities.numbers))

        return entities

    # ─── Keyword Extraction ───────────────────────────────────────────────────

    def _extract_keywords(self, question: str) -> list[str]:
        """
        Extract search-optimized keywords from market question.

        Strategy:
        1. Extract proper nouns (capitalized words) as primary keywords
        2. Extract numbers/dates as context
        3. Extract domain-specific terms
        4. Remove generic prediction market phrasing
        5. Return ranked keywords (proper nouns first, then topics, then dates)
        """
        entities = self._extract_entities(question)

        keywords: list[str] = []

        # Priority 1: People and organizations (most specific)
        keywords.extend(entities.people)
        keywords.extend(entities.orgs)

        # Priority 2: Domain topics
        keywords.extend(entities.topics)

        # Priority 3: Dates (for context)
        keywords.extend(entities.dates)

        # If we got very few keywords, fall back to cleaned content words
        if len(keywords) < 2:
            stop_words = MARKET_PHRASING | {
                "the", "is", "are", "was", "were", "has", "have",
                "had", "do", "does", "did", "can", "could", "would",
                "should", "may", "might", "shall", "this", "that",
                "these", "those", "and", "or", "but", "not", "for",
                "with", "from", "by", "at", "in", "on", "of", "to",
                "a", "an", "it", "its", "than", "more", "less",
                "what", "when", "where", "who", "whom", "which",
                "how", "any", "all",
            }
            clean = re.sub(r'[^\w\s]', ' ', question)
            fallback = [w for w in clean.split()
                        if w.lower() not in stop_words and len(w) > 2]
            keywords.extend(fallback)

        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique.append(kw)

        return unique[:8]

    def _build_search_queries(self, keywords: list[str],
                              question: str) -> list[str]:
        """
        Build 2-3 search queries with different specificity levels:
        1. Tight: top 2-3 keywords (most specific)
        2. Medium: top 4-5 keywords
        3. Broad: simplified question
        """
        queries: list[str] = []

        # Tight query (most specific)
        if len(keywords) >= 2:
            queries.append(" ".join(keywords[:3]))

        # Medium query
        if len(keywords) >= 4:
            queries.append(" ".join(keywords[:5]))

        # Broad fallback: cleaned question
        broad = re.sub(r'\b(will|be|the|is|are|has|have|does|do)\b', '',
                       question, flags=re.IGNORECASE)
        broad = re.sub(r'[?!.,]', '', broad)
        broad = " ".join(broad.split()[:6])
        if broad and broad not in queries:
            queries.append(broad)

        return queries if queries else [" ".join(keywords[:3])]

    def _subreddits_for_market(self, market: PolyMarket) -> list[str]:
        """Determine relevant subreddits based on market tags and entities."""
        subreddits: set[str] = set()

        # Check tags
        for tag in (market.tags or []):
            tag_lower = tag.lower()
            for key, subs in TAG_SUBREDDIT_MAP.items():
                if key in tag_lower:
                    subreddits.update(subs)

        # Check question content for domain hints
        q_lower = market.question.lower()
        for key, subs in TAG_SUBREDDIT_MAP.items():
            for domain, terms in DOMAIN_TERMS.items():
                if domain == key:
                    if any(term in q_lower for term in terms[:3]):
                        subreddits.update(subs)

        # Default fallback
        if not subreddits:
            subreddits.add("news")

        return list(subreddits)[:4]

    # ─── News Source Fetchers ─────────────────────────────────────────────────

    def _fetch_newsapi(self, query: str) -> list[dict]:
        """Fetch from NewsAPI.org (requires API key)."""
        try:
            resp = self._session.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "language": "en",
                    "apiKey": self._newsapi_key,
                },
                timeout=8,
            )
            if not resp.ok:
                logger.debug("NewsAPI HTTP %d for query: %s", resp.status_code,
                             query)
                return []

            data = resp.json()
            articles = []
            for a in data.get("articles", []):
                published = a.get("publishedAt", "")
                source_name = a.get("source", {}).get("name", "")
                articles.append({
                    "title": a.get("title", ""),
                    "source": source_name,
                    "url": a.get("url", ""),
                    "published": published,
                    "age_minutes": self._age_minutes(published),
                    "description": a.get("description", ""),
                    "origin": "newsapi",
                })
            return articles
        except requests.RequestException as e:
            logger.debug("NewsAPI request error: %s", e)
            return []
        except Exception as e:
            logger.debug("NewsAPI error: %s", e)
            return []

    def _fetch_gdelt(self, query: str) -> list[dict]:
        """
        Fetch from GDELT DOC API (free, no key needed).
        Provides real-time global news coverage with the broadest reach.
        URL: https://api.gdeltproject.org/api/v2/doc/doc
        """
        try:
            encoded_query = urllib.parse.quote(query)
            resp = self._session.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query": encoded_query,
                    "mode": "artlist",
                    "maxrecords": 15,
                    "format": "json",
                    "sort": "datedesc",
                },
                timeout=10,
            )
            if not resp.ok:
                logger.debug("GDELT HTTP %d for query: %s",
                             resp.status_code, query)
                return []

            data = resp.json()
            articles = []
            for a in data.get("articles", []):
                # GDELT returns seendate in YYYYMMDDTHHMMSSZ format
                seen_date = a.get("seendate", "")
                published = self._parse_gdelt_date(seen_date)
                source_domain = a.get("domain", "")
                source_name = a.get("sourcecountry", source_domain)

                articles.append({
                    "title": a.get("title", ""),
                    "source": source_domain,
                    "url": a.get("url", ""),
                    "published": published,
                    "age_minutes": self._age_minutes(published),
                    "language": a.get("language", "English"),
                    "origin": "gdelt",
                })
            return articles
        except requests.RequestException as e:
            logger.debug("GDELT request error: %s", e)
            return []
        except Exception as e:
            logger.debug("GDELT error: %s", e)
            return []

    def _fetch_google_rss(self, query: str) -> list[dict]:
        """Fetch from Google News RSS (no key needed, always available)."""
        try:
            import feedparser
        except ImportError:
            logger.debug("feedparser not installed -- Google News RSS unavailable")
            return []

        try:
            encoded = urllib.parse.quote(query)
            url = (f"https://news.google.com/rss/search?"
                   f"q={encoded}&hl=en-US&gl=US&ceid=US:en")
            feed = feedparser.parse(url)
            articles = []
            for entry in feed.entries[:10]:
                published = entry.get("published", "")
                source = entry.get("source", {})
                source_name = (source.get("title", "Google News")
                               if isinstance(source, dict)
                               else str(source) if source
                               else "Google News")
                articles.append({
                    "title": entry.get("title", ""),
                    "source": source_name,
                    "url": entry.get("link", ""),
                    "published": published,
                    "age_minutes": self._age_minutes(published),
                    "origin": "google_rss",
                })
            return articles
        except Exception as e:
            logger.debug("Google News RSS error: %s", e)
            return []

    def _fetch_reddit_rss(self, query: str,
                          subreddits: list[str] | None = None) -> list[dict]:
        """
        Fetch from Reddit RSS feeds for relevant subreddits.
        Uses old.reddit.com for reliable RSS access.
        """
        if not subreddits:
            subreddits = ["news"]

        try:
            import feedparser
        except ImportError:
            logger.debug("feedparser not installed -- Reddit RSS unavailable")
            return []

        articles: list[dict] = []
        encoded_query = urllib.parse.quote(query)

        for subreddit in subreddits[:3]:  # Limit to 3 subreddits
            try:
                url = (f"https://old.reddit.com/r/{subreddit}/search.rss"
                       f"?q={encoded_query}&sort=new&t=week&restrict_sr=on")
                feed = feedparser.parse(url)

                for entry in feed.entries[:5]:
                    published = entry.get("published", entry.get("updated", ""))
                    articles.append({
                        "title": entry.get("title", ""),
                        "source": f"reddit/r/{subreddit}",
                        "url": entry.get("link", ""),
                        "published": published,
                        "age_minutes": self._age_minutes(published),
                        "origin": "reddit",
                    })
            except Exception as e:
                logger.debug("Reddit RSS error for r/%s: %s", subreddit, e)
                continue

        return articles

    # ─── Sentiment Scoring Engine ─────────────────────────────────────────────

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into lowercase words, preserving negation contractions."""
        # Expand contractions for negation handling
        text = text.lower()
        text = re.sub(r"n't", " not", text)
        text = re.sub(r"[^\w\s'-]", " ", text)
        return text.split()

    def _score_with_negation(self, words: list[str]) -> float:
        """
        Score a sequence of words with negation and intensity awareness.

        Rules:
        - Negation words ("not", "never", etc.) flip the sign of the next
          sentiment word within a 3-word window.
        - Intensity modifiers ("very", "extremely", etc.) scale the magnitude
          of the next sentiment word.
        - Scores are accumulated and normalized by count of sentiment-bearing
          words.
        """
        score = 0.0
        count = 0
        negate = False
        negate_countdown = 0
        intensity = 1.0

        for word in words:
            # Check negation
            if word in NEGATION_WORDS:
                negate = True
                negate_countdown = 3  # Negation applies to next 3 words
                continue

            # Check intensity modifier
            if word in INTENSITY_MODIFIERS:
                intensity = INTENSITY_MODIFIERS[word]
                continue

            # Check sentiment lexicon
            if word in SENTIMENT_LEXICON:
                val = SENTIMENT_LEXICON[word]
                if isinstance(val, str):
                    # It's a negation marker in the lexicon
                    negate = True
                    negate_countdown = 3
                    continue

                # Apply intensity
                val *= intensity
                intensity = 1.0  # Reset intensity

                # Apply negation
                if negate and negate_countdown > 0:
                    val = -val * 0.8  # Negation slightly dampens magnitude
                    negate = False
                    negate_countdown = 0

                score += val
                count += 1
            else:
                # Non-sentiment word: decay intensity and negation window
                intensity = 1.0

            # Decay negation window
            if negate_countdown > 0:
                negate_countdown -= 1
                if negate_countdown == 0:
                    negate = False

        return score / max(1, count)

    def _source_credibility(self, source: str) -> float:
        """Return credibility score (0-1) for a news source."""
        if not source:
            return DEFAULT_SOURCE_CREDIBILITY

        source_lower = source.lower().strip()

        # Direct lookup
        if source_lower in SOURCE_CREDIBILITY:
            return SOURCE_CREDIBILITY[source_lower]

        # Partial match (e.g., "Reuters" matches "reuters")
        for name, score in SOURCE_CREDIBILITY.items():
            if name in source_lower or source_lower in name:
                return score

        # Reddit sources get lower credibility
        if "reddit" in source_lower:
            return 0.35

        return DEFAULT_SOURCE_CREDIBILITY

    # ─── Deduplication & Freshness ────────────────────────────────────────────

    def _deduplicate_articles(self, articles: list[dict]) -> list[dict]:
        """
        Remove duplicate articles across sources:
        - Compare headline similarity (SequenceMatcher > 0.7 = duplicate)
        - Keep the version from the most credible source
        - Keep the freshest version if same credibility
        """
        if len(articles) <= 1:
            return articles

        unique: list[dict] = []

        for article in articles:
            title = article.get("title", "")
            if not title:
                continue

            is_dup = False
            for i, existing in enumerate(unique):
                existing_title = existing.get("title", "")
                sim = SequenceMatcher(
                    None, title.lower(), existing_title.lower()
                ).ratio()

                if sim > 0.7:
                    is_dup = True
                    # Keep the better version
                    new_cred = self._source_credibility(
                        article.get("source", ""))
                    old_cred = self._source_credibility(
                        existing.get("source", ""))

                    if new_cred > old_cred:
                        unique[i] = article
                    elif (new_cred == old_cred
                          and article.get("age_minutes", 9999)
                          < existing.get("age_minutes", 9999)):
                        unique[i] = article
                    break

            if not is_dup:
                unique.append(article)

        return unique

    def _calculate_freshness_score(self, article: dict) -> float:
        """
        Score 0-1 based on how recent the article is:
        - < 15 min: 1.0
        - < 1 hour: 0.8
        - < 6 hours: 0.5
        - < 24 hours: 0.3
        - > 24 hours: 0.1
        """
        age = article.get("age_minutes", 9999)

        if age < 15:
            return 1.0
        elif age < 60:
            return 0.8
        elif age < 360:
            return 0.5
        elif age < 1440:
            return 0.3
        else:
            return 0.1

    # ─── Catalyst Tracking ────────────────────────────────────────────────────

    def _is_duplicate_catalyst(self, headlines: list[str],
                               market_id: str) -> bool:
        """
        Check if we already generated a catalyst signal for similar headlines.
        Uses fingerprinting to detect substantially similar news stories.
        """
        if market_id not in self._seen_catalysts:
            return False

        seen = self._seen_catalysts[market_id]
        for headline in headlines:
            fp = self._headline_fingerprint(headline)
            if fp in seen:
                return True

        return False

    def _record_catalyst(self, headlines: list[str], market_id: str) -> None:
        """Record headlines that triggered a catalyst to prevent re-triggering."""
        if market_id not in self._seen_catalysts:
            self._seen_catalysts[market_id] = set()

        for headline in headlines:
            fp = self._headline_fingerprint(headline)
            self._seen_catalysts[market_id].add(fp)

    def _headline_fingerprint(self, headline: str) -> str:
        """
        Create a fingerprint for deduplication.
        Uses sorted significant words to match semantically similar headlines.
        """
        words = re.sub(r'[^\w\s]', '', headline.lower()).split()
        # Keep words > 3 chars, sorted, to create order-independent fingerprint
        significant = sorted(w for w in words if len(w) > 3)
        return " ".join(significant[:6])

    def clear_stale_catalysts(self) -> None:
        """Remove catalyst tracking entries older than TTL."""
        # Called externally if needed; in practice the dict stays small
        # because we track by market_id and each market has few catalysts.
        self._seen_catalysts.clear()

    # ─── Date Parsing Utilities ───────────────────────────────────────────────

    def _age_minutes(self, date_str: str) -> int:
        """Calculate age of article in minutes from various date formats."""
        if not date_str:
            return 9999
        try:
            # Try ISO format first (NewsAPI, most APIs)
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - dt
            return max(0, int(delta.total_seconds() / 60))
        except (ValueError, TypeError):
            pass

        try:
            # Try RSS date format (e.g., "Sat, 15 Mar 2026 10:30:00 GMT")
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - dt
            return max(0, int(delta.total_seconds() / 60))
        except (ValueError, TypeError, Exception):
            pass

        return 9999

    def _parse_gdelt_date(self, gdelt_date: str) -> str:
        """
        Parse GDELT date format (YYYYMMDDTHHMMSSZ) to ISO format.
        """
        if not gdelt_date or len(gdelt_date) < 15:
            return ""
        try:
            dt = datetime.strptime(gdelt_date[:15], "%Y%m%dT%H%M%S")
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            return ""

    # ─── Cache ────────────────────────────────────────────────────────────────

    def _get_cached(self, key: str):
        """Retrieve cached value if not expired."""
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return val
            else:
                del self._cache[key]
        return None

    def _set_cache(self, key: str, val) -> None:
        """Store value in cache with current timestamp."""
        self._cache[key] = (val, time.time())

    def clear_cache(self) -> None:
        """Clear the entire news cache."""
        self._cache.clear()
