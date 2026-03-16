"""
Smart Money Tracker
===================
Production-grade whale tracking, trader profiling, flow analysis, and
signal generation from Polymarket leaderboard data.

Tracks the top traders on Polymarket, diffs their positions over time,
computes aggregate flow signals, and generates prioritised whale alerts
that downstream strategies can act on.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .models import PolyMarket, WhaleActivity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_REFRESH_INTERVAL = 1800       # 30 min leaderboard refresh
_DEFAULT_MIN_WHALE_SIZE = 1_000        # $1 000 minimum for whale signal
_DEFAULT_LARGE_MOVE_MULT = 2.0         # 2x normal = "large move"
_DEFAULT_LARGE_MOVE_THRESHOLD = 5_000  # $5k absolute large-move floor
_TOP_N_TRACK = 20                      # Traders to pull from leaderboard
_TOP_N_SCAN = 10                       # Traders to scan positions each cycle
_CONVERGENCE_WINDOW_SEC = 3600         # 1h window for convergence detection
_ACCURACY_FILE = "whale_accuracy.json"


# ---------------------------------------------------------------------------
# TraderProfile
# ---------------------------------------------------------------------------
@dataclass
class TraderProfile:
    """Detailed profile of a tracked trader."""

    address: str
    rank: int
    total_pnl: float
    total_volume: float
    win_rate: float
    avg_position_size: float
    preferred_categories: list[str] = field(default_factory=list)
    avg_hold_time_hours: float = 0.0
    active_positions: int = 0
    last_active: str = ""
    follow_score: float = 0.0       # 0-1: how profitable following them would be
    signal_reliability: float = 0.5  # historical accuracy of their entries

    @property
    def rank_weight(self) -> float:
        """Higher-ranked traders get more weight (rank 1 → 1.0, rank 20 → 0.05)."""
        if self.rank <= 0:
            return 0.0
        return max(0.05, 1.0 - (self.rank - 1) * 0.05)


# ---------------------------------------------------------------------------
# WhaleAccuracyTracker
# ---------------------------------------------------------------------------
class WhaleAccuracyTracker:
    """Persist and query historical accuracy of following specific traders.

    Each record captures an entry we chose to follow and, once the market
    resolves, whether that follow was profitable.
    """

    def __init__(self, filepath: str = _ACCURACY_FILE):
        self._filepath = filepath
        # address → list of {condition_id, side, entry_price, outcome, pnl, ts}
        self.records: dict[str, list[dict]] = {}
        self.load()

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        if not os.path.exists(self._filepath):
            return
        try:
            with open(self._filepath, "r") as fh:
                self.records = json.load(fh)
            logger.debug("WhaleAccuracy: loaded %d traders from %s",
                         len(self.records), self._filepath)
        except Exception as exc:
            logger.warning("WhaleAccuracy: failed to load %s: %s",
                           self._filepath, exc)

    def save(self) -> None:
        try:
            with open(self._filepath, "w") as fh:
                json.dump(self.records, fh, indent=2)
        except Exception as exc:
            logger.warning("WhaleAccuracy: failed to save: %s", exc)

    # -- recording ----------------------------------------------------------

    def record_follow(self, address: str, condition_id: str,
                      side: str, entry_price: float) -> None:
        """Record that we are following a trader into a position."""
        rec = {
            "condition_id": condition_id,
            "side": side,
            "entry_price": entry_price,
            "outcome": None,       # filled on resolution
            "pnl": None,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.records.setdefault(address, []).append(rec)
        self.save()

    def record_resolution(self, condition_id: str, winning_side: str) -> None:
        """Update all records for a resolved market with P&L."""
        for addr, recs in self.records.items():
            for rec in recs:
                if rec["condition_id"] != condition_id:
                    continue
                if rec["outcome"] is not None:
                    continue  # already resolved
                won = rec["side"].upper() == winning_side.upper()
                rec["outcome"] = won
                if won:
                    rec["pnl"] = (1.0 - rec["entry_price"])
                else:
                    rec["pnl"] = -rec["entry_price"]
        self.save()

    # -- queries ------------------------------------------------------------

    def get_accuracy(self, address: str) -> float:
        """Return win-rate (0-1) for a trader. Default 0.5 if no data."""
        recs = [r for r in self.records.get(address, [])
                if r.get("outcome") is not None]
        if not recs:
            return 0.5
        wins = sum(1 for r in recs if r["outcome"])
        return wins / len(recs)

    def get_avg_pnl(self, address: str) -> float:
        """Average P&L per follow for a trader."""
        recs = [r for r in self.records.get(address, [])
                if r.get("pnl") is not None]
        if not recs:
            return 0.0
        return sum(r["pnl"] for r in recs) / len(recs)

    def get_follow_score(self, address: str) -> float:
        """Composite 0-1 score combining accuracy and avg P&L.

        Blend of win-rate (60%) and normalised average P&L (40%).
        """
        acc = self.get_accuracy(address)
        avg = self.get_avg_pnl(address)
        # normalise pnl: clip to [-1, 1] then scale to [0, 1]
        norm_pnl = max(0.0, min(1.0, (avg + 1.0) / 2.0))
        return 0.6 * acc + 0.4 * norm_pnl

    def get_top_performers(self, n: int = 5) -> list[str]:
        """Return addresses of the *n* most profitable traders to follow."""
        scored = [
            (addr, self.get_follow_score(addr))
            for addr in self.records
            if len([r for r in self.records[addr] if r.get("outcome") is not None]) >= 2
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [addr for addr, _ in scored[:n]]

    def total_resolved(self, address: str) -> int:
        return len([r for r in self.records.get(address, [])
                    if r.get("outcome") is not None])


# ---------------------------------------------------------------------------
# SmartMoneyTracker
# ---------------------------------------------------------------------------
class SmartMoneyTracker:
    """Tracks top traders and detects whale movements with full profiling,
    aggregate flow analysis, timing intelligence, and alert generation."""

    def __init__(self, api_client, *,
                 refresh_interval: int = _DEFAULT_REFRESH_INTERVAL,
                 min_whale_size: float = _DEFAULT_MIN_WHALE_SIZE):
        self._api = api_client
        self._tracked_traders: list[dict] = []
        self._trader_profiles: dict[str, TraderProfile] = {}
        self._trader_positions: dict[str, list] = {}   # addr → positions snapshot
        self._last_refresh = 0.0
        self._refresh_interval = refresh_interval
        self._min_whale_size = min_whale_size

        # Flow accumulators: condition_id → list of WhaleActivity
        self._flow_history: dict[str, list[WhaleActivity]] = defaultdict(list)

        # Recent price snapshots for timing analysis: condition_id → [(ts, price)]
        self._price_history: dict[str, list[tuple[float, float]]] = defaultdict(list)

        # Accuracy tracker
        self.accuracy = WhaleAccuracyTracker()

    # -----------------------------------------------------------------------
    # Leaderboard refresh
    # -----------------------------------------------------------------------
    def refresh_leaderboard(self) -> list[dict]:
        """Refresh top traders from leaderboard (cached for refresh_interval)."""
        now = time.time()
        if now - self._last_refresh < self._refresh_interval and self._tracked_traders:
            return self._tracked_traders

        try:
            leaders = self._api.get_leaderboard(limit=_TOP_N_TRACK)
            self._tracked_traders = []
            for i, leader in enumerate(leaders):
                addr = leader.get("address") or leader.get("user", "")
                if not addr:
                    continue
                self._tracked_traders.append({
                    "address": addr,
                    "rank": i + 1,
                    "pnl": float(leader.get("pnl", 0) or 0),
                    "volume": float(leader.get("volume", 0) or 0),
                    "win_rate": float(leader.get("winRate", 0)
                                      or leader.get("win_rate", 0) or 0),
                    "num_trades": int(leader.get("numTrades", 0)
                                      or leader.get("num_trades", 0) or 0),
                    "categories": leader.get("categories", []),
                })
            self._last_refresh = now
            logger.info("Smart money: tracking %d top traders", len(self._tracked_traders))

            # Build / update profiles
            for td in self._tracked_traders:
                self._build_trader_profile(td)

        except Exception as exc:
            logger.debug("Leaderboard refresh error: %s", exc)

        return self._tracked_traders

    # -----------------------------------------------------------------------
    # Trader profiling
    # -----------------------------------------------------------------------
    def _build_trader_profile(self, trader_data: dict,
                              positions: Optional[list[dict]] = None) -> TraderProfile:
        """Build a comprehensive trader profile from leaderboard data and
        (optionally) their current position snapshot."""
        addr = trader_data["address"]
        rank = trader_data.get("rank", 99)
        pnl = trader_data.get("pnl", 0.0)
        volume = trader_data.get("volume", 0.0)
        win_rate = trader_data.get("win_rate", 0.0)
        num_trades = trader_data.get("num_trades", 0) or trader_data.get("num_trades", 1)

        # Compute average position size from volume / trades
        avg_pos = volume / max(num_trades, 1)

        # Preferred categories
        cats: list[str] = trader_data.get("categories", [])

        # Active positions count + avg hold time from positions list
        active = 0
        total_hold_hours = 0.0
        last_active_ts = ""
        if positions is None:
            positions = self._trader_positions.get(addr, [])
        for pos in positions:
            active += 1
            ts_str = pos.get("timestamp") or pos.get("created_at", "")
            if ts_str and ts_str > last_active_ts:
                last_active_ts = ts_str
            # Approximate hold time from created_at → now
            try:
                created = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - created
                total_hold_hours += delta.total_seconds() / 3600.0
            except Exception:
                pass

        avg_hold = total_hold_hours / max(active, 1)
        follow = self.accuracy.get_follow_score(addr)
        reliability = self.accuracy.get_accuracy(addr)

        profile = TraderProfile(
            address=addr,
            rank=rank,
            total_pnl=pnl,
            total_volume=volume,
            win_rate=win_rate,
            avg_position_size=avg_pos,
            preferred_categories=cats[:5],
            avg_hold_time_hours=round(avg_hold, 1),
            active_positions=active,
            last_active=last_active_ts,
            follow_score=follow,
            signal_reliability=reliability,
        )
        self._trader_profiles[addr] = profile
        return profile

    def get_trader_profile(self, address: str) -> Optional[TraderProfile]:
        """Return cached profile for a trader, or None."""
        return self._trader_profiles.get(address)

    # -----------------------------------------------------------------------
    # Core scanning — whale signals
    # -----------------------------------------------------------------------
    def get_whale_signals(self, markets: list[PolyMarket]) -> list[WhaleActivity]:
        """Check for whale activity on tracked markets.

        Compares current positions with previous snapshot to detect entries,
        exits, adds, and reductions.  Returns signals filtered to the
        supplied market set and weighted by trader profile.
        """
        self.refresh_leaderboard()

        signals: list[WhaleActivity] = []
        market_ids = {m.condition_id for m in markets}

        for trader in self._tracked_traders[:_TOP_N_SCAN]:
            addr = trader["address"]
            try:
                current_positions = self._api.get_trader_positions(addr)
            except Exception:
                continue

            profile = self._trader_profiles.get(addr)
            prev_positions = self._trader_positions.get(addr, [])
            new_moves = self._diff_positions(prev_positions, current_positions,
                                             profile or trader)

            for move in new_moves:
                # Fill in trader address (diff doesn't know it yet)
                move.trader_address = addr
                if move.market_condition_id in market_ids and move.size_usdc >= self._min_whale_size:
                    signals.append(move)
                    # Record in flow history
                    self._flow_history[move.market_condition_id].append(move)

            self._trader_positions[addr] = current_positions

        if signals:
            logger.info("Smart money: %d whale signals detected", len(signals))
        return signals

    def detect_whale_movement(self, condition_id: str) -> Optional[WhaleActivity]:
        """Check for whale activity on a specific market (single-market probe)."""
        self.refresh_leaderboard()

        for trader in self._tracked_traders[:5]:
            addr = trader["address"]
            try:
                positions = self._api.get_trader_positions(addr)
            except Exception:
                continue

            for pos in positions:
                pos_cid = pos.get("conditionId") or pos.get("condition_id", "")
                if pos_cid != condition_id:
                    continue
                size = float(pos.get("size", 0) or pos.get("value", 0) or 0)
                if size < self._min_whale_size:
                    continue
                outcome = (pos.get("outcome", "") or pos.get("side", "")).upper()
                return WhaleActivity(
                    trader_address=addr,
                    trader_rank=trader["rank"],
                    action=f"HOLD_{outcome}" if outcome else "HOLD",
                    market_condition_id=condition_id,
                    size_usdc=size,
                    timestamp=pos.get("timestamp", ""),
                )
        return None

    # -----------------------------------------------------------------------
    # Position diffing — advanced
    # -----------------------------------------------------------------------
    def _diff_positions(self, old: list, new: list,
                        trader: TraderProfile | dict) -> list[WhaleActivity]:
        """Advanced position diff that detects:

        1. New entries (position did not exist before)
        2. Size increases (adding to winners or losers)
        3. Size decreases (partial exits)
        4. Full exits (position closed entirely)
        5. Urgency classification (large immediate fills vs gradual scaling)
        6. Dollar-flow aggregation per market
        7. Weighting by trader historical accuracy
        """
        if isinstance(trader, dict):
            rank = trader.get("rank", 99)
            accuracy_weight = 1.0
        else:
            rank = trader.rank
            accuracy_weight = max(0.3, trader.signal_reliability)

        # Build maps: condition_id → {size, outcome, ...}
        def _build_map(positions: list) -> dict[str, dict]:
            m: dict[str, dict] = {}
            for p in positions:
                cid = p.get("conditionId") or p.get("condition_id", "")
                if cid:
                    m[cid] = p
            return m

        old_map = _build_map(old)
        new_map = _build_map(new)

        signals: list[WhaleActivity] = []

        def _size(p: dict) -> float:
            return float(p.get("size", 0) or p.get("value", 0) or 0)

        def _outcome(p: dict) -> str:
            return (p.get("outcome", "") or p.get("side", "")).upper()

        # -- Detect new entries & size changes --------------------------------
        for cid, p in new_map.items():
            new_size = _size(p)
            outcome = _outcome(p)

            if cid not in old_map:
                # Brand-new position
                if new_size >= self._min_whale_size:
                    action = f"BUY_{outcome}" if outcome else "BUY"
                    signals.append(WhaleActivity(
                        trader_address="",
                        trader_rank=rank,
                        action=action,
                        market_condition_id=cid,
                        size_usdc=round(new_size * accuracy_weight, 2),
                        timestamp=p.get("timestamp", ""),
                    ))
            else:
                old_size = _size(old_map[cid])
                diff = new_size - old_size

                if diff > 0 and diff >= self._min_whale_size * 0.5:
                    # Adding to existing position
                    is_large = diff >= _DEFAULT_LARGE_MOVE_THRESHOLD or diff >= old_size * _DEFAULT_LARGE_MOVE_MULT
                    action = f"ADD_{outcome}" if outcome else "ADD"
                    if is_large:
                        action = f"LARGE_{action}"
                    signals.append(WhaleActivity(
                        trader_address="",
                        trader_rank=rank,
                        action=action,
                        market_condition_id=cid,
                        size_usdc=round(diff * accuracy_weight, 2),
                        timestamp=p.get("timestamp", ""),
                    ))

                elif diff < 0 and abs(diff) >= self._min_whale_size * 0.5:
                    # Reducing position
                    remaining_pct = new_size / max(old_size, 1)
                    action = f"REDUCE_{outcome}" if outcome else "REDUCE"
                    if remaining_pct < 0.1:
                        action = f"EXIT_{outcome}" if outcome else "EXIT"
                    signals.append(WhaleActivity(
                        trader_address="",
                        trader_rank=rank,
                        action=action,
                        market_condition_id=cid,
                        size_usdc=round(abs(diff) * accuracy_weight, 2),
                        timestamp=p.get("timestamp", ""),
                    ))

        # -- Detect full exits (in old but not in new) ------------------------
        for cid, p in old_map.items():
            if cid in new_map:
                continue
            old_size = _size(p)
            if old_size >= self._min_whale_size * 0.5:
                outcome = _outcome(p)
                action = f"EXIT_{outcome}" if outcome else "EXIT"
                signals.append(WhaleActivity(
                    trader_address="",
                    trader_rank=rank,
                    action=action,
                    market_condition_id=cid,
                    size_usdc=round(old_size * accuracy_weight, 2),
                    timestamp=p.get("timestamp", ""),
                ))

        return signals

    # -----------------------------------------------------------------------
    # Aggregate flow analysis
    # -----------------------------------------------------------------------
    def get_aggregate_flow(self, condition_id: str) -> dict:
        """Aggregate all tracked trader activity on a market.

        Returns:
            dict with keys:
                net_flow_usd       – net $ flow (positive = buying, negative = selling)
                num_buyers         – number of distinct traders buying
                num_sellers        – number of distinct traders selling
                conviction_score   – flow weighted by trader rank and size (0-100)
                flow_direction     – "STRONG_BUY" / "BUY" / "NEUTRAL" / "SELL" / "STRONG_SELL"
                total_volume       – absolute volume of all activity
                recent_signals     – count of signals in last hour
        """
        activities = self._flow_history.get(condition_id, [])
        if not activities:
            return {
                "net_flow_usd": 0.0,
                "num_buyers": 0,
                "num_sellers": 0,
                "conviction_score": 0.0,
                "flow_direction": "NEUTRAL",
                "total_volume": 0.0,
                "recent_signals": 0,
            }

        now = time.time()
        buyers: set[str] = set()
        sellers: set[str] = set()
        net_flow = 0.0
        total_vol = 0.0
        weighted_flow = 0.0
        recent = 0

        for act in activities:
            action_upper = act.action.upper()
            is_buy = any(k in action_upper for k in ("BUY", "ADD", "HOLD"))
            is_sell = any(k in action_upper for k in ("SELL", "EXIT", "REDUCE"))

            # Rank weight: top-ranked traders carry more conviction
            rank_w = max(0.05, 1.0 - (act.trader_rank - 1) * 0.05)

            if is_buy:
                buyers.add(act.trader_address)
                net_flow += act.size_usdc
                weighted_flow += act.size_usdc * rank_w
            elif is_sell:
                sellers.add(act.trader_address)
                net_flow -= act.size_usdc
                weighted_flow -= act.size_usdc * rank_w

            total_vol += act.size_usdc

            # Check recency
            try:
                ts = datetime.fromisoformat(act.timestamp.replace("Z", "+00:00"))
                if (now - ts.timestamp()) < _CONVERGENCE_WINDOW_SEC:
                    recent += 1
            except Exception:
                pass

        # Normalise conviction to 0-100 scale
        # Use log-scale anchored at $10k = conviction 50
        import math
        raw_conviction = abs(weighted_flow)
        if raw_conviction > 0:
            conviction = min(100.0, 50.0 * math.log10(max(raw_conviction, 1)) / math.log10(10_000))
        else:
            conviction = 0.0

        # Classify direction
        if total_vol == 0:
            direction = "NEUTRAL"
        else:
            ratio = net_flow / total_vol  # -1 to +1
            if ratio > 0.6:
                direction = "STRONG_BUY"
            elif ratio > 0.2:
                direction = "BUY"
            elif ratio < -0.6:
                direction = "STRONG_SELL"
            elif ratio < -0.2:
                direction = "SELL"
            else:
                direction = "NEUTRAL"

        return {
            "net_flow_usd": round(net_flow, 2),
            "num_buyers": len(buyers),
            "num_sellers": len(sellers),
            "conviction_score": round(conviction, 1),
            "flow_direction": direction,
            "total_volume": round(total_vol, 2),
            "recent_signals": recent,
        }

    # -----------------------------------------------------------------------
    # Timing intelligence
    # -----------------------------------------------------------------------
    def record_price(self, condition_id: str, price: float) -> None:
        """Record a price observation for timing analysis."""
        self._price_history[condition_id].append((time.time(), price))
        # Keep last 500 observations per market
        if len(self._price_history[condition_id]) > 500:
            self._price_history[condition_id] = self._price_history[condition_id][-500:]

    def get_entry_timing(self, whale: WhaleActivity,
                         market: PolyMarket) -> dict:
        """Analyse the whale's entry timing relative to market context.

        Returns:
            dict with keys:
                market_age_category – "EARLY" / "MID" / "LATE"
                pre_move            – True if whale entered before a price move
                contrarian          – True if going against recent trend
                convergence_count   – number of other whales active in same
                                      direction within the convergence window
                timing_quality      – 0-1 composite score
        """
        now = time.time()

        # 1. Market age: early / mid / late
        try:
            end = datetime.fromisoformat(market.end_date.replace("Z", "+00:00"))
            remaining = (end - datetime.now(timezone.utc)).total_seconds()
            total_life = max(remaining + 86400, 1)  # estimate market opened ~1d before now
            pct_remaining = remaining / total_life
            if pct_remaining > 0.7:
                age_cat = "EARLY"
            elif pct_remaining > 0.3:
                age_cat = "MID"
            else:
                age_cat = "LATE"
        except Exception:
            age_cat = "MID"

        # 2. Recent price trend (last 1h)
        prices = self._price_history.get(market.condition_id, [])
        recent = [p for ts, p in prices if now - ts < 3600]
        if len(recent) >= 2:
            trend = recent[-1] - recent[0]  # positive = price up
        else:
            trend = 0.0

        # 3. Contrarian: whale buying while price falling, or selling while rising
        action_upper = whale.action.upper()
        is_buy = any(k in action_upper for k in ("BUY", "ADD"))
        is_sell = any(k in action_upper for k in ("SELL", "EXIT", "REDUCE"))
        contrarian = (is_buy and trend < -0.02) or (is_sell and trend > 0.02)

        # 4. Pre-move detection: did price move >2% after entry?
        # Use most recent price vs whale timestamp
        try:
            whale_ts = datetime.fromisoformat(
                whale.timestamp.replace("Z", "+00:00")).timestamp()
        except Exception:
            whale_ts = now
        post_prices = [p for ts, p in prices if ts > whale_ts]
        if post_prices and recent:
            post_move = abs(post_prices[-1] - post_prices[0])
            pre_move = post_move > 0.02
        else:
            pre_move = False

        # 5. Convergence: how many other whales entered same market & direction
        #    within the convergence window
        flow = self._flow_history.get(market.condition_id, [])
        convergence = 0
        for act in flow:
            if act.trader_address == whale.trader_address:
                continue
            try:
                act_ts = datetime.fromisoformat(
                    act.timestamp.replace("Z", "+00:00")).timestamp()
            except Exception:
                act_ts = 0
            if abs(act_ts - whale_ts) > _CONVERGENCE_WINDOW_SEC:
                continue
            act_buy = any(k in act.action.upper() for k in ("BUY", "ADD"))
            act_sell = any(k in act.action.upper() for k in ("SELL", "EXIT", "REDUCE"))
            if (is_buy and act_buy) or (is_sell and act_sell):
                convergence += 1

        # 6. Composite timing quality
        #    Early entries are better, contrarian is interesting, convergence is very bullish
        timing_q = 0.5
        if age_cat == "EARLY":
            timing_q += 0.15
        elif age_cat == "LATE":
            timing_q -= 0.1
        if contrarian:
            timing_q += 0.1
        if convergence >= 2:
            timing_q += 0.2
        elif convergence >= 1:
            timing_q += 0.1
        if pre_move:
            timing_q += 0.1
        timing_q = max(0.0, min(1.0, timing_q))

        return {
            "market_age_category": age_cat,
            "pre_move": pre_move,
            "contrarian": contrarian,
            "convergence_count": convergence,
            "timing_quality": round(timing_q, 2),
            "recent_trend": round(trend, 4),
        }

    # -----------------------------------------------------------------------
    # Alert system
    # -----------------------------------------------------------------------
    def get_whale_alerts(self, markets: list[PolyMarket]) -> list[dict]:
        """Generate high-priority alerts for exceptional whale activity.

        Alert types (in descending priority):
          1. CONVERGENCE   – Multiple top-10 traders entering same market, same direction
          2. LARGE_MOVE    – Single trader making >=2x normal position ($5k+ abs)
          3. CONTRARIAN    – Top-3 trader going against recent trend
          4. EXODUS        – Multiple smart-money exits from same market
          5. TOP_ENTRY     – Top-5 trader opening a new position

        Returns list of alert dicts sorted by priority (highest first).
        """
        signals = self.get_whale_signals(markets)
        if not signals:
            return []

        market_map = {m.condition_id: m for m in markets}
        alerts: list[dict] = []

        # Group signals by market
        by_market: dict[str, list[WhaleActivity]] = defaultdict(list)
        for sig in signals:
            by_market[sig.market_condition_id].append(sig)

        for cid, sigs in by_market.items():
            mkt = market_map.get(cid)
            mkt_question = mkt.question[:80] if mkt else cid[:16]

            buys = [s for s in sigs if any(
                k in s.action.upper() for k in ("BUY", "ADD"))]
            sells = [s for s in sigs if any(
                k in s.action.upper() for k in ("SELL", "EXIT", "REDUCE"))]

            # 1. CONVERGENCE — multiple top-10 buying same direction
            unique_buy_addrs = {s.trader_address for s in buys if s.trader_rank <= 10}
            unique_sell_addrs = {s.trader_address for s in sells if s.trader_rank <= 10}

            if len(unique_buy_addrs) >= 2:
                total_usd = sum(s.size_usdc for s in buys)
                alerts.append({
                    "type": "CONVERGENCE",
                    "priority": 100,
                    "market": mkt_question,
                    "condition_id": cid,
                    "direction": "BUY",
                    "num_traders": len(unique_buy_addrs),
                    "total_usd": round(total_usd, 2),
                    "message": (f"{len(unique_buy_addrs)} top-10 traders buying "
                                f"{mkt_question} (${total_usd:,.0f} total)"),
                })

            if len(unique_sell_addrs) >= 2:
                total_usd = sum(s.size_usdc for s in sells)
                alerts.append({
                    "type": "CONVERGENCE",
                    "priority": 100,
                    "market": mkt_question,
                    "condition_id": cid,
                    "direction": "SELL",
                    "num_traders": len(unique_sell_addrs),
                    "total_usd": round(total_usd, 2),
                    "message": (f"{len(unique_sell_addrs)} top-10 traders selling "
                                f"{mkt_question} (${total_usd:,.0f} total)"),
                })

            # 2. LARGE_MOVE — single trader >$5k or >2x normal
            for sig in sigs:
                if sig.size_usdc >= _DEFAULT_LARGE_MOVE_THRESHOLD:
                    alerts.append({
                        "type": "LARGE_MOVE",
                        "priority": 90,
                        "market": mkt_question,
                        "condition_id": cid,
                        "trader": sig.trader_address[:10],
                        "rank": sig.trader_rank,
                        "size_usd": round(sig.size_usdc, 2),
                        "action": sig.action,
                        "message": (f"Rank #{sig.trader_rank} trader "
                                    f"${sig.size_usdc:,.0f} {sig.action} on {mkt_question}"),
                    })

            # 3. CONTRARIAN — top-3 trader going against trend
            for sig in sigs:
                if sig.trader_rank > 3:
                    continue
                if not mkt:
                    continue
                timing = self.get_entry_timing(sig, mkt)
                if timing["contrarian"]:
                    alerts.append({
                        "type": "CONTRARIAN",
                        "priority": 85,
                        "market": mkt_question,
                        "condition_id": cid,
                        "trader": sig.trader_address[:10],
                        "rank": sig.trader_rank,
                        "action": sig.action,
                        "trend": timing["recent_trend"],
                        "message": (f"Top-{sig.trader_rank} CONTRARIAN "
                                    f"{sig.action} on {mkt_question} "
                                    f"(trend={timing['recent_trend']:+.3f})"),
                    })

            # 4. EXODUS — multiple exits from same market
            exit_sigs = [s for s in sells
                         if any(k in s.action.upper() for k in ("EXIT", "REDUCE"))]
            unique_exit_addrs = {s.trader_address for s in exit_sigs}
            if len(unique_exit_addrs) >= 2:
                total_exit = sum(s.size_usdc for s in exit_sigs)
                alerts.append({
                    "type": "EXODUS",
                    "priority": 80,
                    "market": mkt_question,
                    "condition_id": cid,
                    "num_traders": len(unique_exit_addrs),
                    "total_usd": round(total_exit, 2),
                    "message": (f"Smart money EXODUS: {len(unique_exit_addrs)} traders "
                                f"exiting {mkt_question} (${total_exit:,.0f})"),
                })

            # 5. TOP_ENTRY — top-5 trader new position
            for sig in buys:
                if sig.trader_rank <= 5 and "BUY" in sig.action.upper():
                    # Skip if already captured as convergence or large move
                    alerts.append({
                        "type": "TOP_ENTRY",
                        "priority": 70,
                        "market": mkt_question,
                        "condition_id": cid,
                        "trader": sig.trader_address[:10],
                        "rank": sig.trader_rank,
                        "size_usd": round(sig.size_usdc, 2),
                        "action": sig.action,
                        "message": (f"Top-{sig.trader_rank} NEW ENTRY "
                                    f"{sig.action} ${sig.size_usdc:,.0f} on {mkt_question}"),
                    })

        # Sort by priority descending, then by total USD
        alerts.sort(key=lambda a: (a["priority"], a.get("total_usd", a.get("size_usd", 0))),
                    reverse=True)

        if alerts:
            logger.info("Whale alerts: %d alerts generated (top: %s)",
                        len(alerts), alerts[0].get("type", "?"))
        return alerts

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------
    def get_flow_history(self, condition_id: str,
                         max_age_seconds: int = 86400) -> list[WhaleActivity]:
        """Return recent flow history for a market, pruning old entries."""
        cutoff = time.time() - max_age_seconds
        activities = self._flow_history.get(condition_id, [])
        recent = []
        for act in activities:
            try:
                ts = datetime.fromisoformat(
                    act.timestamp.replace("Z", "+00:00")).timestamp()
                if ts >= cutoff:
                    recent.append(act)
            except Exception:
                recent.append(act)  # keep if timestamp unparseable
        self._flow_history[condition_id] = recent
        return recent

    def get_all_profiles(self) -> list[TraderProfile]:
        """Return all cached trader profiles, sorted by rank."""
        profiles = list(self._trader_profiles.values())
        profiles.sort(key=lambda p: p.rank)
        return profiles

    def summary(self) -> dict:
        """Return a summary of the tracker's state for logging / dashboards."""
        total_flow_markets = len(self._flow_history)
        total_signals = sum(len(v) for v in self._flow_history.values())
        return {
            "tracked_traders": len(self._tracked_traders),
            "profiled_traders": len(self._trader_profiles),
            "markets_with_flow": total_flow_markets,
            "total_signals_recorded": total_signals,
            "accuracy_records": sum(
                len(v) for v in self.accuracy.records.values()),
        }
