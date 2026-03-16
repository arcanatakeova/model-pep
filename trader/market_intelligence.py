"""
Market Intelligence Engine — Big-Picture Solana Memecoin Analysis
=================================================================
Tracks every token the scanner touches to build a persistent, rolling
model of the entire Solana memecoin market.  All scanner instances share
one engine (singleton) so the picture accumulates across cycles.

Capabilities:
  - Macro sentiment:  is the market in HOT / NEUTRAL / COLD mode right now?
  - Narrative heat:   which categories (AI, dog, trump…) are currently pumping?
  - Outcome learning: did tokens with signal X actually gain?  (1h / 4h windows)
  - Context scoring:  score multiplier + narrative boost fed back into scoring
  - Breadth metrics:  how many tokens is the bot seeing per hour / day?

Usage (from dex_screener.py):
    from market_intelligence import get_engine
    eng = get_engine()
    eng.record_scan_batch(raw_tokens)
    boost   = eng.get_narrative_boost(token.base_symbol)
    mult    = eng.get_market_context_multiplier()
    summary = eng.get_market_summary()  # for logging
"""
from __future__ import annotations

import json
import logging
import os
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Narrative fingerprints ─────────────────────────────────────────────────────
# Tokens are bucketed into categories by fuzzy-matching their symbol/name.
# The engine then tracks how each category is performing across the whole market.
_NARRATIVES: dict[str, list[str]] = {
    "ai":        ["ai", "gpt", "llm", "neural", "agent", "agi", "robot",
                  "cyber", "algo", "deepseek", "claude", "gemini", "compute"],
    "animal":    ["dog", "cat", "pepe", "frog", "bear", "bull", "wolf", "ape",
                  "monkey", "whale", "doge", "shib", "bonk", "floki", "hamster",
                  "duck", "bird", "fish", "crab", "rat", "pig", "cow"],
    "trump":     ["trump", "maga", "don", "melania", "barron", "ivanka",
                  "eric", "potus", "whitehouse", "maga"],
    "elon":      ["elon", "musk", "tesla", "grok", "xai", "spacex"],
    "gaming":    ["game", "play", "quest", "arena", "battle", "hero",
                  "rpg", "guild", "raid", "nft", "pixel", "metaverse"],
    "defi":      ["swap", "lend", "yield", "stake", "vault", "pool",
                  "farm", "liquid", "dex", "amm", "perp", "options"],
    "meme":      ["meme", "wojak", "chad", "based", "degen", "wagmi",
                  "ngmi", "lfg", "rekt", "fud", "moon", "wen"],
    "space":     ["moon", "mars", "space", "galaxy", "star", "cosmos",
                  "astro", "nasa", "alien", "orbit", "rocket", "launch"],
    "celeb":     ["taylor", "swift", "kanye", "bieber", "drake",
                  "rihanna", "kylie", "kardashian", "lebron", "kobe"],
    "political": ["biden", "harris", "congress", "vote", "senate",
                  "election", "senator", "president", "liberal", "maga"],
}


def _detect_narrative(symbol: str, name: str = "") -> str:
    """Classify a token into a narrative bucket based on its symbol / name."""
    text = (symbol + " " + name).lower()
    for cat, keywords in _NARRATIVES.items():
        if any(kw in text for kw in keywords):
            return cat
    return "other"


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class TokenRecord:
    """Immutable point-in-time snapshot of a token as seen during a scan."""
    address:          str
    symbol:           str
    pair_address:     str
    score:            float
    price_usd:        float
    market_cap:       float
    volume_h1:        float
    volume_h24:       float
    price_change_m5:  float
    price_change_h1:  float
    price_change_h24: float
    liquidity_usd:    float
    buys_h1:          int
    sells_h1:         int
    narrative:        str
    source:           str
    scanned_at:       float = field(default_factory=time.time)
    # Outcome fields — filled retrospectively by record_price_update()
    outcome_1h_pct:   Optional[float] = None
    outcome_4h_pct:   Optional[float] = None


# ── Engine ─────────────────────────────────────────────────────────────────────

class MarketIntelligenceEngine:
    """
    Singleton.  All DexScreener instances (across threads) share one engine so
    the market picture accumulates continuously rather than being reset.
    """

    _instance: Optional[MarketIntelligenceEngine] = None
    _cls_lock: threading.Lock = threading.Lock()

    def __new__(cls):
        with cls._cls_lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._ready = False
                cls._instance = obj
        return cls._instance

    def __init__(self):
        if self._ready:
            return
        self._ready = True
        self._lock = threading.Lock()

        # Rolling 8-hour window of every token the scanner touches
        self._records: deque[TokenRecord] = deque(maxlen=50_000)

        # Tokens awaiting outcome resolution (address → list[TokenRecord])
        self._pending: dict[str, list[TokenRecord]] = defaultdict(list)

        # Per score-tier gain history: "score_70" → deque of 1h outcome %s
        self._tier_perf: dict[str, deque] = defaultdict(lambda: deque(maxlen=300))

        # Cached results — avoid recomputing on every token score call
        self._sentiment_cache: tuple[float, float, str] = (0.0, 0.5, "NEUTRAL")
        self._narrative_cache: tuple[float, list] = (0.0, [])
        self._CACHE_TTL = 20   # seconds

        # Persistent outcome stats saved across restarts
        self._stats_path = os.path.join(os.path.dirname(__file__), "market_outcomes.json")
        self._outcome_stats: dict = self._load_stats()

        logger.info("MarketIntelligenceEngine online — tracking market breadth + sentiment")

    # ── Feed API (called by DexScreener) ──────────────────────────────────────

    def record_scan_batch(self, tokens: list) -> None:
        """
        Ingest ALL tokens discovered in a scan cycle (not just the top-scored ones).
        The engine needs the full breadth to compute accurate macro stats.
        tokens: list[DexToken]
        """
        now = time.time()
        with self._lock:
            for t in tokens:
                if not getattr(t, "base_address", None) or not getattr(t, "price_usd", 0):
                    continue
                narrative = _detect_narrative(t.base_symbol or "")
                rec = TokenRecord(
                    address          = t.base_address,
                    symbol           = t.base_symbol or "",
                    pair_address     = t.pair_address or "",
                    score            = t.score,
                    price_usd        = t.price_usd,
                    market_cap       = t.market_cap or 0.0,
                    volume_h1        = t.volume_h1 or 0.0,
                    volume_h24       = t.volume_h24 or 0.0,
                    price_change_m5  = t.price_change_m5 or 0.0,
                    price_change_h1  = t.price_change_h1 or 0.0,
                    price_change_h24 = t.price_change_h24 or 0.0,
                    liquidity_usd    = t.liquidity_usd or 0.0,
                    buys_h1          = t.buys_h1 or 0,
                    sells_h1         = t.sells_h1 or 0,
                    narrative        = narrative,
                    source           = getattr(t, "source", ""),
                    scanned_at       = now,
                )
                self._records.append(rec)
                # Track tokens above threshold for outcome learning
                if t.score >= 0.33:
                    self._pending[t.base_address].append(rec)
            self._evict_pending()

    def record_price_update(self, address: str, current_price: float) -> None:
        """
        Called by the price monitor for every held (or recently seen) token.
        Resolves 1h and 4h outcomes for pending records.
        """
        now = time.time()
        with self._lock:
            for rec in self._pending.get(address, []):
                if rec.price_usd <= 0:
                    continue
                elapsed = now - rec.scanned_at
                gain_pct = (current_price - rec.price_usd) / rec.price_usd * 100

                if elapsed >= 3600 and rec.outcome_1h_pct is None:
                    rec.outcome_1h_pct = gain_pct
                    tier = f"score_{min(9, int(rec.score * 10)) * 10}"
                    self._tier_perf[tier].append(gain_pct)

                if elapsed >= 14400 and rec.outcome_4h_pct is None:
                    rec.outcome_4h_pct = gain_pct

    # ── Query API (called by scoring + logging) ───────────────────────────────

    def get_market_sentiment(self) -> tuple[float, str]:
        """
        Macro market state derived from the last 5 minutes of scan data.
        Returns (score 0–1, label) where:
          > 0.65 → HOT   (bull mode — many tokens moving up with buy pressure)
          < 0.35 → COLD  (bear mode — declining prices, sell pressure)
          else   → NEUTRAL
        """
        ts, score, label = self._sentiment_cache
        if time.time() - ts < self._CACHE_TTL:
            return score, label

        now = time.time()
        with self._lock:
            recent = [r for r in self._records if now - r.scanned_at < 300]

        if len(recent) < 20:
            return 0.5, "NEUTRAL"

        n = len(recent)
        avg_m5      = sum(r.price_change_m5 for r in recent) / n
        avg_h1      = sum(r.price_change_h1 for r in recent) / n
        pct_pos_m5  = sum(1 for r in recent if r.price_change_m5 > 0) / n
        pct_pos_h1  = sum(1 for r in recent if r.price_change_h1 > 0) / n
        avg_bsr     = sum(
            r.buys_h1 / max(r.buys_h1 + r.sells_h1, 1)
            for r in recent
        ) / n
        # Fraction of tokens with volume above their 24h hourly avg
        vol_accel   = sum(
            1 for r in recent
            if r.volume_h24 > 0 and r.volume_h1 / (r.volume_h24 / 24) > 1.5
        ) / n

        # Normalise each component to [0, 1]
        m5_sig  = min(1.0, max(0.0, (avg_m5  + 10) / 20))
        h1_sig  = min(1.0, max(0.0, (avg_h1  + 25) / 50))
        bsr_sig = min(1.0, max(0.0, (avg_bsr - 0.30) / 0.40))

        score = (
            m5_sig     * 0.30 +
            h1_sig     * 0.25 +
            pct_pos_m5 * 0.15 +
            pct_pos_h1 * 0.15 +
            bsr_sig    * 0.10 +
            vol_accel  * 0.05
        )
        label = "HOT" if score > 0.65 else ("COLD" if score < 0.35 else "NEUTRAL")
        self._sentiment_cache = (now, score, label)
        return score, label

    def get_hot_narratives(self, top_n: int = 3) -> list[tuple[str, int, float]]:
        """
        Returns [(narrative, token_count, avg_h1_pct)] for the hottest categories
        seen in the last 30 minutes.  Only categories with ≥ 3 tokens are reported.
        """
        ts, cached = self._narrative_cache
        if time.time() - ts < self._CACHE_TTL:
            return cached[:top_n]

        now = time.time()
        with self._lock:
            recent = [r for r in self._records
                      if now - r.scanned_at < 1800 and r.narrative != "other"
                      and r.score >= 0.20]

        by_narrative: dict[str, list[float]] = defaultdict(list)
        for r in recent:
            by_narrative[r.narrative].append(r.price_change_h1)

        scored = []
        for narr, gains in by_narrative.items():
            if len(gains) < 3:
                continue
            avg_gain   = sum(gains) / len(gains)
            pct_hot    = sum(1 for g in gains if g > 15) / len(gains)
            heat_score = avg_gain * 0.6 + pct_hot * 100 * 0.4
            scored.append((narr, len(gains), avg_gain, heat_score))

        scored.sort(key=lambda x: x[3], reverse=True)
        result = [(n, cnt, avg) for n, cnt, avg, _ in scored]
        self._narrative_cache = (time.time(), result)
        return result[:top_n]

    def get_narrative_boost(self, symbol: str, name: str = "") -> tuple[float, str]:
        """
        Returns (boost 0.0–0.07, label) if this token's narrative is currently hot.
        Uses the top-3 hot narratives; boost scales with avg hourly gain.
        """
        narrative = _detect_narrative(symbol, name)
        if narrative == "other":
            return 0.0, ""
        hot = self.get_hot_narratives(top_n=3)
        for rank, (n, cnt, avg_gain) in enumerate(hot):
            if n == narrative:
                # Scale: avg +500% h1 across category → max boost 0.07
                boost = min(0.07, max(0.01, avg_gain / 500 * 0.07))
                tier  = ["🔥", "🌡", "📈"][rank] if rank < 3 else ""
                label = f"{tier} {narrative} sector +{avg_gain:.0f}% avg"
                return boost, label
        return 0.0, ""

    def get_market_context_multiplier(self) -> float:
        """
        Score multiplier reflecting overall market conditions:
          HOT   → 1.05 (slightly lower effective threshold — ride the tide)
          COLD  → 0.93 (raise the bar — only the best setups)
          NEUTRAL → 1.0
        """
        _, label = self.get_market_sentiment()
        return {"HOT": 1.05, "COLD": 0.93, "NEUTRAL": 1.0}.get(label, 1.0)

    def get_outcome_calibration(self) -> str:
        """
        Returns a one-line summary of how well each score tier predicted gains.
        e.g. 'score_90: +48% avg | score_70: +21% avg | score_50: +4% avg'
        """
        parts = []
        for tier in ("score_90", "score_80", "score_70", "score_60", "score_50"):
            g = list(self._tier_perf.get(tier, []))
            if len(g) >= 5:
                avg = sum(g) / len(g)
                wr  = sum(1 for x in g if x > 15) / len(g)
                parts.append(f"{tier}: {avg:+.0f}% avg {wr:.0%} win ({len(g)})")
        return " | ".join(parts) if parts else "calibration pending"

    def get_market_summary(self) -> dict:
        """Full market snapshot for logging and dashboard."""
        sentiment_score, sentiment_label = self.get_market_sentiment()
        hot_narr = self.get_hot_narratives(top_n=5)
        now = time.time()
        with self._lock:
            total_1h    = len(set(r.address for r in self._records
                                  if now - r.scanned_at < 3600))
            total_today = len(set(r.address for r in self._records
                                  if now - r.scanned_at < 86400))
            scored_high = sum(1 for r in self._records
                              if r.score >= 0.60 and now - r.scanned_at < 3600)
            with_outcome = [r for r in self._records if r.outcome_1h_pct is not None]
            win_1h  = (sum(1 for r in with_outcome if r.outcome_1h_pct > 15)
                       / len(with_outcome) if with_outcome else 0)
            avg_1h  = (sum(r.outcome_1h_pct for r in with_outcome)
                       / len(with_outcome) if with_outcome else 0)

        return {
            "sentiment":        sentiment_label,
            "sentiment_score":  round(sentiment_score, 3),
            "context_mult":     self.get_market_context_multiplier(),
            "tokens_seen_1h":   total_1h,
            "tokens_seen_today": total_today,
            "high_score_1h":    scored_high,
            "hot_narratives":   [(n, f"+{a:.0f}%") for n, _, a in hot_narr],
            "outcome_n":        len(with_outcome),
            "win_rate_1h":      round(win_1h, 2),
            "avg_gain_1h":      round(avg_1h, 1),
            "calibration":      self.get_outcome_calibration(),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _evict_pending(self) -> None:
        """Drop pending outcome records older than 8 hours."""
        cutoff = time.time() - 28800
        stale  = [addr for addr, recs in self._pending.items()
                  if all(r.scanned_at < cutoff for r in recs)]
        for addr in stale:
            del self._pending[addr]

    def _load_stats(self) -> dict:
        try:
            if os.path.exists(self._stats_path):
                with open(self._stats_path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save_stats(self) -> None:
        """Persist outcome stats to disk (call periodically from main loop)."""
        try:
            stats = {
                "updated_at": time.time(),
                "calibration": self.get_outcome_calibration(),
                "outcome_n":   sum(len(v) for v in self._tier_perf.values()),
            }
            with open(self._stats_path, "w") as f:
                json.dump(stats, f, indent=2)
        except Exception as e:
            logger.debug("market_intelligence: stats save failed: %s", e)


# ── Module-level singleton accessor ───────────────────────────────────────────

_engine: Optional[MarketIntelligenceEngine] = None


def get_engine() -> MarketIntelligenceEngine:
    """Return the shared singleton MarketIntelligenceEngine."""
    global _engine
    if _engine is None:
        _engine = MarketIntelligenceEngine()
    return _engine
