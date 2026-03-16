"""
Polymarket Data Models
======================
All dataclasses for the Polymarket trading system.

Production-grade models with validation, serialization, and derived metrics
for real-time trading decisions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string to timezone-aware datetime.

    Handles common variants: trailing 'Z', with/without microseconds,
    with/without timezone offset.  Returns ``None`` on any parse failure
    so callers never crash on bad data.
    """
    if not ts:
        return None
    try:
        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# PolyMarket
# ---------------------------------------------------------------------------

@dataclass
class PolyMarket:
    """A single Polymarket market with pricing.

    All prices are clamped to [0, 1] on construction.  ``condition_id``
    must be non-empty.
    """
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
    # Enhanced fields
    event_id: str = ""
    description: str = ""
    resolution_source: str = ""
    open_interest: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    tick_size: float = 0.01

    # -- validation ----------------------------------------------------------

    def __post_init__(self):
        # Clamp prices to [0, 1]
        self.yes_price = max(0.0, min(1.0, float(self.yes_price)))
        self.no_price = max(0.0, min(1.0, float(self.no_price)))
        self.best_bid = max(0.0, min(1.0, float(self.best_bid)))
        self.best_ask = max(0.0, min(1.0, float(self.best_ask)))
        # Ensure condition_id is non-empty
        if not self.condition_id:
            raise ValueError("condition_id must be non-empty")
        # Coerce numerics
        self.volume_24h = float(self.volume_24h)
        self.volume_total = float(self.volume_total)
        self.open_interest = float(self.open_interest)
        self.yes_reward_rate = float(self.yes_reward_rate)
        self.no_reward_rate = float(self.no_reward_rate)
        self.tick_size = float(self.tick_size) if self.tick_size else 0.01

    # -- properties ----------------------------------------------------------

    @property
    def yes_implied_prob(self) -> float:
        return self.yes_price

    @property
    def no_implied_prob(self) -> float:
        return 1.0 - self.yes_price

    @property
    def spread(self) -> float:
        """Bid-ask spread proxy -- higher = less efficient."""
        return abs((self.yes_price + self.no_price) - 1.0)

    @property
    def mid_price(self) -> float:
        """Mid-point price.

        If yes_price + no_price is close to 1.0 (within 5 cents), use the
        average.  Otherwise fall back to yes_price as the best single-sided
        estimate.
        """
        total = self.yes_price + self.no_price
        if abs(total - 1.0) < 0.05:
            return (self.yes_price + (1.0 - self.no_price)) / 2.0
        return self.yes_price

    @property
    def implied_vig(self) -> float:
        """Market-maker vig: how much over-round exists.

        A perfectly efficient book has yes + no = 1.0 => vig = 0.
        """
        return self.yes_price + self.no_price - 1.0

    @property
    def is_liquid(self) -> bool:
        """Quick liquidity check: volume_24h > $10k and spread < 10 cents."""
        return self.volume_24h > 10_000 and self.spread < 0.10

    @property
    def time_to_resolution_hours(self) -> Optional[float]:
        """Hours until market end_date.  ``None`` if end_date is unparseable."""
        dt = _parse_iso(self.end_date)
        if dt is None:
            return None
        delta = dt - _utcnow()
        return max(0.0, delta.total_seconds() / 3600.0)

    @property
    def liquidity_score(self) -> float:
        """Combined 0-1 liquidity score from volume, spread, and open interest.

        Components (equal weight):
          * Volume score:  sigmoid-like mapping of volume_24h ($0 -> 0, $100k -> ~1)
          * Spread score:  1 - spread (tighter is better)
          * OI score:      sigmoid-like mapping of open_interest ($0 -> 0, $500k -> ~1)
        """
        # Volume component: 1 - exp(-vol / 50_000)
        vol_score = 1.0 - math.exp(-self.volume_24h / 50_000.0) if self.volume_24h >= 0 else 0.0
        # Spread component: 1 - spread, floored at 0
        spread_score = max(0.0, 1.0 - self.spread)
        # Open interest component
        oi_score = 1.0 - math.exp(-self.open_interest / 250_000.0) if self.open_interest >= 0 else 0.0
        return max(0.0, min(1.0, (vol_score + spread_score + oi_score) / 3.0))

    # -- methods -------------------------------------------------------------

    def is_near_resolution(self, hours: int = 72) -> bool:
        """True if the market resolves within *hours* from now."""
        ttr = self.time_to_resolution_hours
        if ttr is None:
            return False
        return ttr <= hours

    def to_dict(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "question": self.question,
            "slug": self.slug,
            "end_date": self.end_date,
            "active": self.active,
            "accepting_orders": self.accepting_orders,
            "yes_price": round(self.yes_price, 4),
            "no_price": round(self.no_price, 4),
            "spread": round(self.spread, 4),
            "mid_price": round(self.mid_price, 4),
            "implied_vig": round(self.implied_vig, 4),
            "volume_24h": self.volume_24h,
            "volume_total": self.volume_total,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "yes_reward_rate": self.yes_reward_rate,
            "no_reward_rate": self.no_reward_rate,
            "tags": self.tags,
            "event_id": self.event_id,
            "description": self.description,
            "resolution_source": self.resolution_source,
            "open_interest": self.open_interest,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "tick_size": self.tick_size,
            "is_liquid": self.is_liquid,
            "liquidity_score": round(self.liquidity_score, 4),
            "time_to_resolution_hours": self.time_to_resolution_hours,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PolyMarket:
        """Reconstruct a PolyMarket from a ``to_dict()`` output or similar mapping."""
        return cls(
            condition_id=d.get("condition_id", ""),
            question=d.get("question", ""),
            slug=d.get("slug", ""),
            end_date=d.get("end_date", ""),
            active=d.get("active", True),
            accepting_orders=d.get("accepting_orders", True),
            volume_24h=float(d.get("volume_24h", 0)),
            volume_total=float(d.get("volume_total", 0)),
            yes_token_id=d.get("yes_token_id", ""),
            no_token_id=d.get("no_token_id", ""),
            yes_price=float(d.get("yes_price", 0.5)),
            no_price=float(d.get("no_price", 0.5)),
            yes_reward_rate=float(d.get("yes_reward_rate", 0)),
            no_reward_rate=float(d.get("no_reward_rate", 0)),
            tags=d.get("tags", []),
            event_id=d.get("event_id", ""),
            description=d.get("description", ""),
            resolution_source=d.get("resolution_source", ""),
            open_interest=float(d.get("open_interest", 0)),
            best_bid=float(d.get("best_bid", 0)),
            best_ask=float(d.get("best_ask", 0)),
            tick_size=float(d.get("tick_size", 0.01)),
        )


# ---------------------------------------------------------------------------
# PolySignal
# ---------------------------------------------------------------------------

@dataclass
class PolySignal:
    """Actionable signal for a Polymarket trade.

    Validated on construction: ``score`` and ``edge_pct`` are clamped to
    sensible ranges, ``side`` must be YES or NO.
    """
    market: PolyMarket
    side: str           # "YES" or "NO"
    target_price: float
    edge_pct: float     # Expected edge as fraction (e.g. 0.05 = 5%)
    score: float        # [0, 1]
    reasons: list[str] = field(default_factory=list)
    # Enhanced fields
    strategy: str = ""
    model_probability: float = 0.0
    confidence: float = 0.0
    news_catalyst: str = ""
    cross_platform_consensus: float = 0.0

    def __post_init__(self):
        self.side = self.side.upper()
        if self.side not in ("YES", "NO"):
            raise ValueError(f"side must be 'YES' or 'NO', got '{self.side}'")
        self.score = max(0.0, min(1.0, float(self.score)))
        self.target_price = max(0.0, min(1.0, float(self.target_price)))
        self.edge_pct = float(self.edge_pct)
        self.model_probability = max(0.0, min(1.0, float(self.model_probability)))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    # -- properties ----------------------------------------------------------

    @property
    def expected_value(self) -> float:
        """Quick EV metric: edge * score."""
        return self.edge_pct * self.score

    @property
    def kelly_fraction(self) -> float:
        """Kelly criterion fraction for optimal bet sizing, clamped to [0, 0.25].

        Kelly = edge / odds, where odds = (1 / target_price) - 1.
        Returns 0 if target_price is at the boundary (0 or 1).
        """
        if self.target_price <= 0.0 or self.target_price >= 1.0:
            return 0.0
        odds = (1.0 / self.target_price) - 1.0
        if odds <= 0.0:
            return 0.0
        raw = self.edge_pct / odds
        return max(0.0, min(0.25, raw))

    @property
    def risk_reward_ratio(self) -> float:
        """Ratio of potential reward to potential risk.

        Reward = target_price - market price; risk = market price (loss to 0).
        Uses yes_price or no_price depending on side.
        """
        market_price = (
            self.market.yes_price if self.side == "YES" else self.market.no_price
        )
        if market_price <= 0.0:
            return 0.0
        reward = self.target_price - market_price
        risk = market_price  # worst case: price goes to 0
        if risk <= 0.0:
            return 0.0
        return reward / risk

    # -- methods -------------------------------------------------------------

    def is_actionable(self, min_score: float = 0.3, min_edge: float = 0.04) -> bool:
        """True if signal meets minimum quality thresholds."""
        return self.score >= min_score and self.edge_pct >= min_edge

    def to_dict(self) -> dict:
        return {
            "signal": self.side,
            "market": self.market.question[:80],
            "condition_id": self.market.condition_id,
            "target_price": round(self.target_price, 4),
            "edge_pct": round(self.edge_pct * 100, 2),
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "strategy": self.strategy,
            "model_probability": round(self.model_probability, 4),
            "confidence": round(self.confidence, 4),
            "news_catalyst": self.news_catalyst,
            "cross_platform_consensus": round(self.cross_platform_consensus, 4),
            "expected_value": round(self.expected_value, 4),
            "kelly_fraction": round(self.kelly_fraction, 4),
            "risk_reward_ratio": round(self.risk_reward_ratio, 4),
        }

    @classmethod
    def from_dict(cls, d: dict, market: PolyMarket) -> PolySignal:
        """Reconstruct from ``to_dict()`` output plus the parent market."""
        return cls(
            market=market,
            side=d.get("signal", "YES"),
            target_price=float(d.get("target_price", 0.5)),
            edge_pct=float(d.get("edge_pct", 0)) / 100.0,  # stored as percentage
            score=float(d.get("score", 0)),
            reasons=d.get("reasons", []),
            strategy=d.get("strategy", ""),
            model_probability=float(d.get("model_probability", 0)),
            confidence=float(d.get("confidence", 0)),
            news_catalyst=d.get("news_catalyst", ""),
            cross_platform_consensus=float(d.get("cross_platform_consensus", 0)),
        )


# ---------------------------------------------------------------------------
# PolyPosition
# ---------------------------------------------------------------------------

@dataclass
class PolyPosition:
    """Tracked Polymarket position with P&L."""
    condition_id: str
    token_id: str
    side: str                       # "YES" or "NO"
    entry_price: float
    current_price: float
    size_usdc: float
    shares: float
    opened_at: str
    market_question: str
    end_date: str
    strategy: str
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    partial_exits: list = field(default_factory=list)

    # -- properties ----------------------------------------------------------

    @property
    def holding_hours(self) -> Optional[float]:
        """Hours since position was opened.  ``None`` if ``opened_at`` cannot be parsed."""
        dt = _parse_iso(self.opened_at)
        if dt is None:
            return None
        delta = _utcnow() - dt
        return max(0.0, delta.total_seconds() / 3600.0)

    # -- methods -------------------------------------------------------------

    def is_stale(self, max_hours: int = 48) -> bool:
        """True if position has been held longer than *max_hours*."""
        hh = self.holding_hours
        if hh is None:
            return False
        return hh > max_hours

    def update_pnl(self, new_price: float) -> None:
        """Recalculate unrealised P&L from a fresh price."""
        new_price = max(0.0, min(1.0, float(new_price)))
        self.current_price = new_price
        price_delta = new_price - self.entry_price
        self.unrealized_pnl = price_delta * self.shares
        if self.entry_price > 0:
            self.unrealized_pnl_pct = price_delta / self.entry_price
        else:
            self.unrealized_pnl_pct = 0.0

    def should_exit(self, stop_pct: float = 0.15, tp_mult: float = 2.5) -> bool:
        """True if position hits stop-loss or take-profit threshold.

        Parameters
        ----------
        stop_pct:
            Maximum tolerable loss as a fraction of entry price.
        tp_mult:
            Take-profit expressed as a multiple of the initial edge
            (approx. ``entry_price`` distance from 0.5).
        """
        if self.entry_price <= 0:
            return False
        # Stop loss check
        loss_pct = (self.entry_price - self.current_price) / self.entry_price
        if loss_pct >= stop_pct:
            return True
        # Take profit check: price moved in favorable direction by tp_mult * edge
        edge = abs(self.entry_price - 0.5)
        target_gain = edge * tp_mult
        if self.current_price >= self.entry_price + target_gain:
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "side": self.side,
            "entry_price": round(self.entry_price, 4),
            "current_price": round(self.current_price, 4),
            "size_usdc": round(self.size_usdc, 2),
            "shares": round(self.shares, 4),
            "opened_at": self.opened_at,
            "market_question": self.market_question,
            "end_date": self.end_date,
            "strategy": self.strategy,
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 4),
            "stop_loss_price": round(self.stop_loss_price, 4),
            "take_profit_price": round(self.take_profit_price, 4),
            "partial_exits": self.partial_exits,
            "holding_hours": self.holding_hours,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PolyPosition:
        """Reconstruct from ``to_dict()`` output."""
        return cls(
            condition_id=d.get("condition_id", ""),
            token_id=d.get("token_id", ""),
            side=d.get("side", "YES"),
            entry_price=float(d.get("entry_price", 0)),
            current_price=float(d.get("current_price", 0)),
            size_usdc=float(d.get("size_usdc", 0)),
            shares=float(d.get("shares", 0)),
            opened_at=d.get("opened_at", ""),
            market_question=d.get("market_question", ""),
            end_date=d.get("end_date", ""),
            strategy=d.get("strategy", ""),
            unrealized_pnl=float(d.get("unrealized_pnl", 0)),
            unrealized_pnl_pct=float(d.get("unrealized_pnl_pct", 0)),
            stop_loss_price=float(d.get("stop_loss_price", 0)),
            take_profit_price=float(d.get("take_profit_price", 0)),
            partial_exits=d.get("partial_exits", []),
        )


# ---------------------------------------------------------------------------
# ProbabilityEstimate
# ---------------------------------------------------------------------------

@dataclass
class ProbabilityEstimate:
    """Output from the probability engine."""
    market_question: str
    estimated_prob: float           # 0.0 - 1.0
    confidence: float               # 0.0 - 1.0
    reasoning: str
    sources_used: list[str] = field(default_factory=list)
    timestamp: str = ""
    model_used: str = ""

    # -- properties ----------------------------------------------------------

    @property
    def age_seconds(self) -> Optional[float]:
        """Seconds since this estimate was produced.  ``None`` if timestamp is empty."""
        dt = _parse_iso(self.timestamp)
        if dt is None:
            return None
        delta = _utcnow() - dt
        return max(0.0, delta.total_seconds())

    # -- methods -------------------------------------------------------------

    def edge_vs_market(self, market_price: float) -> float:
        """Signed edge: positive means our estimate is higher than market.

        Returns ``estimated_prob - market_price``.
        """
        return self.estimated_prob - market_price

    def is_high_confidence(self, threshold: float = 0.7) -> bool:
        """True if confidence exceeds *threshold*."""
        return self.confidence >= threshold


# ---------------------------------------------------------------------------
# CrossPlatformPrice (unchanged)
# ---------------------------------------------------------------------------

@dataclass
class CrossPlatformPrice:
    """Price from an external prediction platform."""
    platform: str                   # "metaculus", "manifold", "kalshi"
    question: str
    probability: float
    volume: float
    url: str
    last_updated: str


# ---------------------------------------------------------------------------
# WhaleActivity (unchanged)
# ---------------------------------------------------------------------------

@dataclass
class WhaleActivity:
    """Smart money signal from leaderboard tracking."""
    trader_address: str
    trader_rank: int
    action: str                     # "BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"
    market_condition_id: str
    size_usdc: float
    timestamp: str


# ---------------------------------------------------------------------------
# OrderBookSnapshot (new)
# ---------------------------------------------------------------------------

@dataclass
class OrderBookSnapshot:
    """Point-in-time orderbook state for a single token."""
    token_id: str
    timestamp: str
    bids: list[tuple[float, float]]  # (price, size) pairs, best first
    asks: list[tuple[float, float]]  # (price, size) pairs, best first
    mid_price: float
    spread: float
    bid_depth_10pct: float   # Total bid size within 10% of mid
    ask_depth_10pct: float   # Total ask size within 10% of mid
    imbalance: float         # (bid_depth - ask_depth) / (bid_depth + ask_depth)


# ---------------------------------------------------------------------------
# TradeRecord (new)
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    """Completed trade for P&L tracking and post-mortem analysis."""
    condition_id: str
    market_question: str
    side: str
    strategy: str
    entry_price: float
    exit_price: float
    size_usdc: float
    shares: float
    pnl_usd: float
    pnl_pct: float
    hold_hours: float
    opened_at: str
    closed_at: str
    exit_reason: str

    def to_dict(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "market_question": self.market_question,
            "side": self.side,
            "strategy": self.strategy,
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "size_usdc": round(self.size_usdc, 2),
            "shares": round(self.shares, 4),
            "pnl_usd": round(self.pnl_usd, 4),
            "pnl_pct": round(self.pnl_pct, 4),
            "hold_hours": round(self.hold_hours, 2),
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "exit_reason": self.exit_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TradeRecord:
        return cls(
            condition_id=d.get("condition_id", ""),
            market_question=d.get("market_question", ""),
            side=d.get("side", ""),
            strategy=d.get("strategy", ""),
            entry_price=float(d.get("entry_price", 0)),
            exit_price=float(d.get("exit_price", 0)),
            size_usdc=float(d.get("size_usdc", 0)),
            shares=float(d.get("shares", 0)),
            pnl_usd=float(d.get("pnl_usd", 0)),
            pnl_pct=float(d.get("pnl_pct", 0)),
            hold_hours=float(d.get("hold_hours", 0)),
            opened_at=d.get("opened_at", ""),
            closed_at=d.get("closed_at", ""),
            exit_reason=d.get("exit_reason", ""),
        )


# ---------------------------------------------------------------------------
# EngineMetrics (new)
# ---------------------------------------------------------------------------

@dataclass
class EngineMetrics:
    """Runtime metrics for the Polymarket engine.

    All counters start at zero and are incremented by the engine loop.
    Thread-safe updates should be done by the caller (e.g. via a lock).
    """
    scan_count: int = 0
    signals_generated: int = 0
    trades_executed: int = 0
    trades_closed: int = 0
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    llm_calls: int = 0
    api_calls: int = 0
    api_errors: int = 0
    ws_reconnects: int = 0
    last_scan_time: str = ""
    last_scan_duration_ms: float = 0.0
    uptime_hours: float = 0.0

    @property
    def win_rate(self) -> float:
        """Win rate as a fraction [0, 1].  Returns 0 if no trades closed."""
        total = self.win_count + self.loss_count
        if total == 0:
            return 0.0
        return self.win_count / total

    @property
    def avg_pnl_per_trade(self) -> float:
        """Average P&L per closed trade."""
        if self.trades_closed == 0:
            return 0.0
        return self.total_pnl / self.trades_closed

    @property
    def api_error_rate(self) -> float:
        """Fraction of API calls that errored."""
        if self.api_calls == 0:
            return 0.0
        return self.api_errors / self.api_calls

    def to_dict(self) -> dict:
        return {
            "scan_count": self.scan_count,
            "signals_generated": self.signals_generated,
            "trades_executed": self.trades_executed,
            "trades_closed": self.trades_closed,
            "total_pnl": round(self.total_pnl, 4),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "avg_pnl_per_trade": round(self.avg_pnl_per_trade, 4),
            "llm_calls": self.llm_calls,
            "api_calls": self.api_calls,
            "api_errors": self.api_errors,
            "api_error_rate": round(self.api_error_rate, 4),
            "ws_reconnects": self.ws_reconnects,
            "last_scan_time": self.last_scan_time,
            "last_scan_duration_ms": round(self.last_scan_duration_ms, 2),
            "uptime_hours": round(self.uptime_hours, 2),
        }
