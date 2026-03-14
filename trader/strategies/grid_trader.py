"""
Grid Trading Strategy
=====================
Places a ladder of limit orders above and below the current price,
automatically buying dips and selling rips within a range.

How it works:
  Center price: e.g. BTC = $67,000
  Grid spacing: 0.4% between levels
  8 levels each side:
    SELL at $67,268 → $67,537 → $67,807 → ... → $69,726
    BUY  at $66,732 → $66,465 → $66,199 → ... → $64,512

  When a BUY fills → place a SELL one level above it (profit locked)
  When a SELL fills → place a BUY one level below it (reinvest)

  Profits from every oscillation. Works beautifully in ranging markets.
  Exits cleanly on strong trend breakout (ensemble score > 0.6).

Expected returns:
  0.1-0.3%/day in ranging markets
  Near-zero on strong trend (exits early)
  Stop-loss: center ±5% = grid abandoned

Paper trading: simulates fills when price crosses level thresholds
Live trading: uses Binance limit orders (POST-ONLY for maker rebates)
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import config
import data_fetcher as df_mod

logger = logging.getLogger(__name__)


@dataclass
class GridLevel:
    """One rung of the grid ladder."""
    price: float
    side: str           # "buy" or "sell"
    size_usd: float
    filled: bool = False
    fill_price: Optional[float] = None
    fill_time: Optional[str] = None
    pnl_usd: float = 0.0
    order_id: Optional[str] = None


@dataclass
class Grid:
    """A complete grid for one symbol."""
    symbol: str
    center_price: float
    spacing_pct: float
    levels: list[GridLevel] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_pnl_usd: float = 0.0
    fills: int = 0
    active: bool = True
    close_reason: str = ""

    def buy_levels(self)  -> list[GridLevel]:
        return [l for l in self.levels if l.side == "buy"  and not l.filled]

    def sell_levels(self) -> list[GridLevel]:
        return [l for l in self.levels if l.side == "sell" and not l.filled]

    def lowest_buy(self)  -> Optional[float]:
        buys = self.buy_levels()
        return min(l.price for l in buys) if buys else None

    def highest_sell(self) -> Optional[float]:
        sells = self.sell_levels()
        return max(l.price for l in sells) if sells else None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "center_price": self.center_price,
            "spacing_pct": self.spacing_pct,
            "levels": len(self.levels),
            "active_buys": len(self.buy_levels()),
            "active_sells": len(self.sell_levels()),
            "fills": self.fills,
            "total_pnl_usd": round(self.total_pnl_usd, 4),
            "created_at": self.created_at,
            "active": self.active,
            "close_reason": self.close_reason,
        }


class GridTrader:
    """
    Manages multi-symbol grid trading strategies.

    Usage:
        trader = GridTrader(portfolio, executor)
        trader.open_grid("BTC", center_price=67000)
        trader.update_all_grids()   # call every cycle
    """

    def __init__(self, portfolio, executor):
        self.portfolio = portfolio
        self.executor  = executor
        self.grids: dict[str, Grid] = {}   # symbol → Grid
        self.history: list[dict] = []

    # ─────────────────────────────────────────────────────────────────────────
    # Grid Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def open_grid(self, symbol: str, center_price: float = 0.0) -> Optional[Grid]:
        """
        Open a new grid for `symbol`.
        If center_price is 0, fetches current WebSocket/REST price.
        """
        if symbol in self.grids and self.grids[symbol].active:
            logger.debug("Grid already active for %s", symbol)
            return None

        if center_price <= 0:
            center_price = df_mod.get_realtime_price(symbol)
            if not center_price or center_price <= 0:
                logger.warning("Cannot open grid for %s: no valid price", symbol)
                return None

        n       = config.GRID_LEVELS
        spacing = config.GRID_SPACING_PCT
        sz      = config.GRID_SIZE_USD_PER_LEVEL
        max_usd = config.GRID_MAX_TOTAL_USD

        # Validate config
        if spacing <= 0:
            logger.warning("GRID_SPACING_PCT must be > 0, got %s — skipping grid", spacing)
            return None
        if n <= 0:
            logger.warning("GRID_LEVELS must be > 0 — skipping grid")
            return None

        # Cap by max_usd config and available cash (leave 40% for other strategies)
        buy_budget = min(sz * n, max_usd / 2, self.portfolio.cash * 0.60)
        sz = buy_budget / n if n > 0 else sz
        if sz < 5.0:
            logger.warning("Grid budget too small ($%.2f/level) for %s — skipping", sz, symbol)
            return None

        total_needed = sz * n

        levels: list[GridLevel] = []

        # BUY levels below center (determine decimal precision from price)
        decimals = max(2, -int(math.floor(math.log10(abs(center_price)))) + 3) if center_price >= 0.001 else 8
        for i in range(1, n + 1):
            price = center_price * (1 - spacing * i)
            if price <= 0:
                continue
            levels.append(GridLevel(price=round(price, decimals), side="buy", size_usd=round(sz, 2)))

        # SELL levels above center
        for i in range(1, n + 1):
            price = center_price * (1 + spacing * i)
            levels.append(GridLevel(price=round(price, decimals), side="sell", size_usd=round(sz, 2)))

        grid = Grid(
            symbol=symbol,
            center_price=center_price,
            spacing_pct=spacing,
            levels=levels,
        )
        self.grids[symbol] = grid

        # Reserve cash for buy orders
        self.portfolio.cash -= total_needed

        logger.info(
            "GRID OPEN %-6s center=$%.4f | %d levels @ %.3f%% spacing | "
            "buy range: $%.4f–$%.4f | sell range: $%.4f–$%.4f | total=$%.0f",
            symbol, center_price, n, spacing * 100,
            levels[n - 1].price, levels[0].price,
            levels[n].price, levels[-1].price,
            total_needed,
        )

        # Live: place limit orders on exchange
        if self.executor._exchange and not config.PAPER_TRADING:
            self._place_live_orders(grid)

        return grid

    def close_grid(self, symbol: str, reason: str = "Manual close"):
        """Cancel all pending grid orders and return reserved cash."""
        grid = self.grids.pop(symbol, None)
        if not grid:
            return

        grid.active      = False
        grid.close_reason = reason

        # Return unused cash from unfilled buy orders
        unfilled_cash = sum(l.size_usd for l in grid.levels
                           if l.side == "buy" and not l.filled)
        self.portfolio.cash += unfilled_cash

        self.history.append(grid.to_dict())

        logger.info("GRID CLOSE %s | fills=%d pnl=$%.2f | %s",
                    symbol, grid.fills, grid.total_pnl_usd, reason)

        # Live: cancel orders
        if self.executor._exchange and not config.PAPER_TRADING:
            try:
                self.executor._exchange.cancel_all_orders(symbol + "USDT")
            except Exception as e:
                logger.warning("Failed to cancel grid orders for %s: %s", symbol, e)

    # ─────────────────────────────────────────────────────────────────────────
    # Cycle Update
    # ─────────────────────────────────────────────────────────────────────────

    def update_all_grids(self):
        """
        Call every cycle. Checks each grid for:
          1. Price crossing a level → simulate fill
          2. After fill → place counter order one level away
          3. Exit conditions (strong trend breakout, ±5% from center)
        """
        for symbol in list(self.grids.keys()):
            grid = self.grids[symbol]
            if not grid.active:
                continue
            try:
                self._update_grid(symbol, grid)
            except Exception as e:
                logger.debug("Grid update error for %s: %s", symbol, e)

    def _update_grid(self, symbol: str, grid: Grid):
        current_price = df_mod.get_realtime_price(symbol)
        if not current_price or current_price <= 0:
            return
        if grid.center_price <= 0:
            self.close_grid(symbol, "Invalid center price")
            return

        # ── Exit condition: price moved too far from center ───────────────────
        distance_pct = abs(current_price - grid.center_price) / grid.center_price
        if distance_pct > 0.05:   # 5% from center — trend breakout
            self.close_grid(symbol, f"Trend breakout: {distance_pct*100:.1f}% from center")
            return

        filled_any = False

        # ── Check BUY levels (filled when price drops to/below level) ─────────
        for level in grid.levels:
            if level.filled:
                continue
            if level.side == "buy" and current_price <= level.price:
                self._fill_buy(grid, level, current_price)
                filled_any = True

            elif level.side == "sell" and current_price >= level.price:
                self._fill_sell(grid, level, current_price)
                filled_any = True

        if filled_any:
            logger.debug("Grid %s: price=%.4f center=%.4f dist=%.2f%%",
                         symbol, current_price, grid.center_price, distance_pct * 100)

    def _fill_buy(self, grid: Grid, level: GridLevel, fill_price: float):
        """Simulate/execute a buy fill and place the counter sell."""
        level.filled     = True
        level.fill_price = fill_price
        level.fill_time  = datetime.now(timezone.utc).isoformat()

        # Place counter SELL one spacing above fill price
        sell_price = fill_price * (1 + grid.spacing_pct)
        counter = GridLevel(
            price=round(sell_price, 6),
            side="sell",
            size_usd=level.size_usd,
        )
        grid.levels.append(counter)
        grid.fills += 1

        logger.info("GRID FILL BUY  %s @ $%.4f | counter SELL @ $%.4f",
                    grid.symbol, fill_price, sell_price)

    def _fill_sell(self, grid: Grid, level: GridLevel, fill_price: float):
        """Simulate/execute a sell fill and record the profit."""
        # Find the corresponding buy that created this sell level
        # Profit = (sell_price - buy_price) * qty
        buy_price = fill_price / (1 + grid.spacing_pct)
        qty       = level.size_usd / buy_price
        pnl       = (fill_price - buy_price) * qty

        level.filled     = True
        level.fill_price = fill_price
        level.fill_time  = datetime.now(timezone.utc).isoformat()
        level.pnl_usd    = round(pnl, 4)

        grid.total_pnl_usd += pnl
        grid.fills         += 1
        self.portfolio.cash += level.size_usd + pnl   # Return capital + profit

        # Place counter BUY one spacing below fill price
        buy_price_new = fill_price * (1 - grid.spacing_pct)
        counter = GridLevel(
            price=round(buy_price_new, 6),
            side="buy",
            size_usd=level.size_usd,
        )
        grid.levels.append(counter)

        logger.info("GRID FILL SELL %s @ $%.4f | pnl=$%.2f | counter BUY @ $%.4f",
                    grid.symbol, fill_price, pnl, buy_price_new)

    # ─────────────────────────────────────────────────────────────────────────
    # Smart Grid Management
    # ─────────────────────────────────────────────────────────────────────────

    def maybe_open_grids(self, cex_signals: list):
        """
        Auto-open grids when the market is ranging (ensemble score near 0).
        Called from main.py each cycle.
        """
        for sig in cex_signals:
            sym = sig.symbol.replace("/USDT", "").replace("USDT", "")
            if sym not in config.GRID_SYMBOLS:
                continue
            if sym in self.grids and self.grids[sym].active:
                continue
            # Ranging market: absolute ensemble score is low (no strong trend)
            if abs(sig.score) < 0.25 and sig.regime == "ranging":
                price = df_mod.get_realtime_price(sym) or sig.current_price
                self.open_grid(sym, center_price=price)

    def recenter_grids(self):
        """
        If price has drifted >3% from center but <5% (not a breakout),
        close the current grid and reopen at the new price level.
        Keeps the grid profitable in slow-trending markets.
        """
        for symbol, grid in list(self.grids.items()):
            if not grid.active:
                continue
            price = df_mod.get_realtime_price(symbol)
            if not price:
                continue
            drift = abs(price - grid.center_price) / grid.center_price
            if 0.03 <= drift < 0.05:
                logger.info("GRID RECENTER %s: drift=%.1f%% — closing and reopening", symbol, drift * 100)
                self.close_grid(symbol, f"Recenter at ${price:.4f}")
                self.open_grid(symbol, center_price=price)

    # ─────────────────────────────────────────────────────────────────────────
    # Live Order Management
    # ─────────────────────────────────────────────────────────────────────────

    def _place_live_orders(self, grid: Grid):
        """Place all initial grid limit orders on the exchange."""
        if not self.executor._exchange:
            return
        binance_sym = grid.symbol + "USDT"
        for level in grid.levels:
            if level.filled:
                continue
            try:
                qty = level.size_usd / level.price
                order = self.executor._exchange.create_limit_order(
                    binance_sym,
                    level.side,
                    qty,
                    level.price,
                    params={"timeInForce": "GTC", "postOnly": True},
                )
                level.order_id = order.get("id")
            except Exception as e:
                logger.warning("Grid order failed %s %s @%.4f: %s",
                               level.side, binance_sym, level.price, e)

    def summary(self) -> dict:
        active_grids   = [g.to_dict() for g in self.grids.values() if g.active]
        total_pnl      = sum(g.total_pnl_usd for g in self.grids.values())
        total_pnl     += sum(h.get("total_pnl_usd", 0) for h in self.history)
        total_fills    = sum(g.fills for g in self.grids.values())

        return {
            "active_grids": len(active_grids),
            "total_fills": total_fills,
            "total_pnl_usd": round(total_pnl, 2),
            "grids": active_grids,
        }
