"""
Cross-Platform Aggregator
=========================
Aggregates probability estimates from Metaculus, Manifold, and other platforms.
"""
from __future__ import annotations
import logging
import time
from difflib import SequenceMatcher
from typing import Optional

import requests

from .models import PolyMarket, CrossPlatformPrice

logger = logging.getLogger(__name__)


class CrossPlatformAggregator:
    """Aggregates prediction market prices across platforms."""

    MATCH_THRESHOLD = 0.55  # Minimum similarity to consider same market

    def __init__(self):
        self._session = requests.Session()
        self._cache: dict[str, tuple] = {}
        self._cache_ttl = 600  # 10 min

    def get_consensus(self, market: PolyMarket) -> Optional[CrossPlatformPrice]:
        """
        Find matching market on other platforms and return consensus price.
        """
        cache_key = f"consensus:{market.condition_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        keywords = market.question.lower()[:60]
        matches: list[CrossPlatformPrice] = []

        # Try each platform
        for fetch_fn in (self._fetch_manifold, self._fetch_metaculus):
            try:
                external = fetch_fn(keywords)
                match = self._match_market(market, external)
                if match:
                    matches.append(match)
            except Exception as e:
                logger.debug("Cross-platform fetch error: %s", e)

        if not matches:
            self._set_cache(cache_key, None)
            return None

        # Return best match (highest volume)
        best = max(matches, key=lambda m: m.volume)
        self._set_cache(cache_key, best)
        return best

    def find_arbitrage(self, markets: list[PolyMarket],
                       min_diff: float = 0.05) -> list[dict]:
        """
        Find markets where Polymarket price diverges from cross-platform consensus.
        """
        opportunities = []
        for mkt in markets:
            consensus = self.get_consensus(mkt)
            if not consensus:
                continue

            diff = abs(mkt.yes_price - consensus.probability)
            if diff >= min_diff:
                direction = "YES" if consensus.probability > mkt.yes_price else "NO"
                opportunities.append({
                    "market": mkt,
                    "consensus": consensus,
                    "diff": diff,
                    "direction": direction,
                    "poly_price": mkt.yes_price,
                    "external_price": consensus.probability,
                })

        opportunities.sort(key=lambda x: x["diff"], reverse=True)
        return opportunities

    # ─── Platform Fetchers ─────────────────────────────────────────────────

    def _fetch_manifold(self, query: str) -> list[CrossPlatformPrice]:
        """Fetch from Manifold Markets API."""
        try:
            resp = self._session.get(
                "https://api.manifold.markets/v0/search-markets",
                params={"term": query, "limit": 5},
                timeout=8,
            )
            if not resp.ok:
                return []

            results = []
            for m in resp.json():
                if m.get("isResolved"):
                    continue
                prob = m.get("probability")
                if prob is None:
                    continue
                results.append(CrossPlatformPrice(
                    platform="manifold",
                    question=m.get("question", ""),
                    probability=float(prob),
                    volume=float(m.get("volume", 0)),
                    url=f"https://manifold.markets/{m.get('creatorUsername', '')}/{m.get('slug', '')}",
                    last_updated=m.get("lastUpdatedTime", ""),
                ))
            return results
        except Exception as e:
            logger.debug("Manifold API error: %s", e)
            return []

    def _fetch_metaculus(self, query: str) -> list[CrossPlatformPrice]:
        """Fetch from Metaculus API."""
        try:
            resp = self._session.get(
                "https://www.metaculus.com/api2/questions/",
                params={"search": query, "limit": 5, "status": "open"},
                timeout=8,
            )
            if not resp.ok:
                return []

            data = resp.json()
            questions = data.get("results", []) if isinstance(data, dict) else data
            results = []
            for q in questions:
                cp = q.get("community_prediction") or {}
                full = cp.get("full") or {}
                prob = full.get("q2")  # median prediction
                if prob is None:
                    continue
                results.append(CrossPlatformPrice(
                    platform="metaculus",
                    question=q.get("title", ""),
                    probability=float(prob),
                    volume=float(q.get("number_of_predictions", 0)),
                    url=f"https://www.metaculus.com/questions/{q.get('id', '')}/",
                    last_updated=q.get("last_activity_time", ""),
                ))
            return results
        except Exception as e:
            logger.debug("Metaculus API error: %s", e)
            return []

    def _match_market(self, poly_market: PolyMarket,
                      external: list[CrossPlatformPrice]) -> Optional[CrossPlatformPrice]:
        """Find the best matching market using fuzzy string matching."""
        if not external:
            return None

        best_match = None
        best_score = 0.0
        poly_q = poly_market.question.lower()

        for ext in external:
            score = SequenceMatcher(None, poly_q, ext.question.lower()).ratio()
            if score > best_score and score >= self.MATCH_THRESHOLD:
                best_score = score
                best_match = ext

        return best_match

    # ─── Cache ─────────────────────────────────────────────────────────────

    def _get_cached(self, key: str):
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return val
        return None

    def _set_cache(self, key: str, val):
        self._cache[key] = (val, time.time())
