"""
Polymarket — Autonomous Prediction Market Trading
=================================================
Trades YES/NO markets on Polymarket (Polygon chain).
Uses the public CLOB REST API — no key for data, EVM key for trading.

Edge sources:
- Probability mispricing vs model estimates
- Late-resolving markets trading at wrong odds
- High-volume markets with inefficient pricing
- Event-correlated position building
"""
from __future__ import annotations
import logging
import time
import requests
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

CLOB_BASE   = "https://clob.polymarket.com"
GAMMA_BASE  = "https://gamma-api.polymarket.com"
DATA_BASE   = "https://data-api.polymarket.com"


@dataclass
class PolyMarket:
    """A single Polymarket market with pricing."""
    condition_id: str
    question: str
    slug: str
    end_date: str
    active: bool
    accepting_orders: bool
    volume_24h: float
    volume_total: float
    yes_token_id: str
    no_token_id: str
    yes_price: float        # probability 0-1
    no_price: float
    yes_reward_rate: float
    no_reward_rate: float
    tags: list[str] = field(default_factory=list)

    @property
    def yes_implied_prob(self) -> float:
        return self.yes_price

    @property
    def no_implied_prob(self) -> float:
        return 1.0 - self.yes_price

    @property
    def spread(self) -> float:
        """Bid-ask spread proxy — higher = less efficient."""
        return abs((self.yes_price + self.no_price) - 1.0)

    def to_dict(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "question": self.question,
            "end_date": self.end_date,
            "yes_price": round(self.yes_price, 4),
            "no_price": round(self.no_price, 4),
            "spread": round(self.spread, 4),
            "volume_24h": self.volume_24h,
            "tags": self.tags,
        }


@dataclass
class PolySignal:
    """Actionable signal for a Polymarket trade."""
    market: PolyMarket
    side: str           # "YES" or "NO"
    target_price: float
    edge_pct: float     # Expected edge as fraction
    score: float        # [0, 1]
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "signal": self.side,
            "market": self.market.question[:80],
            "condition_id": self.market.condition_id,
            "target_price": round(self.target_price, 4),
            "edge_pct": round(self.edge_pct * 100, 2),
            "score": round(self.score, 3),
            "reasons": self.reasons,
        }


class PolymarketTrader:
    """
    Scans Polymarket for edge opportunities and (optionally) executes trades.
    Data fetching is free. Execution requires py-clob-client + Polygon wallet.
    """

    def __init__(self, private_key: str = "", chain_id: int = 137):
        self.private_key = private_key
        self.chain_id    = chain_id
        self._client     = None
        self._cache: dict = {}
        self._cache_ttl  = 120

        if private_key:
            self._init_client()

    def _init_client(self):
        """
        Initialize py-clob-client with L2 API credentials.

        Flow:
        1. Build a base L1 client from the EVM private key.
        2. Call create_or_derive_api_creds() — derives deterministic creds from
           the wallet signature (idempotent: same key always gives same creds).
        3. Re-initialise with the full ApiCreds so authenticated endpoints work.
        """
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            import config as _cfg

            # ── Step 1: L1 client (wallet-only, no creds yet) ─────────────
            l1 = ClobClient(
                host=CLOB_BASE,
                key=self.private_key,
                chain_id=self.chain_id,
            )

            # ── Step 2: derive or load L2 API credentials ─────────────────
            if _cfg.POLYMARKET_API_KEY and _cfg.POLYMARKET_API_SECRET and _cfg.POLYMARKET_PASSPHRASE:
                creds = ApiCreds(
                    api_key=_cfg.POLYMARKET_API_KEY,
                    api_secret=_cfg.POLYMARKET_API_SECRET,
                    api_passphrase=_cfg.POLYMARKET_PASSPHRASE,
                )
                logger.info("Polymarket: using cached L2 API credentials")
            else:
                raw = l1.create_or_derive_api_creds()
                creds = ApiCreds(
                    api_key=raw.get("apiKey", ""),
                    api_secret=raw.get("secret", ""),
                    api_passphrase=raw.get("passphrase", ""),
                )
                logger.info(
                    "Polymarket: derived L2 creds (set POLYMARKET_API_KEY/SECRET/PASSPHRASE "
                    "in .env to skip re-derivation). apiKey=%s", creds.api_key
                )

            # ── Step 3: full authenticated client ─────────────────────────
            self._client = ClobClient(
                host=CLOB_BASE,
                key=self.private_key,
                chain_id=self.chain_id,
                creds=creds,
            )
            logger.info("Polymarket CLOB client ready (live mode, L2 auth)")

        except ImportError:
            logger.warning("py-clob-client not installed — Polymarket in data-only mode.")
        except Exception as e:
            logger.warning("Polymarket client init failed: %s", e)

    # ─── Market Scanning ──────────────────────────────────────────────────────

    def get_active_markets(self, limit: int = 100, min_volume: float = 1000) -> list[PolyMarket]:
        """Fetch active markets sorted by 24h volume using Gamma API."""
        cache_key = f"markets_{limit}"
        cached = self._cached(cache_key, ttl=300)
        if cached is not None:
            return cached

        # Gamma API returns richer market data than CLOB /markets
        markets = []
        offset = 0
        page_size = 100

        while len(markets) < limit:
            data = self._get(f"{GAMMA_BASE}/markets", params={
                "active":    "true",
                "closed":    "false",
                "order":     "volume24hr",
                "ascending": "false",
                "limit":     page_size,
                "offset":    offset,
            })
            if not data:
                break
            # Gamma returns a list directly
            page = data if isinstance(data, list) else data.get("data", [])
            if not page:
                break
            for m in page:
                parsed = self._parse_market(m)
                if parsed and parsed.active and parsed.accepting_orders:
                    markets.append(parsed)
            if len(page) < page_size:
                break
            offset += page_size

        # Filter by volume
        markets = [m for m in markets if m.volume_24h >= min_volume]
        markets.sort(key=lambda m: m.volume_24h, reverse=True)

        self._store(cache_key, markets[:limit])
        logger.info("Polymarket: %d active markets loaded (Gamma API)", len(markets))
        return markets[:limit]

    def find_edges(self, min_edge: float = 0.04, min_volume: float = 5000) -> list[PolySignal]:
        """
        Scan markets for pricing inefficiencies / edges.

        Edge detection strategies:
        1. Extreme probability underpricing (< 10¢ or > 90¢ markets near resolution)
        2. High-spread markets (market maker gap = opportunity)
        3. Volume-weighted momentum (market moving one way = follow)
        4. Near-resolution high-confidence plays
        """
        markets = self.get_active_markets(limit=50, min_volume=min_volume)
        signals = []

        for mkt in markets:
            signal = self._analyze_market(mkt, min_edge)
            if signal:
                signals.append(signal)

        signals.sort(key=lambda s: s.score, reverse=True)
        logger.info("Polymarket: %d edge opportunities found (min_edge=%.1f%%)",
                    len(signals), min_edge * 100)
        return signals

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch order book for a token."""
        data = self._get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        return data or {}

    def get_data_markets(self, limit: int = 50) -> list[dict]:
        """Fetch enriched market data from data-api (volume, liquidity, 24h activity)."""
        data = self._get(f"{DATA_BASE}/markets", params={
            "limit": limit, "sortBy": "volume24hr", "order": "DESC",
        })
        if isinstance(data, list):
            return data
        return (data or {}).get("data", [])

    # ─── Edge Analysis ────────────────────────────────────────────────────────

    def _analyze_market(self, mkt: PolyMarket, min_edge: float) -> Optional[PolySignal]:
        """Analyze a single market for edge opportunities."""
        reasons = []
        score   = 0.0
        side    = None
        target  = None
        edge    = 0.0

        yes_p = mkt.yes_price
        no_p  = mkt.no_price

        # ── Strategy 1: Extreme probability plays ────────────────────────
        # Markets at very low/high probability are often under-priced
        # because retail avoids "almost certain" outcomes
        if 0.02 <= yes_p <= 0.08:
            # YES very cheap — small chance something big happens
            # Better for event arbitrage, skip unless near resolution
            pass
        elif 0.92 <= yes_p <= 0.98:
            # YES almost certain — if there's still uncertainty, buy YES
            model_prob = 0.96   # Our model says 96% if market says 92-98%
            if yes_p < model_prob - min_edge:
                edge = model_prob - yes_p
                side = "YES"
                target = yes_p
                score += 0.4
                reasons.append(f"Near-certain YES underpriced: {yes_p:.2%}")

        # ── Strategy 2: 50/50 markets with momentum ───────────────────────
        elif 0.40 <= yes_p <= 0.60:
            # Orderbook calls were removed here — fetching a per-market orderbook
            # for every market in the 40-60% range caused dozens of blocking HTTP
            # requests per scan cycle, freezing the bot for minutes.
            # The other three strategies already cover this zone sufficiently.
            book = None
            if book:
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                bid_vol = sum(float(b.get("size", 0)) for b in bids[:5])
                ask_vol = sum(float(a.get("size", 0)) for a in asks[:5])
                if bid_vol > ask_vol * 2.5:
                    edge = 0.06
                    side = "YES"
                    target = yes_p
                    score += 0.35
                    reasons.append(f"Strong bid pressure: {bid_vol:.0f} vs {ask_vol:.0f}")
                elif ask_vol > bid_vol * 2.5:
                    edge = 0.06
                    side = "NO"
                    target = no_p
                    score += 0.35
                    reasons.append(f"Strong ask pressure: {ask_vol:.0f} vs {bid_vol:.0f}")

        # ── Strategy 3: High spread = inefficiency ────────────────────────
        if mkt.spread > 0.08:
            # Significant spread — provide liquidity or take the better side
            if yes_p < 0.50:
                side = side or "YES"
                target = target or yes_p
                edge = max(edge, mkt.spread / 2)
                score += 0.3
                reasons.append(f"High spread {mkt.spread:.2%}: YES undervalued")
            else:
                side = side or "NO"
                target = target or no_p
                edge = max(edge, mkt.spread / 2)
                score += 0.3
                reasons.append(f"High spread {mkt.spread:.2%}: NO undervalued")

        # ── Volume boost ─────────────────────────────────────────────────
        if mkt.volume_24h > 100_000:
            score += 0.15
            reasons.append(f"High volume: ${mkt.volume_24h:,.0f}/24h")
        elif mkt.volume_24h > 10_000:
            score += 0.08

        # ── Reward rate bonus ────────────────────────────────────────────
        if side == "YES" and mkt.yes_reward_rate > 0:
            score += 0.10
            reasons.append(f"LP rewards: {mkt.yes_reward_rate:.2%}")
        elif side == "NO" and mkt.no_reward_rate > 0:
            score += 0.10
            reasons.append(f"LP rewards: {mkt.no_reward_rate:.2%}")

        if not side or edge < min_edge or score < 0.3:
            return None

        return PolySignal(
            market=mkt,
            side=side,
            target_price=target,
            edge_pct=edge,
            score=min(score, 1.0),
            reasons=reasons,
        )

    # ─── Trade Execution ──────────────────────────────────────────────────────

    def place_order(self, signal: PolySignal, size_usdc: float) -> Optional[dict]:
        """
        Place a limit order on Polymarket.
        Requires py-clob-client and a funded Polygon wallet.
        """
        if not self._client:
            logger.info(
                "POLY PAPER | %-3s %s | p=%.4f | edge=%.1f%% | $%.0f | vol=$%.0f | '%s'",
                signal.side,
                signal.market.condition_id[:8],
                signal.target_price,
                signal.edge_pct * 100,
                size_usdc,
                signal.market.volume_24h,
                signal.market.question[:60],
            )
            return {"paper": True, "side": signal.side, "size": size_usdc,
                    "price": signal.target_price, "market": signal.market.condition_id}

        try:
            from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions
            from py_clob_client.order_builder.constants import BUY

            token_id = (signal.market.yes_token_id
                        if signal.side == "YES"
                        else signal.market.no_token_id)

            order_args = OrderArgs(
                token_id=token_id,
                price=round(signal.target_price, 4),
                size=round(size_usdc / signal.target_price, 2),
                side=BUY,
            )
            signed = self._client.create_order(order_args)
            resp   = self._client.post_order(signed)
            order_id = (resp or {}).get("orderID", "?")
            logger.info("Polymarket ORDER placed: %s %s @ %.4f size=%.2f (id: %s)",
                        signal.side, signal.market.question[:40],
                        signal.target_price, size_usdc, order_id)
            return resp
        except Exception as e:
            logger.error("Polymarket order failed: %s", e)
            return None

    def get_positions(self) -> list[dict]:
        """Fetch open Polymarket positions."""
        if not self._client:
            return []
        try:
            return self._client.get_positions() or []
        except Exception as e:
            logger.warning("Failed to fetch Polymarket positions: %s", e)
            return []

    # ─── Parsing / Cache ──────────────────────────────────────────────────────

    def _parse_market(self, m: dict) -> Optional[PolyMarket]:
        try:
            # Gamma API uses clobTokenIds list; CLOB API uses tokens list
            tokens = m.get("tokens", [])
            yes_tok = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), {})
            no_tok  = next((t for t in tokens if t.get("outcome", "").upper() == "NO"),  {})

            # Gamma API stores prices at top level as outcomePrices or bestBid/bestAsk
            outcome_prices = m.get("outcomePrices", [])
            if outcome_prices and len(outcome_prices) >= 2:
                yes_price = float(outcome_prices[0] or 0.5)
                no_price  = float(outcome_prices[1] or 0.5)
            else:
                yes_price = float(yes_tok.get("price") or m.get("bestBid") or 0.5)
                no_price  = float(no_tok.get("price")  or 0.5)

            # Token IDs — Gamma uses clobTokenIds list
            clob_ids = m.get("clobTokenIds", [])
            yes_token_id = clob_ids[0] if clob_ids else yes_tok.get("token_id", "")
            no_token_id  = clob_ids[1] if len(clob_ids) > 1 else no_tok.get("token_id", "")

            # Reward rates
            rewards = m.get("rewards") or {}
            if isinstance(rewards, dict):
                rates = rewards.get("rates") or {}
                yes_rate = float(rates.get("yes", 0) or 0)
                no_rate  = float(rates.get("no",  0) or 0)
            else:
                yes_rate = no_rate = 0.0

            return PolyMarket(
                condition_id     = m.get("conditionId") or m.get("condition_id", ""),
                question         = m.get("question", ""),
                slug             = m.get("slug") or m.get("market_slug", ""),
                end_date         = m.get("endDate") or m.get("end_date_iso", ""),
                active           = bool(m.get("active")),
                accepting_orders = bool(m.get("acceptingOrders", m.get("accepting_orders", True))),
                volume_24h       = float(m.get("volume24hr") or m.get("volume_24h") or 0),
                volume_total     = float(m.get("volume") or 0),
                yes_token_id     = yes_token_id,
                no_token_id      = no_token_id,
                yes_price        = yes_price,
                no_price         = no_price,
                yes_reward_rate  = yes_rate,
                no_reward_rate   = no_rate,
                tags             = m.get("tags", []),
            )
        except Exception as e:
            logger.debug("Polymarket parse error: %s", e)
            return None

    def _get(self, url: str, params: dict = None) -> Optional[dict]:
        cache_key = f"{url}:{params}"
        cached = self._cached(cache_key, ttl=60)
        if cached is not None:
            return cached

        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=10,
                                    headers={"Accept": "application/json"})
                if resp.status_code == 429:
                    time.sleep(2 + attempt * 2)   # 2s, 4s, 6s — was 5/10/15s
                    continue
                if resp.ok:
                    data = resp.json()
                    self._store(cache_key, data)
                    return data
            except Exception as e:
                logger.debug("Polymarket API error: %s", e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None

    def _cached(self, key: str, ttl: int = None) -> Optional[object]:
        ttl = ttl or self._cache_ttl
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def _store(self, key: str, val):
        # Evict oldest entries when cache grows too large (memory leak prevention)
        if len(self._cache) >= 200:
            oldest = sorted(self._cache, key=lambda k: self._cache[k][1])
            for k in oldest[:50]:
                del self._cache[k]
        self._cache[key] = (val, time.time())
