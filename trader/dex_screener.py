"""
DEX Screener — Token Discovery and On-Chain Market Intelligence
==============================================================
Multi-source deep scanner covering:
- DexScreener boosted/trending/profiles (500+ candidates per scan)
- Pump.fun live feed (brand-new Solana token launches)
- Raydium pool feed (highest-volume Solana AMM pools)
- Birdeye trending + new listings (real-time Solana price intelligence)
- Jupiter price validation (pre-trade price confirmation)

Scoring engine v2:
- 5-minute momentum (strongest short-term predictor)
- Volume acceleration (is it accelerating or decelerating?)
- Transaction velocity (raw tx count, not just ratio)
- Age-adjusted momentum (new pairs get bigger early-mover bonus)
- Concurrent safety checks via ThreadPoolExecutor
"""
from __future__ import annotations
import logging
import time
import concurrent.futures
import requests
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

import config
from token_safety import TokenSafetyChecker

# Birdeye client — lazy-loaded only if API key is set
_birdeye_client = None


def _get_birdeye():
    global _birdeye_client
    if _birdeye_client is None and config.BIRDEYE_API_KEY:
        try:
            from birdeye import BirdeyeClient
            _birdeye_client = BirdeyeClient(config.BIRDEYE_API_KEY)
        except Exception:
            pass
    return _birdeye_client


logger = logging.getLogger(__name__)

DEXSCREENER_BASE = "https://api.dexscreener.com"
PUMPFUN_BASE     = "https://frontend-api.pump.fun"
RAYDIUM_BASE     = "https://api-v3.raydium.io"
JUPITER_PRICE    = "https://api.jup.ag/price/v2"

# ── Hard filters (applied before scoring) ──────────────────────────────────────
MIN_LIQUIDITY_USD  = 8_000       # $8k — slightly lower catches early pumps
MIN_VOLUME_H24_USD = 15_000      # $15k/24h
MIN_MARKET_CAP     = 30_000      # $30k mcap (pump.fun tokens start small)
MAX_MARKET_CAP     = 500_000_000 # $500M ceiling
MAX_PAIR_AGE_HOURS = 96          # Up to 4 days
PREFERRED_CHAINS   = ["solana"]

# Search terms — broad coverage across naming conventions
_SEARCH_QUERIES = ["SOL", "USDC", "PUMP", "MEME", "AI", "DOG", "WIF"]


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
    pair_created_at: Optional[int]   # unix timestamp ms
    url: str
    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    safety_report: Optional[object] = None
    # Extended fields populated by enrichment
    volume_m5: float = 0.0          # 5-min volume (from pair txns if available)
    buys_m5: int = 0
    sells_m5: int = 0
    source: str = "dexscreener"     # Where this token was discovered
    holder_count: int = 0           # Unique holders (from Birdeye token_overview)
    unique_wallets_24h: int = 0     # Unique trading wallets last 24h (Birdeye)

    @property
    def buy_sell_ratio_h1(self) -> float:
        total = self.buys_h1 + self.sells_h1
        return self.buys_h1 / total if total > 0 else 0.5

    @property
    def buy_sell_ratio_m5(self) -> float:
        total = self.buys_m5 + self.sells_m5
        return self.buys_m5 / total if total > 0 else 0.5

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
            "change_5m_pct": self.price_change_m5,
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
            "source": self.source,
            "url": self.url,
            "safety_score": self.safety_report.safety_score if self.safety_report else None,
            "risk_level": self.safety_report.risk_level if self.safety_report else None,
            "risk_flags": self.safety_report.risk_flags if self.safety_report else [],
        }


class DexScreener:
    """
    Multi-source DEX scanner with concurrent safety checks.
    Sources: DexScreener, Pump.fun, Raydium, Birdeye, Jupiter.
    """

    _CACHE_MAX = 500

    def __init__(self):
        self._cache: dict = {}
        self._cache_ttl = 25   # 25s — aggressive freshness for fast markets
        self._safety_checker = TokenSafetyChecker()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="dex-scanner")

    # ─── Public API ───────────────────────────────────────────────────────────

    def get_trending_tokens(self, min_score: float = 0.40) -> list[DexToken]:
        """
        Parallel fetch from all sources, concurrent safety checks, scored and filtered.
        """
        # Launch all data fetches in parallel
        futures = {
            "boosted":   self._executor.submit(self._fetch_boosted_tokens),
            "profiles":  self._executor.submit(self._fetch_token_profiles),
            "pumpfun":   self._executor.submit(self._fetch_pumpfun_tokens),
            "raydium":   self._executor.submit(self._fetch_raydium_pools),
            "be_trend":  self._executor.submit(self._fetch_birdeye_trending),
        }
        # Also kick off multi-query DexScreener searches in parallel
        search_futures = {
            f"search_{q}": self._executor.submit(
                self._search_top_pairs, "solana", q, 50)
            for q in _SEARCH_QUERIES
        }
        futures.update(search_futures)

        raw_tokens: list[DexToken] = []

        # Collect boosted + profiles via pair batch lookup
        for key in ("boosted", "profiles"):
            try:
                metas = futures[key].result(timeout=12)
                raw_tokens.extend(self._fetch_pairs_for_tokens(metas))
            except Exception as e:
                logger.debug("%s fetch error: %s", key, e)

        # Collect already-parsed token lists
        for key in ("pumpfun", "raydium", "be_trend") + tuple(search_futures):
            try:
                result = futures[key].result(timeout=12)
                raw_tokens.extend(result)
            except Exception as e:
                logger.debug("%s fetch error: %s", key, e)

        # Deduplicate
        seen = set()
        unique = []
        for t in raw_tokens:
            if t.pair_address and t.pair_address not in seen:
                seen.add(t.pair_address)
                unique.append(t)

        # ── Batch price refresh via Birdeye (1 CU total for all tokens) ──────────
        # DexScreener data can be up to 25s stale. Birdeye has 3s cache.
        # Replace price, market_cap, and liquidity for all Solana candidates at once.
        be = _get_birdeye()
        if be:
            sol_mints = [t.base_address for t in unique
                         if t.chain_id == "solana" and t.base_address]
            if sol_mints:
                try:
                    fresh_prices = be.get_multi_price(sol_mints)
                    for t in unique:
                        bp = fresh_prices.get(t.base_address)
                        if bp and bp.price_usd > 0:
                            t.price_usd = bp.price_usd
                            if bp.market_cap > 0:
                                t.market_cap = bp.market_cap
                            if bp.liquidity_usd > 0:
                                t.liquidity_usd = bp.liquidity_usd
                except Exception as e:
                    logger.debug("Batch price refresh error: %s", e)

        # Concurrent safety checks for Solana tokens above pre-score threshold
        # (pre-score without safety to decide which ones are worth checking)
        pre_scored = []
        for t in unique:
            ps = self._pre_score(t)
            if ps >= 0.20 or abs(t.price_change_h1) > 10 or abs(t.price_change_m5) > 5:
                pre_scored.append(t)

        if pre_scored:
            self._run_concurrent_safety_checks(pre_scored)

        # Full score + filter
        scored = []
        for t in unique:
            s = self._score_token(t)
            if s >= min_score:
                scored.append(t)
        scored.sort(key=lambda t: t.score, reverse=True)

        logger.info("Trending scan: %d sources → %d candidates → %d scored >= %.2f",
                    len(futures), len(unique), len(scored), min_score)
        return scored

    def get_new_pairs(self, max_age_hours: float = 48,
                      min_score: float = 0.40) -> list[DexToken]:
        """
        New pair sniping: DexScreener search + Birdeye new listings + Pump.fun new.
        """
        futures = {
            "be_new":    self._executor.submit(self._fetch_birdeye_new_listings),
            "pump_new":  self._executor.submit(self._fetch_pumpfun_new),
        }
        search_futures = {
            f"search_{q}": self._executor.submit(
                self._search_top_pairs, "solana", q, 50)
            for q in ["SOL", "USDC", "USD"]
        }
        futures.update(search_futures)

        raw: list[DexToken] = []
        for key, fut in futures.items():
            try:
                raw.extend(fut.result(timeout=12))
            except Exception as e:
                logger.debug("new_pairs %s error: %s", key, e)

        # Filter by age + liquidity
        fresh = [t for t in raw
                 if (t.age_hours is None or t.age_hours <= max_age_hours)
                 and t.liquidity_usd >= MIN_LIQUIDITY_USD]

        # Dedup
        seen = set()
        unique = []
        for t in fresh:
            if t.pair_address and t.pair_address not in seen:
                seen.add(t.pair_address)
                unique.append(t)

        # ── Batch Birdeye price refresh for new pairs ─────────────────────────────
        be = _get_birdeye()
        if be:
            sol_mints = [t.base_address for t in unique
                         if t.chain_id == "solana" and t.base_address]
            if sol_mints:
                try:
                    fresh_prices = be.get_multi_price(sol_mints)
                    for t in unique:
                        bp = fresh_prices.get(t.base_address)
                        if bp and bp.price_usd > 0:
                            t.price_usd = bp.price_usd
                            if bp.market_cap > 0:
                                t.market_cap = bp.market_cap
                            if bp.liquidity_usd > 0:
                                t.liquidity_usd = bp.liquidity_usd
                except Exception as e:
                    logger.debug("New pairs batch price refresh error: %s", e)

        # Safety checks for new pairs (most likely to be rugs — always check)
        self._run_concurrent_safety_checks(unique)

        scored = [t for t in unique if self._score_token(t) >= min_score]
        scored.sort(key=lambda t: t.score, reverse=True)
        logger.info("New pairs scan: %d candidates → %d scored >= %.2f",
                    len(unique), len(scored), min_score)
        return scored

    def get_multi_chain_opportunities(self) -> list[DexToken]:
        """
        Full scan combining trending + new pairs. Used by main trading cycle.
        """
        # Run both in parallel since they hit different API endpoints
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_trend = ex.submit(self.get_trending_tokens, config.DEX_MIN_SCORE)
            f_new   = ex.submit(self.get_new_pairs, 48, config.DEX_MIN_SCORE)
            trending  = f_trend.result(timeout=30)
            new_pairs = f_new.result(timeout=30)

        all_tokens = trending + new_pairs
        seen = set()
        unique = []
        for t in all_tokens:
            if t.pair_address not in seen:
                seen.add(t.pair_address)
                unique.append(t)

        unique.sort(key=lambda t: t.score, reverse=True)
        logger.info("Full DEX scan complete: %d unique opportunities", len(unique))
        return unique

    def get_token_info(self, token_address: str, chain: str = "solana") -> Optional[DexToken]:
        """
        Fetch data for a specific token, enriched with Birdeye real-time price.
        """
        data = self._get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{token_address}")
        if not data or not data.get("pairs"):
            return None
        pairs = [p for p in data["pairs"] if p.get("chainId") == chain]
        if not pairs:
            pairs = data["pairs"]
        pairs.sort(key=lambda p: p.get("liquidity", {}).get("usd", 0), reverse=True)
        token = self._parse_pair(pairs[0])
        if not token:
            return None

        be = _get_birdeye()
        if be and chain == "solana":
            try:
                bp = be.get_price(token_address)
                if bp and bp.price_usd > 0:
                    token.price_usd        = bp.price_usd
                    token.price_change_h24 = bp.price_change_24h_pct
                    if bp.volume_24h_usd > 0:
                        token.volume_h24   = bp.volume_24h_usd
                    if bp.liquidity_usd > 0:
                        token.liquidity_usd = bp.liquidity_usd
                    if bp.market_cap > 0:
                        token.market_cap   = bp.market_cap  # Birdeye mcap > DexScreener FDV
            except Exception:
                pass

        self._score_token(token)
        return token

    def search_token(self, query: str) -> list[DexToken]:
        data = self._get(f"{DEXSCREENER_BASE}/latest/dex/search", params={"q": query})
        if not data or not data.get("pairs"):
            return []
        tokens = [self._parse_pair(p) for p in data["pairs"][:20]]
        tokens = [t for t in tokens if t is not None]
        for t in tokens:
            self._score_token(t)
        return sorted(tokens, key=lambda t: t.score, reverse=True)

    # ─── Scoring ──────────────────────────────────────────────────────────────

    def _pre_score(self, token: DexToken) -> float:
        """Fast pre-score without safety checks — used to decide if safety check is worth running."""
        if token.liquidity_usd < MIN_LIQUIDITY_USD:
            return 0.0
        if token.volume_h24 < MIN_VOLUME_H24_USD:
            return 0.0
        s = 0.0
        if token.price_change_m5 > 3:
            s += 0.25
        if token.price_change_h1 > 10:
            s += 0.20
        elif token.price_change_h1 > 5:
            s += 0.12
        if token.volume_h1 > 0 and token.volume_h24 > 0:
            ratio = token.volume_h1 / (token.volume_h24 / 24)
            if ratio > 3:
                s += 0.20
        if token.buy_sell_ratio_h1 > 0.65:
            s += 0.15
        return s

    def _score_token(self, token: DexToken) -> float:
        """
        Full multi-factor scoring engine v2.
        Weights tuned for short-term Solana memecoin momentum trading.
        """
        score = 0.0
        signals = []

        # ── Hard disqualifiers ────────────────────────────────────────────
        if token.liquidity_usd < MIN_LIQUIDITY_USD:
            token.score = 0.0
            return 0.0
        if token.volume_h24 < MIN_VOLUME_H24_USD:
            token.score = 0.0
            return 0.0
        if token.market_cap > 0 and token.market_cap < MIN_MARKET_CAP:
            token.score = 0.0
            return 0.0
        if token.market_cap > MAX_MARKET_CAP:
            token.score = 0.0
            return 0.0
        # Mcap/liquidity ratio: > 200x means only 0.5% of market cap is actually
        # tradeable — buying this is buying exit liquidity from whales.
        if (token.market_cap > 0 and token.liquidity_usd > 0 and
                token.market_cap / token.liquidity_usd > 200):
            token.score = 0.0
            return 0.0

        # ── 5-minute momentum (25%) — best predictor for short trades ─────
        # If it's moving hard right now, that's the signal we care about most
        m5 = token.price_change_m5
        if m5 > 15:
            score += 0.25
            signals.append(f"Explosive +{m5:.0f}% 5m")
        elif m5 > 8:
            score += 0.20
            signals.append(f"Hot +{m5:.0f}% 5m")
        elif m5 > 4:
            score += 0.14
            signals.append(f"Moving +{m5:.0f}% 5m")
        elif m5 > 2:
            score += 0.08
            signals.append(f"+{m5:.0f}% 5m")
        elif m5 < -8:
            score -= 0.20   # Dumping hard — avoid
        elif m5 < -4:
            score -= 0.10

        # ── 1-hour price momentum (20%) ───────────────────────────────────
        h1 = token.price_change_h1
        if h1 > 50:
            score += 0.20
            signals.append(f"Parabolic +{h1:.0f}% 1h")
        elif h1 > 20:
            score += 0.18
            signals.append(f"Explosive +{h1:.0f}% 1h")
        elif h1 > 10:
            score += 0.14
            signals.append(f"Strong +{h1:.0f}% 1h")
        elif h1 > 5:
            score += 0.09
            signals.append(f"Bullish +{h1:.0f}% 1h")
        elif h1 > 2:
            score += 0.04
        elif h1 < -15:
            score -= 0.18
        elif h1 < -8:
            score -= 0.10

        # ── 24h context (5%) — sustained trend vs one-hour pump ───────────
        h24 = token.price_change_h24
        if h24 > 100:
            score += 0.05
            signals.append(f"+{h24:.0f}% 24h")
        elif h24 > 50:
            score += 0.04
        elif h24 > 20:
            score += 0.02
        elif h24 < -30:
            score -= 0.05

        # ── Volume acceleration (20%) — is buying pressure building? ──────
        if token.volume_h1 > 0 and token.volume_h24 > 0:
            hourly_avg = token.volume_h24 / 24
            if hourly_avg > 0:
                vol_ratio = token.volume_h1 / hourly_avg
                if vol_ratio > 10:
                    score += 0.20
                    signals.append(f"Vol explosion {vol_ratio:.0f}x avg")
                elif vol_ratio > 5:
                    score += 0.16
                    signals.append(f"Vol surge {vol_ratio:.0f}x avg")
                elif vol_ratio > 3:
                    score += 0.12
                    signals.append(f"Vol spike {vol_ratio:.1f}x avg")
                elif vol_ratio > 2:
                    score += 0.08
                    signals.append(f"Vol up {vol_ratio:.1f}x")
                elif vol_ratio > 1.3:
                    score += 0.04
                elif vol_ratio < 0.5:
                    score -= 0.05   # Volume fading

        # ── Transaction velocity (15%) — raw activity level ───────────────
        total_txns_h1 = token.buys_h1 + token.sells_h1
        bsr = token.buy_sell_ratio_h1

        # Absolute buy count (not just ratio) matters — low-liquidity pairs
        # can have 100% buy ratio with only 5 transactions (manipulated)
        if bsr > 0.75 and total_txns_h1 > 200:
            score += 0.15
            signals.append(f"Frenzy: {token.buys_h1} buys/h ({bsr:.0%})")
        elif bsr > 0.70 and total_txns_h1 > 100:
            score += 0.12
            signals.append(f"Strong buys: {token.buys_h1}/h ({bsr:.0%})")
        elif bsr > 0.65 and total_txns_h1 > 50:
            score += 0.09
            signals.append(f"Buy pressure {bsr:.0%}")
        elif bsr > 0.60 and total_txns_h1 > 20:
            score += 0.05
        elif bsr < 0.40 and total_txns_h1 > 20:
            score -= 0.10   # Heavy selling

        # 5-min buy pressure (ultra-recent signal)
        if token.buys_m5 > 0:
            bsr_m5 = token.buy_sell_ratio_m5
            if bsr_m5 > 0.80 and token.buys_m5 > 20:
                score += 0.05
                signals.append(f"5m buy frenzy ({token.buys_m5} buys)")
            elif bsr_m5 > 0.70 and token.buys_m5 > 10:
                score += 0.03

        # ── Liquidity depth (10%) ─────────────────────────────────────────
        liq = token.liquidity_usd
        if liq > 2_000_000:
            score += 0.10
        elif liq > 500_000:
            score += 0.08
        elif liq > 200_000:
            score += 0.06
        elif liq > 100_000:
            score += 0.04
        elif liq > 50_000:
            score += 0.02

        # ── Age bonus (5%) — early mover advantage ────────────────────────
        age = token.age_hours
        if age is not None:
            if age < 0.5:
                score += 0.08   # Brand new (<30min) — maximum early bonus
                signals.append(f"Brand new (<30min)")
            elif age < 2:
                score += 0.06
                signals.append(f"Very new ({age*60:.0f}min old)")
            elif age < 6:
                score += 0.05
                signals.append(f"New ({age:.0f}h old)")
            elif age < 24:
                score += 0.03
            elif age > 168:
                score -= 0.03

        # ── Holder concentration guard (Birdeye token_overview data) ─────
        # Very few unique holders = concentrated ownership = rug risk regardless
        # of how strong the price momentum looks.
        if token.holder_count > 0:
            if token.holder_count < 50:
                token.score = 0.0
                token.signals = [f"BLOCKED: only {token.holder_count} holders (rug risk)"]
                return 0.0
            elif token.holder_count < 200:
                score -= 0.10
                signals.append(f"Few holders ({token.holder_count})")
            elif token.holder_count > 2000:
                score += 0.03
                signals.append(f"Good distribution ({token.holder_count} holders)")

        # ── Unique wallets (trading breadth) ──────────────────────────────
        # Many unique wallets trading in 24h = organic interest, not 1 whale pumping
        if token.unique_wallets_24h > 500:
            score += 0.03
        elif token.unique_wallets_24h > 0 and token.unique_wallets_24h < 20:
            score -= 0.05   # Thin trading = manipulation risk

        # ── Source bonus ──────────────────────────────────────────────────
        # Pump.fun tokens discovered on the live feed get a freshness bonus
        if token.source == "pumpfun":
            score += 0.04
        elif token.source == "raydium":
            score += 0.02   # Raydium = real liquidity, slight quality bonus

        # ── Solana chain preference ───────────────────────────────────────
        if token.chain_id == "solana":
            score += 0.02

        # ── Safety check (blended in — never skipped for Solana) ──────────
        if token.chain_id == "solana":
            if token.safety_report is None:
                # Not yet checked — do it now (fallback for tokens that slipped
                # through the concurrent pre-check phase)
                try:
                    safety = self._safety_checker.check_token_safety(token.base_address)
                    token.safety_report = safety
                except Exception as e:
                    logger.debug("Safety check error %s: %s", token.base_symbol, e)

            if token.safety_report is not None:
                safety = token.safety_report
                if not safety.is_safe_to_trade:
                    token.score = 0.0
                    token.signals = [f"BLOCKED: {safety.risk_level} risk"] + safety.risk_flags[:2]
                    return 0.0
                w = config.SAFETY_SCORE_WEIGHT
                score = score * (1 - w) + safety.safety_score * w
                if safety.risk_level in ("SAFE", "LOW"):
                    signals.append(f"Safe ({safety.safety_score:.2f})")
                elif safety.risk_level == "MEDIUM":
                    flag = safety.risk_flags[0] if safety.risk_flags else "caution"
                    signals.append(f"MEDIUM risk: {flag}")
                elif safety.risk_level == "HIGH":
                    score *= 0.60   # Heavy penalty for HIGH risk
                    signals.append(f"HIGH risk ({safety.safety_score:.2f})")
            else:
                score *= 0.80   # Penalise unverified tokens

        token.score = float(np.clip(score, 0.0, 1.0))
        token.signals = signals[:5]   # cap at 5 signal tags
        return token.score

    def _run_concurrent_safety_checks(self, tokens: list[DexToken]):
        """Run safety checks for all tokens in parallel, update in-place."""
        sol_tokens = [t for t in tokens
                      if t.chain_id == "solana" and t.safety_report is None]
        if not sol_tokens:
            return

        def _check(t: DexToken):
            try:
                t.safety_report = self._safety_checker.check_token_safety(t.base_address)
            except Exception as e:
                logger.debug("Safety check error %s: %s", t.base_symbol, e)

        futures = [self._executor.submit(_check, t) for t in sol_tokens]
        # Wait with a generous timeout — safety checks can be slow
        for f in concurrent.futures.as_completed(futures, timeout=20):
            try:
                f.result()
            except Exception:
                pass

    # ─── Data Sources ─────────────────────────────────────────────────────────

    def _fetch_boosted_tokens(self) -> list[dict]:
        data  = self._get(f"{DEXSCREENER_BASE}/token-boosts/latest/v1")
        boosted = data if isinstance(data, list) else []
        data2 = self._get(f"{DEXSCREENER_BASE}/token-boosts/top/v1")
        top   = data2 if isinstance(data2, list) else []
        return boosted[:50] + top[:50]

    def _fetch_token_profiles(self) -> list[dict]:
        data = self._get(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
        return data if isinstance(data, list) else []

    def _fetch_pairs_for_tokens(self, token_metas: list[dict]) -> list[DexToken]:
        """Batch-fetch pair data (30 addresses per request)."""
        by_chain: dict[str, list[str]] = {}
        for t in token_metas:
            chain = t.get("chainId", "")
            addr  = t.get("tokenAddress", "")
            if chain in PREFERRED_CHAINS and addr:
                by_chain.setdefault(chain, []).append(addr)

        tokens = []
        for chain, addresses in by_chain.items():
            addr_list = list(dict.fromkeys(addresses))  # preserve order, dedup
            # DexScreener batch: up to 30 comma-separated addresses
            for i in range(0, len(addr_list), 30):
                batch = addr_list[i:i + 30]
                data = self._get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(batch)}")
                if not data or not data.get("pairs"):
                    continue
                by_addr: dict[str, list] = {}
                for p in data["pairs"]:
                    if p.get("chainId") != chain:
                        continue
                    a = p.get("baseToken", {}).get("address", "")
                    by_addr.setdefault(a, []).append(p)
                for addr_pairs in by_addr.values():
                    addr_pairs.sort(
                        key=lambda p: p.get("liquidity", {}).get("usd", 0), reverse=True)
                    t = self._parse_pair(addr_pairs[0])
                    if t:
                        tokens.append(t)
        return tokens

    def _search_top_pairs(self, chain: str, query: str = "SOL",
                          limit: int = 50) -> list[DexToken]:
        data = self._get(f"{DEXSCREENER_BASE}/latest/dex/search", params={"q": query})
        if not data or not data.get("pairs"):
            return []
        chain_pairs = [p for p in data["pairs"] if p.get("chainId") == chain]
        tokens = []
        for p in chain_pairs[:limit]:
            t = self._parse_pair(p)
            if t:
                tokens.append(t)
        return tokens

    def _fetch_pumpfun_tokens(self) -> list[DexToken]:
        """
        Fetch most recently traded pump.fun tokens — highest real-time activity.
        Cross-referenced with DexScreener for full pair data.
        """
        try:
            data = self._get(
                f"{PUMPFUN_BASE}/coins",
                params={"limit": 50, "sort": "last_trade_unix_time",
                        "order": "DESC", "includeNsfw": "false"},
                timeout=8)
            if not isinstance(data, list):
                return []
            mints = [c["mint"] for c in data if c.get("mint")][:30]
            if not mints:
                return []
            result = self._get(
                f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(mints)}")
            if not result or not result.get("pairs"):
                return []
            tokens = []
            seen = set()
            for p in result["pairs"]:
                if p.get("chainId") != "solana":
                    continue
                addr = p.get("pairAddress", "")
                if addr in seen:
                    continue
                seen.add(addr)
                t = self._parse_pair(p)
                if t:
                    t.source = "pumpfun"
                    tokens.append(t)
            logger.debug("Pump.fun active: %d tokens", len(tokens))
            return tokens
        except Exception as e:
            logger.debug("Pump.fun fetch error: %s", e)
            return []

    def _fetch_pumpfun_new(self) -> list[DexToken]:
        """Fetch newest pump.fun launches — these haven't even trended yet."""
        try:
            data = self._get(
                f"{PUMPFUN_BASE}/coins",
                params={"limit": 50, "sort": "created_timestamp",
                        "order": "DESC", "includeNsfw": "false"},
                timeout=8)
            if not isinstance(data, list):
                return []
            mints = [c["mint"] for c in data if c.get("mint")][:30]
            if not mints:
                return []
            result = self._get(
                f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(mints)}")
            if not result or not result.get("pairs"):
                return []
            tokens = []
            seen = set()
            for p in result["pairs"]:
                if p.get("chainId") != "solana":
                    continue
                addr = p.get("pairAddress", "")
                if addr in seen:
                    continue
                seen.add(addr)
                t = self._parse_pair(p)
                if t:
                    t.source = "pumpfun"
                    tokens.append(t)
            return tokens
        except Exception as e:
            logger.debug("Pump.fun new fetch error: %s", e)
            return []

    def _fetch_raydium_pools(self) -> list[DexToken]:
        """
        Fetch top Raydium pools by 24h volume — catches tokens not on DexScreener trending.
        """
        try:
            data = self._get(
                f"{RAYDIUM_BASE}/pools/info/list",
                params={"poolType": "all", "poolSortField": "volume24h",
                        "sortType": "desc", "pageSize": 50, "page": 1},
                timeout=10)
            if not data or not data.get("data", {}).get("data"):
                return []
            pools = data["data"]["data"]
            # Extract base token mint addresses
            mints = []
            for p in pools:
                mint = p.get("mintA", {}).get("address") or p.get("mintB", {}).get("address")
                if mint and mint not in mints:
                    mints.append(mint)
            mints = mints[:30]
            if not mints:
                return []
            result = self._get(
                f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(mints)}")
            if not result or not result.get("pairs"):
                return []
            tokens = []
            seen = set()
            for p in result["pairs"]:
                if p.get("chainId") != "solana" or p.get("dexId") != "raydium":
                    continue
                addr = p.get("pairAddress", "")
                if addr in seen:
                    continue
                seen.add(addr)
                t = self._parse_pair(p)
                if t:
                    t.source = "raydium"
                    tokens.append(t)
            logger.debug("Raydium pools: %d tokens", len(tokens))
            return tokens
        except Exception as e:
            logger.debug("Raydium fetch error: %s", e)
            return []

    def _fetch_birdeye_trending(self) -> list[DexToken]:
        """Birdeye top 50 trending Solana tokens → enriched via DexScreener."""
        be = _get_birdeye()
        if not be or not be.enabled:
            return []
        try:
            trending = be.get_trending_tokens(limit=50, min_liquidity=MIN_LIQUIDITY_USD)
            mints = [t["address"] for t in trending if t.get("address")][:30]
            if not mints:
                return []
            data = self._get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(mints)}")
            if not data or not data.get("pairs"):
                return []
            tokens = []
            seen = set()
            for p in data["pairs"]:
                if p.get("chainId") != "solana":
                    continue
                addr = p.get("pairAddress", "")
                if addr in seen:
                    continue
                seen.add(addr)
                t = self._parse_pair(p)
                if t:
                    t.source = "birdeye"
                    tokens.append(t)
            logger.debug("Birdeye trending: %d tokens", len(tokens))
            return tokens
        except Exception as e:
            logger.debug("Birdeye trending error: %s", e)
            return []

    def _fetch_birdeye_new_listings(self) -> list[DexToken]:
        """Birdeye newest Solana listings → enriched via DexScreener."""
        be = _get_birdeye()
        if not be or not be.enabled:
            return []
        try:
            new_listings = be.get_new_listings(limit=30, min_liquidity=MIN_LIQUIDITY_USD)
            mints = [t["address"] for t in new_listings if t.get("address")][:30]
            if not mints:
                return []
            data = self._get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(mints)}")
            if not data or not data.get("pairs"):
                return []
            tokens = []
            seen = set()
            for p in data["pairs"]:
                if p.get("chainId") != "solana":
                    continue
                addr = p.get("pairAddress", "")
                if addr in seen:
                    continue
                seen.add(addr)
                t = self._parse_pair(p)
                if t:
                    t.source = "birdeye_new"
                    tokens.append(t)
            logger.debug("Birdeye new listings: %d tokens", len(tokens))
            return tokens
        except Exception as e:
            logger.debug("Birdeye new listings error: %s", e)
            return []

    # ─── Parsing ──────────────────────────────────────────────────────────────

    def _parse_pair(self, pair: dict) -> Optional[DexToken]:
        try:
            price_str = pair.get("priceUsd") or "0"
            price = float(price_str) if price_str else 0.0
            if price <= 0:
                return None

            txns   = pair.get("txns", {})
            volume = pair.get("volume", {})
            change = pair.get("priceChange", {})
            liq    = pair.get("liquidity", {})
            txns_m5 = txns.get("m5", {})
            txns_h1 = txns.get("h1", {})
            txns_h24 = txns.get("h24", {})

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
                volume_h1   = float(volume.get("h1") or 0),
                volume_h24  = float(volume.get("h24") or 0),
                volume_m5   = float(volume.get("m5") or 0),
                liquidity_usd = float(liq.get("usd") or 0),
                market_cap    = float(pair.get("marketCap") or pair.get("fdv") or 0),
                buys_h1   = int(txns_h1.get("buys", 0)),
                sells_h1  = int(txns_h1.get("sells", 0)),
                buys_h24  = int(txns_h24.get("buys", 0)),
                sells_h24 = int(txns_h24.get("sells", 0)),
                buys_m5   = int(txns_m5.get("buys", 0)),
                sells_m5  = int(txns_m5.get("sells", 0)),
                pair_created_at = pair.get("pairCreatedAt"),
                url = pair.get("url", ""),
            )
        except Exception as e:
            logger.debug("Parse error: %s", e)
            return None

    # ─── HTTP ─────────────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict = None,
             timeout: int = 10) -> Optional[dict | list]:
        cache_key = f"{url}:{params}"
        now = time.time()
        if cache_key in self._cache:
            val, ts = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return val

        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=timeout,
                                    headers={"User-Agent": "ai-trader/2.0"})
                if resp.status_code == 429:
                    time.sleep(2 ** attempt * 2)
                    continue
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()
                self._cache[cache_key] = (data, now)
                # Evict stale + overflow entries
                if len(self._cache) > self._CACHE_MAX:
                    expired = [k for k, (_, ts) in self._cache.items()
                               if now - ts > self._cache_ttl]
                    for k in expired:
                        del self._cache[k]
                    if len(self._cache) > self._CACHE_MAX:
                        oldest = sorted(self._cache.items(), key=lambda x: x[1][1])
                        for k, _ in oldest[:len(self._cache) - self._CACHE_MAX]:
                            del self._cache[k]
                return data
            except Exception as e:
                logger.debug("Request error [%s]: %s", url[:60], e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None
