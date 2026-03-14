"""
DEX Screener — Token Discovery and On-Chain Market Intelligence
==============================================================
Free API, no key required. Covers 50+ chains.

Strategies:
- Trending token detection (volume surge + buy pressure)
- New pair sniping (fresh liquidity with strong momentum)
- Breakout detection (price action + transaction velocity)
- Liquidity safety scoring (rug-pull risk filter)
"""
from __future__ import annotations
import logging
import time
import requests
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

import config
from token_safety import TokenSafetyChecker

logger = logging.getLogger(__name__)

DEXSCREENER_BASE = "https://api.dexscreener.com"

# ── Filters ────────────────────────────────────────────────────────────────────
MIN_LIQUIDITY_USD    = 10_000      # $10k liquidity (lower for Solana memecoins)
MIN_VOLUME_H1_USD    = 2_000       # $2k/hr volume (memecoins move fast)
MIN_VOLUME_H24_USD   = 20_000      # $20k/24h volume
MAX_PAIR_AGE_HOURS   = 96          # Up to 4 days old
MIN_BUY_SELL_RATIO   = 1.1         # Buys exceed sells by 10%
MIN_MARKET_CAP       = 50_000      # $50k mcap minimum
MAX_MARKET_CAP       = 500_000_000 # Under $500M (room to run)
MIN_PRICE_CHANGE_H1  = 2.0         # At least +2% last hour
PREFERRED_CHAINS     = ["solana"]  # Solana only


@dataclass
class DexToken:
    """Scored token opportunity from DEX Screener."""
    chain_id: str
    dex_id: str
    pair_address: str
    base_symbol: str
    base_address: str
    quote_symbol: str
    price_usd: float
    price_change_m5: float
    price_change_h1: float
    price_change_h6: float
    price_change_h24: float
    volume_h1: float
    volume_h24: float
    liquidity_usd: float
    market_cap: float
    buys_h1: int
    sells_h1: int
    buys_h24: int
    sells_h24: int
    pair_created_at: Optional[int]  # unix timestamp ms
    url: str
    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    safety_report: Optional[object] = None  # TokenSafetyReport when available

    @property
    def buy_sell_ratio_h1(self) -> float:
        total = self.buys_h1 + self.sells_h1
        return self.buys_h1 / total if total > 0 else 0.5

    @property
    def age_hours(self) -> Optional[float]:
        if self.pair_created_at:
            return (time.time() * 1000 - self.pair_created_at) / 3_600_000
        return None

    def to_dict(self) -> dict:
        return {
            "chain": self.chain_id,
            "dex": self.dex_id,
            "symbol": self.base_symbol,
            "address": self.base_address,
            "pair_address": self.pair_address,
            "price_usd": self.price_usd,
            "change_1h_pct": self.price_change_h1,
            "change_24h_pct": self.price_change_h24,
            "volume_1h": self.volume_h1,
            "volume_24h": self.volume_h24,
            "liquidity_usd": self.liquidity_usd,
            "market_cap": self.market_cap,
            "buy_sell_ratio": round(self.buy_sell_ratio_h1, 2),
            "age_hours": self.age_hours,
            "score": round(self.score, 3),
            "signals": self.signals,
            "url": self.url,
            "safety_score": self.safety_report.safety_score if self.safety_report else None,
            "risk_level": self.safety_report.risk_level if self.safety_report else None,
            "risk_flags": self.safety_report.risk_flags if self.safety_report else [],
        }


class DexScreener:
    """
    DEX Screener API client and token opportunity scorer.
    """

    def __init__(self):
        self._cache: dict = {}
        self._cache_ttl = 60  # seconds
        self._safety_checker = TokenSafetyChecker()

    # ─── Public API ───────────────────────────────────────────────────────────

    def get_trending_tokens(self, min_score: float = 0.5) -> list[DexToken]:
        """
        Find high-momentum tokens across all chains.
        Combines boosted tokens + volume surge detection.
        """
        tokens = []

        # 1. Boosted / trending tokens
        boosted = self._fetch_boosted_tokens()
        tokens.extend(self._fetch_pairs_for_tokens(boosted))

        # 2. Top gainers by search on preferred chains
        for chain in PREFERRED_CHAINS[:4]:
            gainers = self._search_top_pairs(chain)
            tokens.extend(gainers)

        # Deduplicate by pair address
        seen = set()
        unique = []
        for t in tokens:
            if t.pair_address not in seen:
                seen.add(t.pair_address)
                unique.append(t)

        # Score and filter
        scored = [t for t in unique if self._score_token(t) >= min_score]
        scored.sort(key=lambda t: t.score, reverse=True)
        logger.info("DEX Screener: %d trending tokens (score >= %.2f)", len(scored), min_score)
        return scored

    def get_new_pairs(self, max_age_hours: float = MAX_PAIR_AGE_HOURS,
                      min_score: float = 0.55) -> list[DexToken]:
        """
        Find newly created pairs with strong early momentum.
        These can be 10-100x opportunities — also highest risk.
        """
        all_pairs = []
        for chain in PREFERRED_CHAINS[:4]:
            pairs = self._search_top_pairs(chain, limit=30)
            new = [p for p in pairs
                   if p.age_hours is not None and p.age_hours <= max_age_hours
                   and p.liquidity_usd >= MIN_LIQUIDITY_USD]
            all_pairs.extend(new)

        scored = [t for t in all_pairs if self._score_token(t) >= min_score]
        scored.sort(key=lambda t: t.score, reverse=True)
        logger.info("DEX Screener: %d new pairs (< %.0fh old)", len(scored), max_age_hours)
        return scored

    def get_token_info(self, token_address: str, chain: str = "solana") -> Optional[DexToken]:
        """Fetch data for a specific token address."""
        data = self._get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{token_address}")
        if not data or not data.get("pairs"):
            return None
        pairs = [p for p in data["pairs"] if p.get("chainId") == chain]
        if not pairs:
            pairs = data["pairs"]
        # Return the highest liquidity pair
        pairs.sort(key=lambda p: p.get("liquidity", {}).get("usd", 0), reverse=True)
        token = self._parse_pair(pairs[0])
        if token:
            self._score_token(token)
        return token

    def search_token(self, query: str) -> list[DexToken]:
        """Search tokens by name or symbol."""
        data = self._get(f"{DEXSCREENER_BASE}/latest/dex/search", params={"q": query})
        if not data or not data.get("pairs"):
            return []
        tokens = [self._parse_pair(p) for p in data["pairs"][:20]]
        tokens = [t for t in tokens if t is not None]
        for t in tokens:
            self._score_token(t)
        return sorted(tokens, key=lambda t: t.score, reverse=True)

    def get_multi_chain_opportunities(self) -> list[DexToken]:
        """
        Full scan: trending + new pairs across all preferred chains.
        Returns deduplicated, scored, and filtered list.
        """
        trending = self.get_trending_tokens(min_score=0.45)
        new_pairs = self.get_new_pairs(max_age_hours=24, min_score=0.50)

        all_tokens = trending + new_pairs
        seen = set()
        unique = []
        for t in all_tokens:
            if t.pair_address not in seen:
                seen.add(t.pair_address)
                unique.append(t)

        unique.sort(key=lambda t: t.score, reverse=True)
        return unique

    # ─── Scoring ──────────────────────────────────────────────────────────────

    def _score_token(self, token: DexToken) -> float:
        """
        Multi-factor token opportunity score [0, 1].
        Higher = stronger opportunity.
        """
        score = 0.0
        signals = []

        # ── Safety checks (disqualifiers) ────────────────────────────────
        if token.liquidity_usd < MIN_LIQUIDITY_USD:
            token.score = 0.0
            return 0.0
        if token.volume_h24 < MIN_VOLUME_H24_USD:
            token.score = 0.0
            return 0.0
        if token.market_cap > 0 and token.market_cap < MIN_MARKET_CAP:
            token.score = 0.0
            return 0.0

        # ── Price momentum (30%) ─────────────────────────────────────────
        pc_h1 = token.price_change_h1
        if pc_h1 > 20:
            score += 0.30
            signals.append(f"Explosive +{pc_h1:.0f}% 1h")
        elif pc_h1 > 10:
            score += 0.25
            signals.append(f"Strong +{pc_h1:.0f}% 1h")
        elif pc_h1 > 5:
            score += 0.18
            signals.append(f"Bullish +{pc_h1:.0f}% 1h")
        elif pc_h1 > 2:
            score += 0.10
        elif pc_h1 < -10:
            score -= 0.20
        elif pc_h1 < -5:
            score -= 0.10

        # 24h context
        pc_h24 = token.price_change_h24
        if pc_h24 > 50:
            score += 0.10
            signals.append(f"+{pc_h24:.0f}% 24h")
        elif pc_h24 > 20:
            score += 0.06

        # ── Volume surge (25%) ───────────────────────────────────────────
        if token.volume_h1 > 0 and token.volume_h24 > 0:
            hourly_avg = token.volume_h24 / 24
            if hourly_avg > 0:
                vol_ratio = token.volume_h1 / hourly_avg
                if vol_ratio > 5:
                    score += 0.25
                    signals.append(f"Volume surge {vol_ratio:.0f}x")
                elif vol_ratio > 3:
                    score += 0.18
                    signals.append(f"Volume spike {vol_ratio:.1f}x")
                elif vol_ratio > 2:
                    score += 0.12
                    signals.append(f"Volume up {vol_ratio:.1f}x")
                elif vol_ratio > 1.5:
                    score += 0.06

        # ── Buy pressure (20%) ───────────────────────────────────────────
        bsr = token.buy_sell_ratio_h1
        total_txns = token.buys_h1 + token.sells_h1
        if bsr > 0.75 and total_txns > 50:
            score += 0.20
            signals.append(f"Strong buy pressure {bsr:.0%}")
        elif bsr > 0.65 and total_txns > 20:
            score += 0.14
            signals.append(f"Buy pressure {bsr:.0%}")
        elif bsr > 0.55:
            score += 0.07
        elif bsr < 0.40:
            score -= 0.10

        # ── Liquidity quality (15%) ──────────────────────────────────────
        liq = token.liquidity_usd
        if liq > 1_000_000:
            score += 0.15
        elif liq > 500_000:
            score += 0.12
        elif liq > 200_000:
            score += 0.10
        elif liq > 100_000:
            score += 0.07
        elif liq > 50_000:
            score += 0.04

        # ── New pair bonus (10%) — early mover advantage ─────────────────
        age = token.age_hours
        if age is not None:
            if age < 1:
                score += 0.10
                signals.append("Brand new pair (<1h)")
            elif age < 6:
                score += 0.08
                signals.append(f"New pair ({age:.0f}h old)")
            elif age < 24:
                score += 0.05
            elif age > 168:  # > 1 week old
                score -= 0.03

        # ── Chain preference ─────────────────────────────────────────────
        if token.chain_id == "solana":
            score += 0.03   # Solana: high velocity, Phantom-tradeable

        # ── Token safety check (SAFETY_SCORE_WEIGHT weight) ────────────
        if token.chain_id == "solana" and score > 0.20:
            try:
                safety = self._safety_checker.check_token_safety(token.base_address)
                token.safety_report = safety

                if not safety.is_safe_to_trade:
                    token.score = 0.0
                    token.signals = [f"BLOCKED: {safety.risk_level} risk"
                                     ] + safety.risk_flags[:2]
                    return 0.0

                # Blend safety into composite score
                w = config.SAFETY_SCORE_WEIGHT
                score = score * (1 - w) + safety.safety_score * w

                if safety.risk_level in ("SAFE", "LOW"):
                    signals.append(f"Safety: {safety.risk_level} ({safety.safety_score:.2f})")
                elif safety.risk_level == "MEDIUM":
                    flag = safety.risk_flags[0] if safety.risk_flags else "caution"
                    signals.append(f"Safety: MEDIUM ({safety.safety_score:.2f}) - {flag}")
                elif safety.risk_level == "HIGH":
                    flag = safety.risk_flags[0] if safety.risk_flags else "risky"
                    signals.append(f"Safety: HIGH ({safety.safety_score:.2f}) - {flag}")
            except Exception as e:
                logger.warning("Safety check failed for %s: %s", token.base_symbol, e)
                score *= 0.85  # Penalize if safety check fails

        token.score = float(np.clip(score, 0, 1))
        token.signals = signals
        return token.score

    # ─── Data Fetching ────────────────────────────────────────────────────────

    def _fetch_boosted_tokens(self) -> list[dict]:
        """Fetch trending/boosted tokens from DexScreener."""
        # Latest boosted
        data = self._get(f"{DEXSCREENER_BASE}/token-boosts/latest/v1")
        boosted = data if isinstance(data, list) else []

        # Top boosted
        data2 = self._get(f"{DEXSCREENER_BASE}/token-boosts/top/v1")
        top = data2 if isinstance(data2, list) else []

        return boosted[:20] + top[:20]

    def _fetch_pairs_for_tokens(self, token_metas: list[dict]) -> list[DexToken]:
        """Fetch pair data for a list of token metadata objects."""
        tokens = []
        # Group by chain to batch requests
        by_chain: dict[str, list[str]] = {}
        for t in token_metas:
            chain = t.get("chainId", "")
            addr  = t.get("tokenAddress", "")
            if chain and addr:
                by_chain.setdefault(chain, []).append(addr)

        for chain, addresses in by_chain.items():
            if chain not in PREFERRED_CHAINS:
                continue
            for addr in addresses[:10]:
                data = self._get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{addr}")
                if data and data.get("pairs"):
                    pairs = [p for p in data["pairs"] if p.get("chainId") == chain]
                    if pairs:
                        # Take highest-liquidity pair
                        pairs.sort(key=lambda p: p.get("liquidity", {}).get("usd", 0), reverse=True)
                        token = self._parse_pair(pairs[0])
                        if token:
                            tokens.append(token)

        return tokens

    def _search_top_pairs(self, chain: str, query: str = "USD", limit: int = 20) -> list[DexToken]:
        """Search for top pairs on a given chain."""
        data = self._get(f"{DEXSCREENER_BASE}/latest/dex/search",
                         params={"q": query})
        if not data or not data.get("pairs"):
            return []

        chain_pairs = [p for p in data["pairs"] if p.get("chainId") == chain]
        tokens = []
        for p in chain_pairs[:limit]:
            token = self._parse_pair(p)
            if token:
                tokens.append(token)
        return tokens

    def _parse_pair(self, pair: dict) -> Optional[DexToken]:
        """Parse a DexScreener pair dict into a DexToken."""
        try:
            price_str = pair.get("priceUsd") or "0"
            price = float(price_str) if price_str else 0.0
            if price <= 0:
                return None

            txns   = pair.get("txns", {})
            volume = pair.get("volume", {})
            change = pair.get("priceChange", {})
            liq    = pair.get("liquidity", {})

            return DexToken(
                chain_id       = pair.get("chainId", ""),
                dex_id         = pair.get("dexId", ""),
                pair_address   = pair.get("pairAddress", ""),
                base_symbol    = pair.get("baseToken", {}).get("symbol", ""),
                base_address   = pair.get("baseToken", {}).get("address", ""),
                quote_symbol   = pair.get("quoteToken", {}).get("symbol", ""),
                price_usd      = price,
                price_change_m5  = float(change.get("m5") or 0),
                price_change_h1  = float(change.get("h1") or 0),
                price_change_h6  = float(change.get("h6") or 0),
                price_change_h24 = float(change.get("h24") or 0),
                volume_h1  = float(volume.get("h1") or 0),
                volume_h24 = float(volume.get("h24") or 0),
                liquidity_usd = float(liq.get("usd") or 0),
                market_cap    = float(pair.get("marketCap") or pair.get("fdv") or 0),
                buys_h1   = int(txns.get("h1", {}).get("buys", 0)),
                sells_h1  = int(txns.get("h1", {}).get("sells", 0)),
                buys_h24  = int(txns.get("h24", {}).get("buys", 0)),
                sells_h24 = int(txns.get("h24", {}).get("sells", 0)),
                pair_created_at = pair.get("pairCreatedAt"),
                url = pair.get("url", ""),
            )
        except Exception as e:
            logger.debug("Parse error: %s", e)
            return None

    def _get(self, url: str, params: dict = None, timeout: int = 10) -> Optional[dict | list]:
        cache_key = f"{url}:{params}"
        if cache_key in self._cache:
            val, ts = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return val

        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=timeout,
                                    headers={"User-Agent": "ai-trader/1.0"})
                if resp.status_code == 429:
                    time.sleep(2 ** attempt * 2)
                    continue
                resp.raise_for_status()
                data = resp.json()
                self._cache[cache_key] = (data, time.time())
                return data
            except Exception as e:
                logger.debug("DexScreener request error: %s", e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None
