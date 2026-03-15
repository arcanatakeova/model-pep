"""
Smart Money Tracker
===================
Tracks top Polymarket traders via Data API leaderboards.
"""
from __future__ import annotations
import logging
import time
from typing import Optional

from .models import PolyMarket, WhaleActivity

logger = logging.getLogger(__name__)


class SmartMoneyTracker:
    """Tracks top traders and detects whale movements."""

    def __init__(self, api_client):
        self._api = api_client
        self._tracked_traders: list[dict] = []  # [{address, rank, pnl}, ...]
        self._trader_positions: dict[str, list] = {}  # addr -> positions snapshot
        self._last_refresh = 0.0
        self._refresh_interval = 1800  # 30 min
        self._min_whale_size = 1000  # $1000 minimum to be considered whale move

    def refresh_leaderboard(self) -> list[dict]:
        """Refresh top traders from leaderboard (cached for 30 min)."""
        now = time.time()
        if now - self._last_refresh < self._refresh_interval and self._tracked_traders:
            return self._tracked_traders

        try:
            leaders = self._api.get_leaderboard(limit=20)
            self._tracked_traders = []
            for i, leader in enumerate(leaders):
                addr = leader.get("address") or leader.get("user", "")
                if addr:
                    self._tracked_traders.append({
                        "address": addr,
                        "rank": i + 1,
                        "pnl": float(leader.get("pnl", 0) or 0),
                        "volume": float(leader.get("volume", 0) or 0),
                    })
            self._last_refresh = now
            logger.info("Smart money: tracking %d top traders", len(self._tracked_traders))
        except Exception as e:
            logger.debug("Leaderboard refresh error: %s", e)

        return self._tracked_traders

    def get_whale_signals(self, markets: list[PolyMarket]) -> list[WhaleActivity]:
        """
        Check for whale activity on tracked markets.
        Compares current positions with previous snapshot to detect new entries.
        """
        self.refresh_leaderboard()

        signals: list[WhaleActivity] = []
        market_ids = {m.condition_id for m in markets}

        for trader in self._tracked_traders[:10]:  # Check top 10 only (API rate limits)
            addr = trader["address"]
            try:
                current_positions = self._api.get_trader_positions(addr)
            except Exception:
                continue

            prev_positions = self._trader_positions.get(addr, [])
            new_moves = self._diff_positions(prev_positions, current_positions, trader["rank"])

            # Filter to markets we're watching
            for move in new_moves:
                if move.market_condition_id in market_ids and move.size_usdc >= self._min_whale_size:
                    signals.append(move)

            self._trader_positions[addr] = current_positions

        if signals:
            logger.info("Smart money: %d whale signals detected", len(signals))
        return signals

    def detect_whale_movement(self, condition_id: str) -> Optional[WhaleActivity]:
        """Check for whale activity on a specific market."""
        self.refresh_leaderboard()

        for trader in self._tracked_traders[:5]:
            addr = trader["address"]
            try:
                positions = self._api.get_trader_positions(addr)
            except Exception:
                continue

            for pos in positions:
                pos_cid = pos.get("conditionId") or pos.get("condition_id", "")
                if pos_cid == condition_id:
                    size = float(pos.get("size", 0) or pos.get("value", 0) or 0)
                    if size >= self._min_whale_size:
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

    def _diff_positions(self, old: list, new: list, rank: int) -> list[WhaleActivity]:
        """Compare position snapshots to detect new entries/exits."""
        old_map: dict[str, dict] = {}
        for p in old:
            cid = p.get("conditionId") or p.get("condition_id", "")
            if cid:
                old_map[cid] = p

        signals: list[WhaleActivity] = []
        for p in new:
            cid = p.get("conditionId") or p.get("condition_id", "")
            if not cid:
                continue

            new_size = float(p.get("size", 0) or p.get("value", 0) or 0)
            outcome = (p.get("outcome", "") or p.get("side", "")).upper()

            if cid not in old_map:
                # New position
                if new_size >= self._min_whale_size:
                    signals.append(WhaleActivity(
                        trader_address="",  # Filled by caller
                        trader_rank=rank,
                        action=f"BUY_{outcome}" if outcome else "BUY",
                        market_condition_id=cid,
                        size_usdc=new_size,
                        timestamp=p.get("timestamp", ""),
                    ))
            else:
                old_size = float(old_map[cid].get("size", 0) or old_map[cid].get("value", 0) or 0)
                diff = new_size - old_size
                if diff >= self._min_whale_size:
                    signals.append(WhaleActivity(
                        trader_address="",
                        trader_rank=rank,
                        action=f"BUY_{outcome}" if outcome else "ADD",
                        market_condition_id=cid,
                        size_usdc=diff,
                        timestamp=p.get("timestamp", ""),
                    ))

        return signals
