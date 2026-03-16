"""
Probability Engine
==================
Production-grade LLM-based probability estimation for prediction markets.

Features:
- Multi-model consensus (Claude + OpenAI) with confidence-weighted averaging
- Per-model accuracy tracking with Brier scores
- Domain-specific superforecasting prompts
- Robust multi-strategy response parsing
- Calibration tracking with persistent storage
- Smart batching by topic with priority scoring
- Cache invalidation on market-moving events
- Exponential backoff retry logic
- Heuristic fallback when LLMs are unavailable
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .models import PolyMarket, ProbabilityEstimate

logger = logging.getLogger(__name__)

# ─── Model Tracking ──────────────────────────────────────────────────────────


@dataclass
class ModelTrack:
    """Tracks per-model accuracy and calibration over time."""

    model_name: str
    total_estimates: int = 0
    correct_predictions: int = 0  # Within 10% of resolution
    avg_confidence: float = 0.5
    avg_edge_captured: float = 0.0
    brier_score: float = 0.5  # Lower = better calibration
    cumulative_brier_sum: float = 0.0
    brier_count: int = 0

    @property
    def accuracy_rate(self) -> float:
        if self.total_estimates == 0:
            return 0.5
        return self.correct_predictions / self.total_estimates

    @property
    def weight(self) -> float:
        """Compute trust weight from accuracy and calibration. Higher = more trusted."""
        if self.total_estimates < 5:
            return 1.0  # Default weight until enough data
        # Combine accuracy rate (higher=better) and brier score (lower=better)
        accuracy_component = self.accuracy_rate
        calibration_component = max(0.0, 1.0 - self.brier_score)
        return 0.6 * accuracy_component + 0.4 * calibration_component

    def record_estimate(self, confidence: float) -> None:
        self.total_estimates += 1
        # Running average of confidence
        n = self.total_estimates
        self.avg_confidence = self.avg_confidence * ((n - 1) / n) + confidence / n

    def record_resolution(self, predicted_prob: float, actual_outcome: bool) -> None:
        """Update tracking after a market resolves."""
        outcome_val = 1.0 if actual_outcome else 0.0
        brier = (predicted_prob - outcome_val) ** 2
        self.cumulative_brier_sum += brier
        self.brier_count += 1
        self.brier_score = self.cumulative_brier_sum / self.brier_count

        # "Correct" = within 10% of outcome
        if (actual_outcome and predicted_prob >= 0.5) or (
            not actual_outcome and predicted_prob < 0.5
        ):
            error = abs(predicted_prob - outcome_val)
            if error <= 0.10:
                self.correct_predictions += 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ModelTrack:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ─── Calibration Tracker ─────────────────────────────────────────────────────


class CalibrationTracker:
    """Tracks prediction accuracy to improve calibration over time.

    Persists predictions and resolutions to disk so calibration data
    survives restarts.
    """

    CALIBRATION_FILE = "poly_calibration.json"

    def __init__(self, data_dir: str = "."):
        self._data_dir = data_dir
        self.predictions: list[dict] = []
        self.model_tracks: dict[str, ModelTrack] = {}
        self.load()

    def record_prediction(
        self,
        market_id: str,
        predicted_prob: float,
        market_price: float,
        model_name: str,
        confidence: float,
    ) -> None:
        """Record a new prediction for later calibration analysis."""
        self.predictions.append(
            {
                "market_id": market_id,
                "predicted_prob": predicted_prob,
                "market_price": market_price,
                "model_name": model_name,
                "confidence": confidence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "resolved": False,
                "actual_outcome": None,
            }
        )
        # Update model track
        track = self.model_tracks.setdefault(model_name, ModelTrack(model_name=model_name))
        track.record_estimate(confidence)
        self.save()

    def record_resolution(self, market_id: str, outcome: bool) -> None:
        """Record the resolution of a market for calibration scoring."""
        for pred in self.predictions:
            if pred["market_id"] == market_id and not pred["resolved"]:
                pred["resolved"] = True
                pred["actual_outcome"] = outcome
                # Update model track
                model_name = pred.get("model_name", "unknown")
                track = self.model_tracks.setdefault(
                    model_name, ModelTrack(model_name=model_name)
                )
                track.record_resolution(pred["predicted_prob"], outcome)
        self.save()

    def get_brier_score(self) -> float:
        """Compute overall Brier score across all resolved predictions."""
        resolved = [p for p in self.predictions if p["resolved"]]
        if not resolved:
            return 0.5  # No data, return baseline
        total = 0.0
        for p in resolved:
            outcome = 1.0 if p["actual_outcome"] else 0.0
            total += (p["predicted_prob"] - outcome) ** 2
        return total / len(resolved)

    def get_calibration_curve(self, bins: int = 10) -> list[dict]:
        """Compute calibration curve: for each probability bin, what fraction resolved YES?"""
        resolved = [p for p in self.predictions if p["resolved"]]
        if not resolved:
            return []

        bin_width = 1.0 / bins
        curve = []
        for i in range(bins):
            lo = i * bin_width
            hi = lo + bin_width
            in_bin = [p for p in resolved if lo <= p["predicted_prob"] < hi]
            if in_bin:
                actual_rate = sum(1 for p in in_bin if p["actual_outcome"]) / len(in_bin)
                curve.append(
                    {
                        "bin_low": round(lo, 2),
                        "bin_high": round(hi, 2),
                        "bin_midpoint": round((lo + hi) / 2, 2),
                        "predicted_avg": round(
                            sum(p["predicted_prob"] for p in in_bin) / len(in_bin), 3
                        ),
                        "actual_rate": round(actual_rate, 3),
                        "count": len(in_bin),
                    }
                )
        return curve

    def get_adjustment_factor(self) -> float:
        """Return a multiplicative adjustment based on historical overconfidence.

        >1.0 means we tend to underestimate (shift toward extremes),
        <1.0 means we tend to overestimate (shift toward 50%).
        Returns 1.0 if not enough data.
        """
        curve = self.get_calibration_curve(bins=5)
        if len(curve) < 3:
            return 1.0

        # Measure average calibration error direction
        total_error = 0.0
        total_weight = 0
        for bucket in curve:
            error = bucket["actual_rate"] - bucket["predicted_avg"]
            weight = bucket["count"]
            total_error += error * weight
            total_weight += weight

        if total_weight == 0:
            return 1.0

        avg_error = total_error / total_weight
        # Clamp to reasonable range [0.85, 1.15]
        return max(0.85, min(1.15, 1.0 + avg_error))

    def get_model_weight(self, model_name: str) -> float:
        """Get the trust weight for a specific model."""
        track = self.model_tracks.get(model_name)
        if not track:
            return 1.0
        return track.weight

    def save(self) -> None:
        path = Path(self._data_dir) / self.CALIBRATION_FILE
        try:
            data = {
                "predictions": self.predictions[-500:],  # Keep last 500
                "model_tracks": {k: v.to_dict() for k, v in self.model_tracks.items()},
            }
            path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug("Failed to save calibration data: %s", e)

    def load(self) -> None:
        path = Path(self._data_dir) / self.CALIBRATION_FILE
        try:
            if path.exists():
                data = json.loads(path.read_text())
                self.predictions = data.get("predictions", [])
                tracks_raw = data.get("model_tracks", {})
                self.model_tracks = {
                    k: ModelTrack.from_dict(v) for k, v in tracks_raw.items()
                }
                logger.debug(
                    "Loaded calibration data: %d predictions, %d models",
                    len(self.predictions),
                    len(self.model_tracks),
                )
        except Exception as e:
            logger.debug("Failed to load calibration data: %s", e)
            self.predictions = []
            self.model_tracks = {}


# ─── Prompt Engineering ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a superforecaster with expertise in prediction markets, \
geopolitics, economics, technology, sports, and current events. You have been \
trained on the methodology of Philip Tetlock's superforecasting research.

Your approach:
1. REFERENCE CLASS FORECASTING: What is the base rate for events like this?
2. INSIDE VIEW: What specific factors affect this particular event?
3. UPDATE FROM EVIDENCE: How do recent developments shift the probability?
4. CONSIDER CONTRARIAN VIEW: What would make the opposite outcome happen?
5. FINAL CALIBRATION: Ensure your probability reflects genuine uncertainty.

Critical rules:
- Never give exactly 50% - that means you haven't thought enough.
- Extreme probabilities (>95% or <5%) require extraordinary evidence.
- Be more uncertain than you think you should be (calibration research shows overconfidence).
- Consider the time horizon - longer time = more uncertainty.
- Market prices embed information from thousands of traders - respect that signal.
- Your job is NOT to agree with the market. Find where the market is WRONG."""

DOMAIN_PROMPTS: dict[str, str] = {
    "politics": (
        "Consider: polling data quality (likely-voter vs registered), historical election "
        "patterns, incumbency advantage/disadvantage, approval ratings, economic conditions "
        "(jobs, inflation), gerrymandering effects, early voting trends, legal challenges, "
        "and the difference between national and state-level dynamics."
    ),
    "elections": (
        "Consider: polling averages and methodology, prediction market history of over/under-"
        "pricing frontrunners, primary vs general election dynamics, voter turnout models, "
        "October surprise base rates, and the typical polling error margin (~3-4%)."
    ),
    "crypto": (
        "Consider: market cycle position (accumulation/markup/distribution), BTC dominance trends, "
        "regulatory actions (SEC, CFTC), exchange flows (on-chain data), halving cycle effects, "
        "DeFi TVL trends, institutional adoption signals, and macro correlation with risk assets."
    ),
    "sports": (
        "Consider: team/player ELO ratings, injury reports and lineup changes, home/away splits, "
        "rest days and travel schedule, head-to-head records, recent form (last 10 games), "
        "weather conditions for outdoor sports, and referee/umpire tendencies."
    ),
    "science": (
        "Consider: scientific consensus and peer review status, replication crisis rates by field, "
        "funding and institutional incentives, publication bias, base rates for breakthrough claims, "
        "regulatory approval timelines (FDA phases), and expert survey data (Metaculus, etc.)."
    ),
    "economics": (
        "Consider: leading economic indicators (PMI, yield curve, jobless claims), central bank "
        "forward guidance and dot plots, historical base rates for recessions/expansions, "
        "consensus economist forecasts, geopolitical risk premia, and supply chain data."
    ),
    "technology": (
        "Consider: technology adoption S-curves, regulatory landscape (antitrust, AI policy), "
        "patent filings and R&D spend trends, competitive moat analysis, management track record, "
        "and the typical gap between announcement and delivery in tech."
    ),
    "geopolitics": (
        "Consider: historical base rates for escalation/de-escalation, alliance structures, "
        "economic interdependence, domestic political pressures on leaders, intelligence community "
        "assessments, and the track record of prediction markets on geopolitical events."
    ),
    "entertainment": (
        "Consider: historical award patterns, critical consensus scores, box office performance, "
        "studio campaigning budgets, genre bias in awards, and prediction market track records "
        "for entertainment events."
    ),
    "weather": (
        "Consider: ensemble model consensus (GFS, ECMWF, NAM), climatological base rates, "
        "current atmospheric patterns (ENSO, NAO), model uncertainty at various time horizons, "
        "and the typical skill decay of weather forecasts beyond 7 days."
    ),
}

# Map common Polymarket tags to domain keys
TAG_TO_DOMAIN: dict[str, str] = {
    "politics": "politics",
    "us-politics": "politics",
    "elections": "elections",
    "election": "elections",
    "us-elections": "elections",
    "presidential": "elections",
    "crypto": "crypto",
    "cryptocurrency": "crypto",
    "bitcoin": "crypto",
    "ethereum": "crypto",
    "defi": "crypto",
    "sports": "sports",
    "nfl": "sports",
    "nba": "sports",
    "mlb": "sports",
    "soccer": "sports",
    "football": "sports",
    "science": "science",
    "health": "science",
    "covid": "science",
    "ai": "technology",
    "tech": "technology",
    "technology": "technology",
    "geopolitics": "geopolitics",
    "war": "geopolitics",
    "conflict": "geopolitics",
    "international": "geopolitics",
    "economics": "economics",
    "economy": "economics",
    "finance": "economics",
    "fed": "economics",
    "inflation": "economics",
    "entertainment": "entertainment",
    "oscars": "entertainment",
    "movies": "entertainment",
    "tv": "entertainment",
    "weather": "weather",
    "climate": "weather",
}


def _detect_domain(market: PolyMarket) -> Optional[str]:
    """Detect the domain of a market from its tags and question text."""
    # Check tags first
    for tag in market.tags:
        domain = TAG_TO_DOMAIN.get(tag.lower())
        if domain:
            return domain

    # Fallback: keyword scan on question
    q_lower = market.question.lower()
    keyword_hints = {
        "politics": ["president", "congress", "senate", "governor", "democrat", "republican", "vote"],
        "elections": ["election", "primary", "nominee", "ballot", "poll"],
        "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "token", "blockchain"],
        "sports": ["win", "championship", "playoff", "mvp", "super bowl", "world cup", "game"],
        "economics": ["gdp", "inflation", "interest rate", "fed", "recession", "unemployment"],
        "geopolitics": ["war", "invasion", "nato", "sanction", "treaty", "ceasefire"],
        "science": ["fda", "vaccine", "clinical trial", "study", "research"],
        "technology": ["launch", "release", "iphone", "ai model", "openai", "google"],
    }
    for domain, keywords in keyword_hints.items():
        if any(kw in q_lower for kw in keywords):
            return domain

    return None


# ─── Response Parsing ────────────────────────────────────────────────────────

# Ordered from most to least specific
_PROB_PATTERNS = [
    # JSON-like: "probability": 0.73 or "probability": 73
    re.compile(r'"probability"\s*:\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
    # Natural language: "I estimate 73%" or "probability of 73%"
    re.compile(r'(?:estimate|probability|likelihood|chance)[:\s]+(\d+(?:\.\d+)?)\s*%', re.IGNORECASE),
    # "73% likely/chance/probability"
    re.compile(r'(\d+(?:\.\d+)?)\s*%\s*(?:likely|chance|probability|likelihood)', re.IGNORECASE),
    # Bare percentage at start of line or after colon
    re.compile(r'(?:^|:\s*)(\d+(?:\.\d+)?)\s*%', re.MULTILINE),
    # Decimal form: "probability is 0.73"
    re.compile(r'(?:probability|estimate|likelihood)\s+(?:is|of|=|:)\s*(0\.\d+)', re.IGNORECASE),
]

_CONF_PATTERNS = [
    re.compile(r'"confidence"\s*:\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'confidence[:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
]


def _parse_llm_response(raw: str) -> tuple[float, float, str]:
    """Parse LLM response with multiple fallback strategies.

    Returns (probability, confidence, reasoning). Probability is -1.0 on failure.
    """
    text = raw.strip()

    # ── Strategy 1: JSON extraction ──
    prob, conf, reasoning = _try_json_parse(text)
    if prob >= 0:
        return prob, conf, reasoning

    # ── Strategy 2: Regex pattern matching ──
    prob = _try_regex_prob(text)
    conf = _try_regex_conf(text)
    if prob >= 0:
        reasoning = _extract_reasoning_text(text)
        return prob, max(0.1, conf), reasoning

    # ── Strategy 3: Look for any number that looks like a probability ──
    # Last resort: find standalone decimals between 0 and 1
    decimal_match = re.search(r'\b(0\.\d{1,4})\b', text)
    if decimal_match:
        prob = float(decimal_match.group(1))
        if 0.01 <= prob <= 0.99:
            return prob, 0.3, _extract_reasoning_text(text)

    logger.debug("All parse strategies failed for response: %.120s", text)
    return -1.0, 0.0, ""


def _try_json_parse(text: str) -> tuple[float, float, str]:
    """Try to extract and parse JSON from the response."""
    candidates = []

    # Try the raw text
    candidates.append(text)

    # Try extracting from markdown code fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                candidates.append(part)

    # Try extracting any JSON object with regex
    json_match = re.search(r'\{[^{}]*"probability"[^{}]*\}', text, re.DOTALL)
    if json_match:
        candidates.append(json_match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            prob = float(data.get("probability", -1))
            # Handle percentage values (73 -> 0.73)
            if prob > 1.0:
                prob = prob / 100.0
            conf = float(data.get("confidence", 0.5))
            if conf > 1.0:
                conf = conf / 100.0
            reason = str(data.get("reasoning", data.get("rationale", data.get("explanation", ""))))

            if 0.0 <= prob <= 1.0:
                conf = max(0.0, min(1.0, conf))
                return prob, conf, reason
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    return -1.0, 0.0, ""


def _try_regex_prob(text: str) -> float:
    """Try to extract probability from text using regex patterns."""
    for pattern in _PROB_PATTERNS:
        match = pattern.search(text)
        if match:
            val = float(match.group(1))
            if val > 1.0:
                val = val / 100.0
            if 0.0 <= val <= 1.0:
                return val
    return -1.0


def _try_regex_conf(text: str) -> float:
    """Try to extract confidence from text using regex patterns."""
    for pattern in _CONF_PATTERNS:
        match = pattern.search(text)
        if match:
            val = float(match.group(1))
            if val > 1.0:
                val = val / 100.0
            return max(0.0, min(1.0, val))
    return 0.4  # Default confidence when not explicitly stated


def _extract_reasoning_text(text: str) -> str:
    """Extract a reasoning snippet from unstructured text."""
    # Try to find a reasoning section
    for marker in ["reasoning:", "rationale:", "explanation:", "because ", "analysis:"]:
        idx = text.lower().find(marker)
        if idx >= 0:
            snippet = text[idx + len(marker) :].strip()
            # Take first 300 chars or until a double newline
            end = snippet.find("\n\n")
            if end > 0:
                snippet = snippet[:end]
            return snippet[:300].strip()
    # Fallback: first 200 chars
    return text[:200].strip()


# ─── Main Engine ─────────────────────────────────────────────────────────────


class ProbabilityEngine:
    """Production-grade probability estimation engine for prediction markets.

    Features:
    - Multi-model consensus with confidence-weighted averaging
    - Per-model accuracy tracking (Brier scores, accuracy rates)
    - Domain-specific superforecasting prompts
    - Smart cache with event-driven invalidation
    - Intelligent batching by topic
    - Heuristic fallback when LLMs are unavailable
    - Exponential backoff retry logic
    - Calibration tracking with persistent storage
    """

    # Retry configuration
    MAX_RETRIES = 2
    BASE_RETRY_DELAY = 1.0  # seconds
    CLAUDE_TIMEOUT = 20  # seconds
    OPENAI_TIMEOUT = 25  # seconds

    # Consensus thresholds
    DISAGREEMENT_THRESHOLD = 0.15  # Flag low-confidence if models disagree by this much
    CACHE_PRICE_MOVE_THRESHOLD = 0.05  # Invalidate cache if market moved >5%

    def __init__(
        self,
        news_client=None,
        cross_platform=None,
        data_dir: str = ".",
    ):
        self._anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._openai_key = os.getenv("OPENAI_API_KEY", "")
        self._news = news_client
        self._cross_platform = cross_platform
        self._session = requests.Session()

        # Cache: condition_id -> (estimate, timestamp, market_price_at_cache_time)
        self._cache: dict[str, tuple[ProbabilityEstimate, float, float]] = {}
        self._cache_ttl = 600  # 10 minutes default

        # Rate limiting
        self._calls_this_cycle = 0
        self._max_calls_per_cycle = 20

        # API latency tracking
        self._api_stats: dict[str, dict] = {
            "claude": {"calls": 0, "errors": 0, "total_latency": 0.0},
            "openai": {"calls": 0, "errors": 0, "total_latency": 0.0},
        }

        # Calibration
        self._calibration = CalibrationTracker(data_dir=data_dir)

    # ─── Public API ──────────────────────────────────────────────────────────

    def reset_cycle_counter(self) -> None:
        """Reset LLM call counter at the start of each scan cycle."""
        self._calls_this_cycle = 0

    def estimate_probability(self, market: PolyMarket) -> Optional[ProbabilityEstimate]:
        """Estimate true probability for a market using multi-model consensus.

        If both Claude and OpenAI are available, queries both and produces a
        confidence-weighted average.  If models disagree by >15%, the result
        is flagged as low-confidence.  Per-model accuracy history is used for
        weighting when enough data is available.

        Falls back to heuristic estimation if all LLM calls fail.
        """
        # ── Cache check (with smart invalidation) ──
        cache_key = market.condition_id
        if cache_key in self._cache:
            cached_est, cached_ts, cached_price = self._cache[cache_key]
            if not self._should_invalidate_cache(market, cached_est, cached_ts, cached_price):
                return cached_est

        # ── Rate limit ──
        if self._calls_this_cycle >= self._max_calls_per_cycle:
            logger.debug("LLM rate limit reached (%d calls this cycle)", self._calls_this_cycle)
            return self._heuristic_estimate(market)

        # ── Gather context ──
        context = self._gather_context(market)
        prompt = self._build_prompt(market, context)

        # ── Multi-model consensus ──
        estimates: list[tuple[float, float, str, str]] = []  # (prob, conf, reasoning, model)

        if self._anthropic_key:
            raw = self._call_claude(prompt)
            if raw:
                prob, conf, reasoning = _parse_llm_response(raw)
                if prob >= 0:
                    estimates.append((prob, conf, reasoning, "claude-sonnet-4-20250514"))

        if self._openai_key:
            raw = self._call_openai(prompt)
            if raw:
                prob, conf, reasoning = _parse_llm_response(raw)
                if prob >= 0:
                    estimates.append((prob, conf, reasoning, "gpt-4o"))

        self._calls_this_cycle += 1

        # ── No LLM response: fall back to heuristic ──
        if not estimates:
            logger.info("All LLM calls failed for '%s', using heuristic", market.question[:60])
            return self._heuristic_estimate(market)

        # ── Single model: use directly ──
        if len(estimates) == 1:
            prob, conf, reasoning, model = estimates[0]
            estimate = self._build_estimate(market, prob, conf, reasoning, [model], context)
            self._record_and_cache(market, estimate, [model], [conf])
            return estimate

        # ── Multi-model consensus ──
        estimate = self._consensus_estimate(market, estimates, context)
        models = [e[3] for e in estimates]
        confs = [e[1] for e in estimates]
        self._record_and_cache(market, estimate, models, confs)
        return estimate

    def batch_estimate(self, markets: list[PolyMarket]) -> list[ProbabilityEstimate]:
        """Intelligently batch markets for LLM estimation.

        1. Groups markets by topic/domain.
        2. Prioritizes by: volume * spread * (1 / time_to_resolution).
        3. Skips recently cached markets.
        4. Sends groups of 3-5 related markets in single prompts.
        5. Caps total LLM calls per cycle.
        """
        if not markets:
            return []

        # Separate cached from uncached
        uncached: list[PolyMarket] = []
        results: list[ProbabilityEstimate] = []

        for mkt in markets:
            cached = self._get_cached(mkt)
            if cached:
                results.append(cached)
            else:
                uncached.append(mkt)

        if not uncached:
            return results

        # Prioritize uncached markets
        scored = []
        for mkt in uncached:
            score = self._priority_score(mkt)
            scored.append((score, mkt))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Group by domain
        domain_groups: dict[str, list[PolyMarket]] = defaultdict(list)
        for _score, mkt in scored:
            domain = _detect_domain(mkt) or "general"
            domain_groups[domain].append(mkt)

        # Process groups in batches
        for domain, group in domain_groups.items():
            if self._calls_this_cycle >= self._max_calls_per_cycle:
                break

            # Process in chunks of 4
            for i in range(0, len(group), 4):
                if self._calls_this_cycle >= self._max_calls_per_cycle:
                    break

                chunk = group[i : i + 4]
                if len(chunk) >= 2:
                    batch_results = self._estimate_batch_group(chunk, domain)
                    results.extend(batch_results)
                else:
                    # Single market: use normal path
                    est = self.estimate_probability(chunk[0])
                    if est:
                        results.append(est)

        return results

    def record_resolution(self, market_id: str, outcome: bool) -> None:
        """Record market resolution for calibration tracking."""
        self._calibration.record_resolution(market_id, outcome)

    def get_calibration_stats(self) -> dict:
        """Return calibration statistics for monitoring."""
        return {
            "brier_score": self._calibration.get_brier_score(),
            "adjustment_factor": self._calibration.get_adjustment_factor(),
            "calibration_curve": self._calibration.get_calibration_curve(),
            "total_predictions": len(self._calibration.predictions),
            "resolved_predictions": sum(
                1 for p in self._calibration.predictions if p["resolved"]
            ),
            "model_weights": {
                name: round(track.weight, 3)
                for name, track in self._calibration.model_tracks.items()
            },
            "api_stats": self._api_stats,
        }

    # ─── Multi-Model Consensus ───────────────────────────────────────────────

    def _consensus_estimate(
        self,
        market: PolyMarket,
        estimates: list[tuple[float, float, str, str]],
        context: dict,
    ) -> ProbabilityEstimate:
        """Produce a weighted consensus from multiple model estimates."""
        # Get calibration-based weights for each model
        weighted_probs = []
        total_weight = 0.0
        reasonings = []
        models_used = []
        max_conf = 0.0

        for prob, conf, reasoning, model in estimates:
            model_weight = self._calibration.get_model_weight(model)
            # Combined weight = model_trust * stated_confidence
            w = model_weight * conf
            weighted_probs.append((prob, w))
            total_weight += w
            reasonings.append(f"[{model}] {reasoning}")
            models_used.append(model)
            max_conf = max(max_conf, conf)

        # Weighted average probability
        if total_weight > 0:
            consensus_prob = sum(p * w for p, w in weighted_probs) / total_weight
        else:
            consensus_prob = sum(p for p, _ in weighted_probs) / len(weighted_probs)

        # Check for high disagreement
        probs = [e[0] for e in estimates]
        disagreement = max(probs) - min(probs)
        consensus_conf = max_conf

        if disagreement > self.DISAGREEMENT_THRESHOLD:
            # Flag as low confidence and note the disagreement
            consensus_conf = min(consensus_conf, 0.35)
            reasonings.append(
                f"[DISAGREEMENT] Models disagree by {disagreement:.0%} "
                f"({', '.join(f'{p:.0%}' for p in probs)}). Low confidence."
            )
            logger.info(
                "Model disagreement %.1f%% on '%s': %s",
                disagreement * 100,
                market.question[:50],
                [round(p, 3) for p in probs],
            )

        # Apply calibration adjustment
        adj = self._calibration.get_adjustment_factor()
        if adj != 1.0:
            # Adjust away from 50% (if adj > 1) or toward 50% (if adj < 1)
            consensus_prob = 0.5 + (consensus_prob - 0.5) * adj
            consensus_prob = max(0.01, min(0.99, consensus_prob))

        combined_reasoning = " | ".join(reasonings)

        return self._build_estimate(
            market, consensus_prob, consensus_conf, combined_reasoning, models_used, context
        )

    # ─── Prompt Construction ─────────────────────────────────────────────────

    def _build_prompt(self, market: PolyMarket, context: dict) -> str:
        """Build a sophisticated, domain-aware prompt for probability estimation."""
        # Domain-specific guidance
        domain = _detect_domain(market)
        domain_section = ""
        if domain and domain in DOMAIN_PROMPTS:
            domain_section = f"\n\nDomain-specific considerations ({domain}):\n{DOMAIN_PROMPTS[domain]}"

        # News section with source attribution and recency
        news_section = ""
        if context.get("news_detailed"):
            items = []
            for article in context["news_detailed"][:7]:
                title = article.get("title", "")
                source = article.get("source", "")
                age = article.get("age_minutes", 9999)
                if age < 60:
                    recency = f"{age}m ago"
                elif age < 1440:
                    recency = f"{age // 60}h ago"
                else:
                    recency = f"{age // 1440}d ago"
                items.append(f"  - [{source}, {recency}] {title}")
            if items:
                news_section = "\n\nRecent related news:\n" + "\n".join(items)
        elif context.get("news"):
            headlines = "\n".join(f"  - {h}" for h in context["news"][:5])
            news_section = f"\n\nRecent related news:\n{headlines}"

        # Sentiment signal
        sentiment_section = ""
        sentiment = context.get("news_sentiment")
        if sentiment is not None and abs(sentiment) >= 0.1:
            direction = "positive/bullish" if sentiment > 0 else "negative/bearish"
            sentiment_section = f"\nNews sentiment: {direction} ({sentiment:+.2f} on -1 to +1 scale)"

        # Cross-platform section with volume context
        cross_section = ""
        if context.get("cross_platform_prob") is not None:
            cp_prob = context["cross_platform_prob"]
            cp_source = context.get("cross_platform_source", "unknown")
            cp_volume = context.get("cross_platform_volume", 0)
            vol_str = f", volume: {cp_volume:,.0f}" if cp_volume else ""
            diff = cp_prob - market.yes_price
            diff_str = f" ({diff:+.1%} vs Polymarket)" if abs(diff) >= 0.02 else ""
            cross_section = (
                f"\n\nCross-platform estimate: {cp_prob:.1%} "
                f"(from {cp_source}{vol_str}){diff_str}"
            )

        # Market metadata
        time_to_end = self._time_to_resolution_str(market.end_date)
        spread_info = f", spread: {market.spread:.1%}" if market.spread > 0 else ""
        volume_info = (
            f"24h volume: ${market.volume_24h:,.0f}, total volume: ${market.volume_total:,.0f}"
        )
        liquidity_info = ""
        if market.open_interest > 0:
            liquidity_info = f", open interest: ${market.open_interest:,.0f}"

        # Calibration hint
        calibration_hint = ""
        adj = self._calibration.get_adjustment_factor()
        if adj < 0.95:
            calibration_hint = (
                "\nNote: Historical data shows overconfidence in extreme probabilities. "
                "Consider shifting slightly toward 50%."
            )
        elif adj > 1.05:
            calibration_hint = (
                "\nNote: Historical data shows underconfidence. "
                "If you have strong evidence, you can be somewhat more decisive."
            )

        return f"""{SYSTEM_PROMPT}
{domain_section}

─── MARKET DETAILS ───
Question: {market.question}
{f"Description: {market.description}" if market.description else ""}
Current market price (YES): {market.yes_price:.2%}
Time to resolution: {time_to_end}
End date: {market.end_date}
{volume_info}{liquidity_info}{spread_info}
Tags: {", ".join(market.tags) if market.tags else "none"}
{f"Resolution source: {market.resolution_source}" if market.resolution_source else ""}
{news_section}{sentiment_section}{cross_section}
{calibration_hint}

─── YOUR TASK ───
Estimate the TRUE probability of the YES outcome. The market currently prices it at \
{market.yes_price:.1%}. If you believe the true probability differs from the market price, \
explain why.

Respond ONLY with valid JSON in this exact format:
{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "Your concise analysis (2-4 sentences)"}}"""

    def _build_batch_prompt(
        self, markets: list[PolyMarket], contexts: list[dict], domain: str
    ) -> str:
        """Build a single prompt for multiple related markets."""
        domain_section = ""
        if domain in DOMAIN_PROMPTS:
            domain_section = f"\nDomain considerations ({domain}):\n{DOMAIN_PROMPTS[domain]}"

        market_blocks = []
        for i, (mkt, ctx) in enumerate(zip(markets, contexts), 1):
            news_str = ""
            if ctx.get("news"):
                news_str = " | News: " + "; ".join(ctx["news"][:3])

            cross_str = ""
            if ctx.get("cross_platform_prob") is not None:
                cross_str = f" | Cross-platform: {ctx['cross_platform_prob']:.1%}"

            block = (
                f"Market {i} (ID: {mkt.condition_id}):\n"
                f"  Question: {mkt.question}\n"
                f"  Current price (YES): {mkt.yes_price:.2%}\n"
                f"  End date: {mkt.end_date}\n"
                f"  24h volume: ${mkt.volume_24h:,.0f}"
                f"{news_str}{cross_str}"
            )
            market_blocks.append(block)

        markets_text = "\n\n".join(market_blocks)

        return f"""{SYSTEM_PROMPT}
{domain_section}

─── MULTIPLE MARKETS TO EVALUATE ───
{markets_text}

─── YOUR TASK ───
For EACH market above, estimate the TRUE probability of the YES outcome.

Respond ONLY with a valid JSON array. Each element must have:
{{"market_id": "condition_id_here", "probability": 0.XX, "confidence": 0.XX, "reasoning": "brief"}}

Example response format:
[{{"market_id": "abc123", "probability": 0.65, "confidence": 0.7, "reasoning": "..."}}]"""

    # ─── LLM API Calls ──────────────────────────────────────────────────────

    def _call_claude(self, prompt: str) -> Optional[str]:
        """Call Anthropic Claude API with retry and backoff."""
        stats = self._api_stats["claude"]

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                start = time.monotonic()
                resp = self._session.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self._anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 500,
                        "system": SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=self.CLAUDE_TIMEOUT,
                )
                latency = time.monotonic() - start
                stats["calls"] += 1
                stats["total_latency"] += latency

                if resp.ok:
                    data = resp.json()
                    content = data.get("content", [])
                    if content:
                        return content[0].get("text", "")

                # Rate limit: backoff and retry
                if resp.status_code == 429:
                    delay = self.BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.debug("Claude rate limited, retrying in %.1fs (attempt %d)", delay, attempt + 1)
                    time.sleep(delay)
                    continue

                # Server error: retry
                if resp.status_code >= 500:
                    delay = self.BASE_RETRY_DELAY * (2 ** attempt)
                    logger.debug("Claude server error %d, retrying in %.1fs", resp.status_code, delay)
                    time.sleep(delay)
                    continue

                # Client error (not rate limit): don't retry
                logger.debug("Claude API error %d: %s", resp.status_code, resp.text[:200])
                stats["errors"] += 1
                return None

            except requests.exceptions.Timeout:
                stats["errors"] += 1
                logger.debug("Claude API timeout (attempt %d/%d)", attempt + 1, self.MAX_RETRIES + 1)
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.BASE_RETRY_DELAY * (2 ** attempt))
                    continue
            except requests.exceptions.ConnectionError:
                stats["errors"] += 1
                logger.debug("Claude connection error (attempt %d/%d)", attempt + 1, self.MAX_RETRIES + 1)
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.BASE_RETRY_DELAY * (2 ** attempt))
                    continue
            except Exception as e:
                stats["errors"] += 1
                logger.debug("Claude API unexpected error: %s", e)
                return None

        stats["errors"] += 1
        return None

    def _call_openai(self, prompt: str) -> Optional[str]:
        """Call OpenAI API with retry and backoff."""
        stats = self._api_stats["openai"]

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                start = time.monotonic()
                resp = self._session.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._openai_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 500,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,  # Lower temp for more consistent probability estimates
                    },
                    timeout=self.OPENAI_TIMEOUT,
                )
                latency = time.monotonic() - start
                stats["calls"] += 1
                stats["total_latency"] += latency

                if resp.ok:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")

                if resp.status_code == 429:
                    delay = self.BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.debug("OpenAI rate limited, retrying in %.1fs (attempt %d)", delay, attempt + 1)
                    time.sleep(delay)
                    continue

                if resp.status_code >= 500:
                    delay = self.BASE_RETRY_DELAY * (2 ** attempt)
                    logger.debug("OpenAI server error %d, retrying in %.1fs", resp.status_code, delay)
                    time.sleep(delay)
                    continue

                logger.debug("OpenAI API error %d: %s", resp.status_code, resp.text[:200])
                stats["errors"] += 1
                return None

            except requests.exceptions.Timeout:
                stats["errors"] += 1
                logger.debug("OpenAI API timeout (attempt %d/%d)", attempt + 1, self.MAX_RETRIES + 1)
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.BASE_RETRY_DELAY * (2 ** attempt))
                    continue
            except requests.exceptions.ConnectionError:
                stats["errors"] += 1
                logger.debug("OpenAI connection error (attempt %d/%d)", attempt + 1, self.MAX_RETRIES + 1)
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.BASE_RETRY_DELAY * (2 ** attempt))
                    continue
            except Exception as e:
                stats["errors"] += 1
                logger.debug("OpenAI API unexpected error: %s", e)
                return None

        stats["errors"] += 1
        return None

    # ─── Context Gathering ───────────────────────────────────────────────────

    def _gather_context(self, market: PolyMarket) -> dict:
        """Gather rich context from all available sources.

        Collects:
        - News headlines with source attribution and recency
        - News sentiment score
        - Cross-platform prices with platform volume
        - Market metadata (volume, liquidity, time to resolution)
        - Catalyst detection for breaking news
        """
        context: dict = {
            "sources": [],
            "news": [],
            "news_detailed": [],
            "news_sentiment": None,
            "catalyst": None,
            "cross_platform_prob": None,
            "cross_platform_source": None,
            "cross_platform_volume": 0,
        }

        # ── News ──
        if self._news:
            try:
                articles = self._news.get_relevant_news(market, limit=7)
                context["news"] = [a.get("title", "") for a in articles]
                context["news_detailed"] = articles
                if articles:
                    context["sources"].append("news")

                    # Sentiment scoring
                    headlines = [a.get("title", "") for a in articles]
                    sentiment = self._news.score_sentiment(headlines, market.question)
                    context["news_sentiment"] = sentiment

                    # Catalyst detection
                    catalyst = self._news.detect_catalyst(market)
                    if catalyst:
                        context["catalyst"] = catalyst
                        context["sources"].append("catalyst")
            except Exception as e:
                logger.debug("News context error for '%s': %s", market.question[:40], e)

        # ── Cross-platform prices ──
        if self._cross_platform:
            try:
                consensus = self._cross_platform.get_consensus(market)
                if consensus:
                    context["cross_platform_prob"] = consensus.probability
                    context["cross_platform_source"] = consensus.platform
                    context["cross_platform_volume"] = consensus.volume
                    context["sources"].append(consensus.platform)
            except Exception as e:
                logger.debug("Cross-platform context error for '%s': %s", market.question[:40], e)

        return context

    # ─── Caching with Smart Invalidation ─────────────────────────────────────

    def _get_cached(self, market: PolyMarket) -> Optional[ProbabilityEstimate]:
        """Return cached estimate if still valid, None otherwise."""
        cache_key = market.condition_id
        if cache_key not in self._cache:
            return None
        est, ts, cached_price = self._cache[cache_key]
        if self._should_invalidate_cache(market, est, ts, cached_price):
            return None
        return est

    def _should_invalidate_cache(
        self,
        market: PolyMarket,
        cached: ProbabilityEstimate,
        cached_ts: float,
        cached_price: float,
    ) -> bool:
        """Determine if a cached estimate should be invalidated.

        Invalidation triggers:
        - TTL expired
        - Market price moved >5% since estimate
        - Breaking news catalyst detected for this market
        - Cross-platform consensus shifted significantly
        """
        now = time.time()

        # TTL expiry
        if now - cached_ts >= self._cache_ttl:
            return True

        # Market price movement
        price_move = abs(market.yes_price - cached_price)
        if price_move >= self.CACHE_PRICE_MOVE_THRESHOLD:
            logger.debug(
                "Cache invalidated for '%s': price moved %.1f%%",
                market.question[:40],
                price_move * 100,
            )
            return True

        # Breaking news catalyst (check only if news client available and cache > 2 min old)
        if self._news and (now - cached_ts > 120):
            try:
                catalyst = self._news.detect_catalyst(market)
                if catalyst and catalyst.get("age_minutes", 999) < 30:
                    logger.debug(
                        "Cache invalidated for '%s': breaking news catalyst",
                        market.question[:40],
                    )
                    return True
            except Exception:
                pass

        # Cross-platform shift
        if self._cross_platform and (now - cached_ts > 180):
            try:
                consensus = self._cross_platform.get_consensus(market)
                if consensus:
                    cp_shift = abs(consensus.probability - cached.estimated_prob)
                    if cp_shift >= 0.08:
                        logger.debug(
                            "Cache invalidated for '%s': cross-platform shifted %.1f%%",
                            market.question[:40],
                            cp_shift * 100,
                        )
                        return True
            except Exception:
                pass

        return False

    # ─── Heuristic Fallback ──────────────────────────────────────────────────

    def _heuristic_estimate(self, market: PolyMarket) -> ProbabilityEstimate:
        """Fallback estimation when LLM is unavailable.

        Uses:
        - Cross-platform consensus as primary signal
        - News sentiment as secondary signal
        - Market price as baseline with small regression toward 50%
        """
        base_prob = market.yes_price
        sources: list[str] = ["market_price"]
        adjustments: list[str] = []

        # Start with slight mean-reversion (markets overshoot at extremes)
        # Pull extreme prices 3% toward 50%
        mean_reversion = 0.03
        adjusted_prob = base_prob + (0.5 - base_prob) * mean_reversion
        if abs(adjusted_prob - base_prob) > 0.005:
            adjustments.append(f"mean-reversion {(adjusted_prob - base_prob):+.1%}")

        # Cross-platform signal
        if self._cross_platform:
            try:
                consensus = self._cross_platform.get_consensus(market)
                if consensus:
                    # Blend: 60% market price, 40% cross-platform
                    cp_weight = 0.4
                    # Weight by volume (higher volume = more trust)
                    if consensus.volume > 1000:
                        cp_weight = 0.45
                    if consensus.volume > 10000:
                        cp_weight = 0.5
                    adjusted_prob = (1.0 - cp_weight) * adjusted_prob + cp_weight * consensus.probability
                    sources.append(consensus.platform)
                    adjustments.append(f"cross-platform {consensus.platform}: {consensus.probability:.1%}")
            except Exception:
                pass

        # News sentiment signal
        if self._news:
            try:
                articles = self._news.get_relevant_news(market, limit=5)
                if articles:
                    headlines = [a.get("title", "") for a in articles]
                    sentiment = self._news.score_sentiment(headlines, market.question)
                    if abs(sentiment) >= 0.15:
                        # Shift up to 3% based on sentiment
                        sentiment_shift = sentiment * 0.03
                        adjusted_prob += sentiment_shift
                        sources.append("news_sentiment")
                        adjustments.append(f"sentiment {sentiment:+.2f} -> shift {sentiment_shift:+.1%}")
            except Exception:
                pass

        adjusted_prob = max(0.02, min(0.98, adjusted_prob))

        reasoning = "Heuristic estimate (LLM unavailable). " + "; ".join(adjustments) if adjustments else "Heuristic: market price baseline"

        return ProbabilityEstimate(
            market_question=market.question,
            estimated_prob=round(adjusted_prob, 4),
            confidence=0.25,  # Low confidence for heuristic
            reasoning=reasoning,
            sources_used=sources,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_used="heuristic",
        )

    # ─── Batch Processing ────────────────────────────────────────────────────

    def _estimate_batch_group(
        self, markets: list[PolyMarket], domain: str
    ) -> list[ProbabilityEstimate]:
        """Estimate multiple related markets in a single LLM call."""
        contexts = [self._gather_context(mkt) for mkt in markets]
        prompt = self._build_batch_prompt(markets, contexts, domain)

        raw = None
        model_used = ""

        if self._anthropic_key:
            raw = self._call_claude(prompt)
            model_used = "claude-sonnet-4-20250514"

        if not raw and self._openai_key:
            raw = self._call_openai(prompt)
            model_used = "gpt-4o"

        self._calls_this_cycle += 1

        if not raw:
            # Fallback: heuristic for each
            return [self._heuristic_estimate(mkt) for mkt in markets]

        # Parse batch response
        results = self._parse_batch_response(raw, markets, contexts, model_used)
        return results

    def _parse_batch_response(
        self,
        raw: str,
        markets: list[PolyMarket],
        contexts: list[dict],
        model_used: str,
    ) -> list[ProbabilityEstimate]:
        """Parse a batch LLM response containing multiple market estimates."""
        results: list[ProbabilityEstimate] = []

        # Try to parse as JSON array
        text = raw.strip()

        # Extract from code fences if present
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("["):
                    text = part
                    break

        # Try extracting JSON array with regex
        array_match = re.search(r'\[.*\]', text, re.DOTALL)
        if array_match:
            text = array_match.group(0)

        parsed_items: list[dict] = []
        try:
            parsed_items = json.loads(text)
            if not isinstance(parsed_items, list):
                parsed_items = [parsed_items]
        except json.JSONDecodeError:
            # Try to find individual JSON objects
            for obj_match in re.finditer(r'\{[^{}]*"probability"[^{}]*\}', raw, re.DOTALL):
                try:
                    parsed_items.append(json.loads(obj_match.group(0)))
                except json.JSONDecodeError:
                    continue

        # Map parsed items to markets
        id_to_market = {mkt.condition_id: (mkt, ctx) for mkt, ctx in zip(markets, contexts)}
        matched_ids: set[str] = set()

        for item in parsed_items:
            market_id = str(item.get("market_id", ""))
            prob = float(item.get("probability", -1))
            if prob > 1.0:
                prob /= 100.0
            conf = float(item.get("confidence", 0.5))
            if conf > 1.0:
                conf /= 100.0
            reasoning = str(item.get("reasoning", ""))

            if not (0.0 <= prob <= 1.0):
                continue

            if market_id in id_to_market:
                mkt, ctx = id_to_market[market_id]
                matched_ids.add(market_id)
                est = self._build_estimate(mkt, prob, conf, reasoning, [model_used], ctx)
                self._record_and_cache(mkt, est, [model_used], [conf])
                results.append(est)

        # For unmatched markets, try positional matching or fall back to heuristic
        unmatched_markets = [
            (mkt, ctx)
            for mkt, ctx in zip(markets, contexts)
            if mkt.condition_id not in matched_ids
        ]
        unmatched_items = [
            item for item in parsed_items
            if str(item.get("market_id", "")) not in id_to_market
        ]

        for i, (mkt, ctx) in enumerate(unmatched_markets):
            if i < len(unmatched_items):
                item = unmatched_items[i]
                prob = float(item.get("probability", -1))
                if prob > 1.0:
                    prob /= 100.0
                conf = float(item.get("confidence", 0.5))
                if conf > 1.0:
                    conf /= 100.0
                reasoning = str(item.get("reasoning", ""))
                if 0.0 <= prob <= 1.0:
                    est = self._build_estimate(mkt, prob, conf, reasoning, [model_used], ctx)
                    self._record_and_cache(mkt, est, [model_used], [conf])
                    results.append(est)
                    continue
            # Complete fallback
            results.append(self._heuristic_estimate(mkt))

        return results

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _build_estimate(
        self,
        market: PolyMarket,
        prob: float,
        confidence: float,
        reasoning: str,
        models: list[str],
        context: dict,
    ) -> ProbabilityEstimate:
        """Construct a ProbabilityEstimate with all metadata."""
        return ProbabilityEstimate(
            market_question=market.question,
            estimated_prob=round(max(0.01, min(0.99, prob)), 4),
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            reasoning=reasoning[:500],
            sources_used=context.get("sources", []) + models,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_used=", ".join(models),
        )

    def _record_and_cache(
        self,
        market: PolyMarket,
        estimate: ProbabilityEstimate,
        models: list[str],
        confidences: list[float],
    ) -> None:
        """Cache the estimate and record it for calibration."""
        self._cache[market.condition_id] = (estimate, time.time(), market.yes_price)

        for model, conf in zip(models, confidences):
            self._calibration.record_prediction(
                market_id=market.condition_id,
                predicted_prob=estimate.estimated_prob,
                market_price=market.yes_price,
                model_name=model,
                confidence=conf,
            )

    def _priority_score(self, market: PolyMarket) -> float:
        """Score a market for batch prioritization.

        Higher score = process first. Prioritizes:
        - High volume (more liquid, more important)
        - Wide spread (more opportunity)
        - Shorter time to resolution (more actionable)
        """
        volume_score = math.log1p(market.volume_24h) / 15.0  # Normalize ~0-1
        spread_score = min(market.spread * 10.0, 1.0)  # 10% spread -> 1.0

        # Time to resolution factor (shorter = higher priority)
        time_factor = 1.0
        try:
            end_dt = datetime.fromisoformat(market.end_date.replace("Z", "+00:00"))
            hours_left = max(1, (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600)
            # Inverse: 24h -> 1.0, 168h (1 week) -> 0.14, 720h (1 month) -> 0.03
            time_factor = min(1.0, 24.0 / hours_left)
        except (ValueError, TypeError):
            time_factor = 0.5

        return volume_score * 0.4 + spread_score * 0.3 + time_factor * 0.3

    def _time_to_resolution_str(self, end_date: str) -> str:
        """Human-readable time to resolution."""
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            delta = end_dt - datetime.now(timezone.utc)
            total_hours = delta.total_seconds() / 3600
            if total_hours < 0:
                return "EXPIRED"
            if total_hours < 1:
                return f"{int(delta.total_seconds() / 60)} minutes"
            if total_hours < 48:
                return f"{total_hours:.0f} hours"
            days = total_hours / 24
            if days < 60:
                return f"{days:.0f} days"
            months = days / 30
            return f"{months:.1f} months"
        except (ValueError, TypeError):
            return "unknown"
