"""
Polymarket Edge Strategies
==========================
Nine strategies for detecting mispriced prediction markets.
"""
from __future__ import annotations
import logging
from typing import Optional

from .models import PolyMarket, PolySignal

logger = logging.getLogger(__name__)


class PolymarketStrategies:
    """All edge detection strategies for Polymarket."""

    def __init__(self, probability_engine=None, news_analyzer=None,
                 cross_platform=None, smart_money=None, api_client=None):
        self._probability = probability_engine
        self._news = news_analyzer
        self._cross_platform = cross_platform
        self._smart_money = smart_money
        self._api = api_client

    def scan_all(self, markets: list[PolyMarket],
                 min_edge: float = 0.04) -> list[PolySignal]:
        """Run all strategies across markets and return ranked signals."""
        signals: list[PolySignal] = []

        # Reset LLM call counter
        if self._probability:
            self._probability.reset_cycle_counter()

        # Pre-fetch smart money signals for all markets
        whale_signals = {}
        if self._smart_money:
            try:
                for ws in self._smart_money.get_whale_signals(markets):
                    whale_signals[ws.market_condition_id] = ws
            except Exception as e:
                logger.debug("Smart money scan error: %s", e)

        for mkt in markets:
            # Run each strategy and collect signals
            for strategy_fn, name in [
                (self._strategy_llm_edge, "llm_edge"),
                (self._strategy_news_catalyst, "news_catalyst"),
                (self._strategy_cross_platform_arb, "cross_platform_arb"),
                (self._strategy_sum_to_one_arb, "sum_to_one_arb"),
                (self._strategy_resolution_edge, "resolution_edge"),
                (self._strategy_sentiment_divergence, "sentiment_divergence"),
                (self._strategy_high_spread, "high_spread"),
                (self._strategy_event_correlation, "event_correlation"),
            ]:
                try:
                    sig = strategy_fn(mkt, min_edge)
                    if sig:
                        sig.strategy = name
                        signals.append(sig)
                except Exception as e:
                    logger.debug("Strategy %s error on %s: %s",
                                 name, mkt.condition_id[:8], e)

            # Smart money follow (uses pre-fetched data)
            whale = whale_signals.get(mkt.condition_id)
            if whale:
                try:
                    sig = self._strategy_smart_money_follow(mkt, min_edge, whale)
                    if sig:
                        sig.strategy = "smart_money_follow"
                        signals.append(sig)
                except Exception as e:
                    logger.debug("Smart money strategy error: %s", e)

        # Deduplicate: keep highest-scoring signal per market
        best_per_market: dict[str, PolySignal] = {}
        for sig in signals:
            cid = sig.market.condition_id
            if cid not in best_per_market or sig.score > best_per_market[cid].score:
                best_per_market[cid] = sig

        result = list(best_per_market.values())
        result.sort(key=lambda s: s.score, reverse=True)
        logger.info("Polymarket strategies: %d signals from %d markets",
                     len(result), len(markets))
        return result

    # ─── Individual Strategies ─────────────────────────────────────────────

    def _strategy_llm_edge(self, mkt: PolyMarket,
                           min_edge: float) -> Optional[PolySignal]:
        """LLM-estimated probability vs market price."""
        if not self._probability:
            return None

        estimate = self._probability.estimate_probability(mkt)
        if not estimate or estimate.confidence < 0.6:
            return None

        model_prob = estimate.estimated_prob
        market_prob = mkt.yes_price

        diff = model_prob - market_prob
        if abs(diff) < min_edge:
            return None

        if diff > 0:
            side, target, edge = "YES", market_prob, diff
        else:
            side, target, edge = "NO", mkt.no_price, abs(diff)

        score = min(1.0, 0.3 + abs(diff) * 3 + estimate.confidence * 0.2)

        return PolySignal(
            market=mkt,
            side=side,
            target_price=target,
            edge_pct=edge,
            score=score,
            reasons=[
                f"LLM estimates {model_prob:.1%} vs market {market_prob:.1%}",
                f"Confidence: {estimate.confidence:.0%}",
                estimate.reasoning[:100] if estimate.reasoning else "",
            ],
            model_probability=model_prob,
            confidence=estimate.confidence,
        )

    def _strategy_news_catalyst(self, mkt: PolyMarket,
                                min_edge: float) -> Optional[PolySignal]:
        """Breaking news that hasn't been priced in yet."""
        if not self._news:
            return None

        catalyst = self._news.detect_catalyst(mkt)
        if not catalyst:
            return None

        sentiment = catalyst["sentiment"]
        direction = catalyst["direction"]
        age = catalyst.get("age_minutes", 999)

        # Fresher news = higher score
        recency_boost = max(0, (60 - age) / 60) * 0.2
        edge = abs(sentiment) * 0.08  # Max ~8% edge from news

        if edge < min_edge:
            return None

        target = mkt.yes_price if direction == "YES" else mkt.no_price
        score = min(1.0, 0.35 + abs(sentiment) * 0.3 + recency_boost)

        return PolySignal(
            market=mkt,
            side=direction,
            target_price=target,
            edge_pct=edge,
            score=score,
            reasons=[
                f"News catalyst: {catalyst['headlines'][0][:80]}",
                f"Sentiment: {sentiment:+.2f}, age: {age}min",
            ],
            news_catalyst=catalyst["headlines"][0][:100],
        )

    def _strategy_cross_platform_arb(self, mkt: PolyMarket,
                                     min_edge: float) -> Optional[PolySignal]:
        """Cross-platform price divergence."""
        if not self._cross_platform:
            return None

        consensus = self._cross_platform.get_consensus(mkt)
        if not consensus:
            return None

        diff = consensus.probability - mkt.yes_price
        if abs(diff) < max(min_edge, 0.05):
            return None

        if diff > 0:
            side, target, edge = "YES", mkt.yes_price, diff
        else:
            side, target, edge = "NO", mkt.no_price, abs(diff)

        score = min(1.0, 0.3 + abs(diff) * 2.5)

        return PolySignal(
            market=mkt,
            side=side,
            target_price=target,
            edge_pct=edge,
            score=score,
            reasons=[
                f"Cross-platform arb: {consensus.platform} says {consensus.probability:.1%} "
                f"vs Poly {mkt.yes_price:.1%}",
            ],
            cross_platform_consensus=consensus.probability,
        )

    def _strategy_sum_to_one_arb(self, mkt: PolyMarket,
                                 min_edge: float) -> Optional[PolySignal]:
        """Risk-free arb when YES + NO prices sum to < $0.98."""
        total = mkt.yes_price + mkt.no_price
        if total >= 0.98:
            return None

        edge = 1.0 - total  # Guaranteed profit per $1 of both sides
        if edge < 0.02:
            return None

        # Buy both sides
        return PolySignal(
            market=mkt,
            side="YES",  # Buy both; noted in reasons
            target_price=mkt.yes_price,
            edge_pct=edge,
            score=min(1.0, 0.6 + edge * 5),  # High score for risk-free
            reasons=[
                f"Sum-to-one arb: YES({mkt.yes_price:.2f}) + NO({mkt.no_price:.2f}) = {total:.2f}",
                f"Guaranteed profit: {edge:.1%} per pair",
            ],
        )

    def _strategy_resolution_edge(self, mkt: PolyMarket,
                                  min_edge: float) -> Optional[PolySignal]:
        """Near-certain markets close to resolution."""
        if not mkt.end_date:
            return None

        try:
            from datetime import datetime, timezone
            end = datetime.fromisoformat(mkt.end_date.replace("Z", "+00:00"))
            hours_left = (end - datetime.now(timezone.utc)).total_seconds() / 3600
        except (ValueError, TypeError):
            return None

        if hours_left > 72 or hours_left < 1:
            return None

        # Only markets with high confidence pricing
        if 0.90 <= mkt.yes_price <= 0.98:
            model_prob = 0.96
            edge = model_prob - mkt.yes_price
            if edge < min_edge:
                return None
            return PolySignal(
                market=mkt,
                side="YES",
                target_price=mkt.yes_price,
                edge_pct=edge,
                score=min(1.0, 0.4 + edge * 3),
                reasons=[
                    f"Resolution edge: {hours_left:.0f}h left, YES at {mkt.yes_price:.1%}",
                ],
            )
        elif 0.02 <= mkt.yes_price <= 0.10:
            model_prob = 0.04
            edge = mkt.yes_price - model_prob
            if edge < min_edge:
                return None
            return PolySignal(
                market=mkt,
                side="NO",
                target_price=mkt.no_price,
                edge_pct=edge,
                score=min(1.0, 0.4 + edge * 3),
                reasons=[
                    f"Resolution edge: {hours_left:.0f}h left, YES at {mkt.yes_price:.1%}",
                ],
            )
        return None

    def _strategy_sentiment_divergence(self, mkt: PolyMarket,
                                       min_edge: float) -> Optional[PolySignal]:
        """News sentiment diverges from market price."""
        if not self._news:
            return None

        articles = self._news.get_relevant_news(mkt, limit=5)
        if not articles:
            return None

        headlines = [a.get("title", "") for a in articles]
        sentiment = self._news.score_sentiment(headlines, mkt.question)

        if abs(sentiment) < 0.4:
            return None

        # Sentiment bullish but market bearish (or vice versa)
        market_sentiment = mkt.yes_price - 0.5  # Positive = market thinks YES
        divergence = sentiment - market_sentiment

        if abs(divergence) < 0.3:
            return None

        if divergence > 0:
            side, target = "YES", mkt.yes_price
        else:
            side, target = "NO", mkt.no_price

        edge = min(abs(divergence) * 0.1, 0.10)
        if edge < min_edge:
            return None

        score = min(1.0, 0.25 + abs(divergence) * 0.3)

        return PolySignal(
            market=mkt,
            side=side,
            target_price=target,
            edge_pct=edge,
            score=score,
            reasons=[
                f"Sentiment divergence: news={sentiment:+.2f} vs market={market_sentiment:+.2f}",
            ],
        )

    def _strategy_smart_money_follow(self, mkt: PolyMarket, min_edge: float,
                                     whale=None) -> Optional[PolySignal]:
        """Follow top traders' positions."""
        if not whale:
            return None

        action = whale.action.upper()
        if "YES" in action:
            side, target = "YES", mkt.yes_price
        elif "NO" in action:
            side, target = "NO", mkt.no_price
        else:
            return None

        edge = 0.05  # Assumed edge from following smart money
        score = min(1.0, 0.3 + (whale.size_usdc / 10_000) * 0.2)

        return PolySignal(
            market=mkt,
            side=side,
            target_price=target,
            edge_pct=edge,
            score=score,
            reasons=[
                f"Whale follow: rank #{whale.trader_rank} {whale.action} ${whale.size_usdc:,.0f}",
            ],
        )

    def _strategy_high_spread(self, mkt: PolyMarket,
                              min_edge: float) -> Optional[PolySignal]:
        """Markets with high spread indicate liquidity gaps to exploit."""
        if mkt.spread <= 0.08:
            return None

        if mkt.yes_price < 0.50:
            side, target = "YES", mkt.yes_price
        else:
            side, target = "NO", mkt.no_price

        edge = mkt.spread / 2
        if edge < min_edge:
            return None

        score = 0.3
        reasons = [f"High spread {mkt.spread:.2%}: {side} undervalued"]

        # Volume boost
        if mkt.volume_24h > 100_000:
            score += 0.15
            reasons.append(f"High volume: ${mkt.volume_24h:,.0f}/24h")
        elif mkt.volume_24h > 10_000:
            score += 0.08

        # Reward rate bonus
        rate = mkt.yes_reward_rate if side == "YES" else mkt.no_reward_rate
        if rate > 0:
            score += 0.10
            reasons.append(f"LP rewards: {rate:.2%}")

        return PolySignal(
            market=mkt,
            side=side,
            target_price=target,
            edge_pct=edge,
            score=min(score, 1.0),
            reasons=reasons,
        )

    def _strategy_event_correlation(self, mkt: PolyMarket,
                                    min_edge: float) -> Optional[PolySignal]:
        """Detect inconsistencies between correlated markets in the same event."""
        if not mkt.event_id or not self._api:
            return None

        try:
            related_raw = self._api.get_related_markets(mkt.event_id)
        except Exception:
            return None

        if len(related_raw) < 2:
            return None

        related = []
        for r in related_raw:
            parsed = self._api._parse_market(r)
            if parsed and parsed.condition_id != mkt.condition_id:
                related.append(parsed)

        if not related:
            return None

        # Check if related markets imply inconsistent probabilities
        # e.g., "X wins primary" at 80% but "X wins general" at 20%
        for rel in related:
            # If a more specific event (higher prob) implies a general one should be higher
            if rel.yes_price > mkt.yes_price + 0.15:
                edge = rel.yes_price - mkt.yes_price - 0.10
                if edge >= min_edge:
                    return PolySignal(
                        market=mkt,
                        side="YES",
                        target_price=mkt.yes_price,
                        edge_pct=edge,
                        score=min(1.0, 0.35 + edge * 2),
                        reasons=[
                            f"Event correlation: related market at {rel.yes_price:.1%} "
                            f"implies {mkt.question[:40]} should be higher than {mkt.yes_price:.1%}",
                        ],
                    )

        return None
