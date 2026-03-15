"""
Polymarket Data Models
======================
All dataclasses for the Polymarket trading system.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


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
    # Enhanced fields
    event_id: str = ""
    description: str = ""
    resolution_source: str = ""
    open_interest: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    tick_size: float = 0.01

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
    # Enhanced fields
    strategy: str = ""
    model_probability: float = 0.0
    confidence: float = 0.0
    news_catalyst: str = ""
    cross_platform_consensus: float = 0.0

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
        }


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


@dataclass
class CrossPlatformPrice:
    """Price from an external prediction platform."""
    platform: str                   # "metaculus", "manifold", "kalshi"
    question: str
    probability: float
    volume: float
    url: str
    last_updated: str


@dataclass
class WhaleActivity:
    """Smart money signal from leaderboard tracking."""
    trader_address: str
    trader_rank: int
    action: str                     # "BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"
    market_condition_id: str
    size_usdc: float
    timestamp: str
