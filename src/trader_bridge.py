"""ARCANA AI — Trader Bridge.

Connects the trader/ module to ARCANA's reporting, content, and revenue tracking.
Reads trader state (trades.json, equity_curve.json, dex_positions.json) and surfaces
profits/losses in morning reports, trade receipt tweets, and unified revenue tracking.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.llm import LLM, Tier
from src.memory import Memory

logger = logging.getLogger("arcana.trader_bridge")

TRADER_DIR = Path(__file__).resolve().parent.parent / "trader"
TRADES_FILE = TRADER_DIR / "trades.json"
EQUITY_FILE = TRADER_DIR / "equity_curve.json"
DEX_POSITIONS_FILE = TRADER_DIR / "dex_positions.json"


class TraderBridge:
    """Bridge between trader/ bot and ARCANA AI reporting."""

    def __init__(self, llm: LLM, memory: Memory) -> None:
        self.llm = llm
        self.memory = memory

    def _read_json(self, path: Path) -> dict | list | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def get_portfolio_state(self) -> dict[str, Any]:
        """Read current trader portfolio state."""
        data = self._read_json(TRADES_FILE)
        if data is None:
            # File doesn't exist or couldn't be read
            return {"active": False, "equity": 0, "positions": 0, "trades_today": 0}

        # Empty data structure means trader exists but has no trades yet
        if isinstance(data, list):
            # Handle list format - treat as history
            positions = {}
            history = data
            equity = 0
        elif isinstance(data, dict):
            positions = data.get("positions", {})
            history = data.get("history", [])
            equity = data.get("equity", 0)
        else:
            return {"active": False, "equity": 0, "positions": 0, "trades_today": 0}

        # Count today's trades
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_trades = [t for t in history if t.get("timestamp", "").startswith(today)]

        # Calculate today's P&L
        today_pnl = sum(t.get("pnl_usd", 0) for t in today_trades)

        # Win rate
        wins = [t for t in today_trades if t.get("pnl_usd", 0) > 0]
        win_rate = len(wins) / len(today_trades) * 100 if today_trades else 0

        return {
            "active": True,
            "equity": equity,
            "open_positions": len(positions),
            "trades_today": len(today_trades),
            "pnl_today": today_pnl,
            "win_rate_today": win_rate,
            "total_trades": len(history),
            "positions": positions,
        }

    def get_equity_curve(self) -> list[dict[str, Any]]:
        """Read equity curve snapshots."""
        data = self._read_json(EQUITY_FILE)
        if data is None:
            return []
        return data if isinstance(data, list) else data.get("snapshots", [])

    def get_dex_positions(self) -> list[dict[str, Any]]:
        """Read open DEX/memecoin positions."""
        data = self._read_json(DEX_POSITIONS_FILE)
        if data is None:
            return []
        return data if isinstance(data, list) else []

    def get_recent_winners(self, n: int = 5) -> list[dict[str, Any]]:
        """Get the N most profitable recent trades for content."""
        data = self._read_json(TRADES_FILE)
        if data is None:
            return []
        if isinstance(data, list):
            history = data
        elif isinstance(data, dict):
            history = data.get("history", [])
        else:
            return []
        winners = sorted(
            [t for t in history if t.get("pnl_usd", 0) > 0],
            key=lambda t: t.get("pnl_usd", 0),
            reverse=True,
        )
        return winners[:n]

    async def generate_trade_receipt(self, trade: dict[str, Any]) -> str:
        """Generate a trade receipt tweet for X."""
        pnl = trade.get("pnl_usd", 0)
        pnl_pct = trade.get("pnl_pct", 0)
        symbol = trade.get("symbol", "???")
        side = trade.get("side", "long")
        hold_time = trade.get("hold_time_hours", 0)

        tweet = await self.llm.ask(
            f"Generate a trade receipt tweet for ARCANA AI.\n\n"
            f"Trade: {side.upper()} {symbol}\n"
            f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
            f"Hold time: {hold_time:.1f}h\n\n"
            f"Rules:\n"
            f"- Under 280 chars\n"
            f"- Use ARCANA's voice (mystical, pattern-focused)\n"
            f"- Include the numbers\n"
            f"- No financial advice disclaimers\n"
            f"- No hype language or rocket emojis\n"
            f"- Make it shareable\n"
            f"Example style: 'The cards revealed {symbol}. {side.title()} entry, "
            f"${abs(pnl):.0f} extracted. The pattern was there for those watching.'",
            tier=Tier.HAIKU,
            max_tokens=100,
        )
        return tweet.strip()

    def get_trading_summary_for_report(self) -> str:
        """Generate trading summary text for morning report."""
        state = self.get_portfolio_state()
        if not state["active"]:
            return "Trader: Offline or no data."

        dex = self.get_dex_positions()
        return (
            f"**Trading Bot:**\n"
            f"- Equity: ${state['equity']:,.2f}\n"
            f"- Open positions: {state['open_positions']}\n"
            f"- Today's trades: {state['trades_today']}\n"
            f"- Today's P&L: ${state['pnl_today']:+,.2f}\n"
            f"- Win rate: {state['win_rate_today']:.0f}%\n"
            f"- DEX positions: {len(dex)}\n"
            f"- Total lifetime trades: {state['total_trades']}"
        )

    def get_monthly_trading_revenue(self) -> float:
        """Calculate trading revenue for current month."""
        data = self._read_json(TRADES_FILE)
        if data is None:
            return 0.0
        if isinstance(data, list):
            history = data
        elif isinstance(data, dict):
            history = data.get("history", [])
        else:
            return 0.0
        now = datetime.now(timezone.utc)
        month_prefix = now.strftime("%Y-%m")
        month_pnl = sum(
            t.get("pnl_usd", 0)
            for t in history
            if t.get("timestamp", "").startswith(month_prefix)
        )
        return month_pnl  # Report actual P&L including losses
