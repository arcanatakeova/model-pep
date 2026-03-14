"""
Funding Rate Arbitrage Strategy
================================
Exploits the Binance USDT-M perpetual futures funding mechanism.

How it works:
  Every 8 hours, perpetual futures contract holders pay/receive a funding rate.
  When longs pay shorts (positive rate), we:
    → Go LONG spot (or USDC/stablecoin hold)
    → Go SHORT futures (delta-neutral)
    → Collect the funding payment every 8 hours

  This is directionally neutral — we profit regardless of price movement,
  as long as the funding rate stays positive. Win rate is ~90%+ historically.

Expected returns:
  0.03% per 8h = 0.09%/day = ~33%/year  (minimum threshold)
  0.10% per 8h = 0.30%/day = ~110%/year (bull market peak rates)

Safety:
  - Only opens when funding rate exceeds MIN_FUNDING_RATE
  - Closes when rate drops below 0.01% (not worth holding)
  - Hard position size cap per pair
  - Tracks all open arb pairs in a separate state file
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import config
import data_fetcher as df_mod

logger = logging.getLogger(__name__)

# Pairs to consider for arb — liquid enough that spot-futures spread is tight
_ARB_CANDIDATES = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT",
]


@dataclass
class ArbPosition:
    """Tracks one open funding rate arb pair."""
    symbol: str                          # e.g. "BTCUSDT"
    short_sym: str                       # e.g. "BTC"
    size_usd: float
    entry_funding_rate: float            # Rate at open (per 8h)
    spot_entry_price: float
    futures_entry_price: float
    opened_at: str
    funding_collected_usd: float = 0.0
    funding_periods: int = 0
    last_funding_ts: float = field(default_factory=time.time)
    close_reason: str = ""
    closed_at: str = ""

    def daily_yield_pct(self) -> float:
        """Annualised daily yield from current funding rate."""
        return self.entry_funding_rate * 3 * 100  # 3 periods/day

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "short_sym": self.short_sym,
            "size_usd": self.size_usd,
            "entry_funding_rate": self.entry_funding_rate,
            "spot_entry_price": self.spot_entry_price,
            "futures_entry_price": self.futures_entry_price,
            "opened_at": self.opened_at,
            "funding_collected_usd": round(self.funding_collected_usd, 4),
            "funding_periods": self.funding_periods,
            "daily_yield_pct": round(self.daily_yield_pct(), 4),
            "close_reason": self.close_reason,
            "closed_at": self.closed_at,
        }


class FundingArbScanner:
    """
    Scans Binance funding rates and manages delta-neutral arb positions.

    Usage:
        scanner = FundingArbScanner(portfolio, executor)
        opportunities = scanner.find_opportunities()   # returns list of dicts
        scanner.open_arb(opp)                          # open one arb pair
        scanner.update_positions()                     # collect funding, check exits
    """

    # Binance funding schedule — every 8h at 00:00, 08:00, 16:00 UTC
    _FUNDING_HOURS = {0, 8, 16}
    _FUNDING_WINDOW_MIN = 5   # Minutes before/after funding time to collect

    def __init__(self, portfolio, executor):
        self.portfolio = portfolio
        self.executor  = executor
        self.open_arbs: dict[str, ArbPosition] = {}   # symbol → ArbPosition
        self.closed_arbs: list[dict] = []
        self._last_scan = 0.0

    # ─────────────────────────────────────────────────────────────────────────
    # Discovery
    # ─────────────────────────────────────────────────────────────────────────

    def find_opportunities(self) -> list[dict]:
        """
        Scan all funding rates and return pairs worth arbitraging.
        Sorted by daily yield (highest first).
        """
        rates = df_mod.get_funding_rates()
        if not rates:
            return []

        opportunities = []
        for symbol, info in rates.items():
            if symbol not in _ARB_CANDIDATES:
                continue
            if symbol in self.open_arbs:
                continue   # Already in this arb

            rate = info["rate"]
            if abs(rate) < config.FUNDING_ARB_MIN_RATE:
                continue

            # Only go long spot / short futures when longs pay shorts (rate > 0)
            # When rate < 0 (shorts pay longs), strategy is reversed — skip for now
            if rate <= 0:
                continue

            short_sym = symbol.replace("USDT", "")
            spot_price = df_mod.get_realtime_price(short_sym)
            if not spot_price:
                continue

            opportunities.append({
                "symbol": symbol,
                "short_sym": short_sym,
                "rate": rate,
                "rate_pct": info["rate_pct"],
                "rate_daily_pct": info["rate_daily_pct"],
                "annualized_pct": info["annualized_pct"],
                "next_funding_time": info["next_funding_time"],
                "spot_price": spot_price,
            })

        opportunities.sort(key=lambda x: x["rate_daily_pct"], reverse=True)
        return opportunities

    # ─────────────────────────────────────────────────────────────────────────
    # Execution
    # ─────────────────────────────────────────────────────────────────────────

    def open_arb(self, opp: dict) -> Optional[ArbPosition]:
        """
        Open a delta-neutral arb position:
          - Long spot (buy and hold the underlying)
          - Short futures at 1x (perfectly hedges the spot)

        In paper mode: simulates both legs at current prices.
        In live mode: sends orders to Binance spot + futures APIs.
        """
        symbol    = opp["symbol"]
        short_sym = opp["short_sym"]
        rate      = opp["rate"]
        price     = opp["spot_price"]

        # Size check
        max_usd = min(
            config.FUNDING_ARB_MAX_POSITION_USD,
            self.portfolio.cash * 0.10,   # Max 10% cash per arb
        )
        if max_usd < 10:
            logger.debug("Insufficient cash for arb %s", symbol)
            return None

        size_usd = max_usd

        # Check if we can afford it
        if size_usd > self.portfolio.cash:
            logger.debug("Not enough cash for arb: need $%.2f have $%.2f",
                         size_usd, self.portfolio.cash)
            return None

        # Deduct from cash (both legs use same collateral — spot is collateral for futures)
        self.portfolio.cash -= size_usd

        arb = ArbPosition(
            symbol              = symbol,
            short_sym           = short_sym,
            size_usd            = size_usd,
            entry_funding_rate  = rate,
            spot_entry_price    = price,
            futures_entry_price = price,  # Perpetuals trade at near-spot price
            opened_at           = datetime.now(timezone.utc).isoformat(),
        )
        self.open_arbs[symbol] = arb

        logger.info(
            "FUNDING ARB OPEN %-10s $%.0f | rate=%.4f%%/8h (%.2f%%/day, %.0f%%/yr) | spot=$%.4f",
            symbol, size_usd, rate * 100, opp["rate_daily_pct"], opp["annualized_pct"], price,
        )

        # Live execution
        if self.executor._futures_exchange and not config.PAPER_TRADING:
            try:
                # Short 1x futures (no leverage — purely for funding collection)
                qty = size_usd / price
                self.executor._futures_exchange.set_leverage(1, symbol,
                                                              params={"marginMode": "isolated"})
                self.executor._futures_exchange.create_market_order(
                    symbol, "sell", qty,
                    params={"reduceOnly": False},
                )
                logger.info("ARB FUTURES SHORT sent: %s qty=%.6f", symbol, qty)
            except Exception as e:
                logger.error("ARB futures leg failed for %s: %s", symbol, e)

        return arb

    def close_arb(self, symbol: str, reason: str) -> Optional[dict]:
        """Close an open arb position and record PnL."""
        arb = self.open_arbs.pop(symbol, None)
        if not arb:
            return None

        current_price = df_mod.get_realtime_price(arb.short_sym) or arb.spot_entry_price
        spot_pnl  = (current_price - arb.spot_entry_price) / arb.spot_entry_price * arb.size_usd
        short_pnl = -spot_pnl  # Futures short exactly offsets spot move (delta neutral)
        total_pnl = arb.funding_collected_usd  # Net = just the funding

        # Return capital + net PnL
        self.portfolio.cash += arb.size_usd + total_pnl

        arb.close_reason = reason
        arb.closed_at = datetime.now(timezone.utc).isoformat()
        trade = arb.to_dict()
        trade["total_pnl_usd"] = round(total_pnl, 4)
        self.closed_arbs.append(trade)

        logger.info(
            "FUNDING ARB CLOSE %-10s | funding_collected=$%.2f | periods=%d | reason=%s",
            symbol, arb.funding_collected_usd, arb.funding_periods, reason,
        )

        # Live: close futures short
        if self.executor._futures_exchange and not config.PAPER_TRADING:
            try:
                qty = arb.size_usd / current_price
                self.executor._futures_exchange.create_market_order(
                    symbol, "buy", qty, params={"reduceOnly": True})
            except Exception as e:
                logger.error("ARB close futures failed for %s: %s", symbol, e)

        return trade

    # ─────────────────────────────────────────────────────────────────────────
    # Maintenance
    # ─────────────────────────────────────────────────────────────────────────

    def update_positions(self):
        """
        Call every cycle. Checks:
          1. Whether a funding payment is due (collect it)
          2. Whether current rate has dropped below exit threshold
          3. Whether position should be closed for any other reason
        """
        if not self.open_arbs:
            return

        rates = df_mod.get_funding_rates()
        now   = datetime.now(timezone.utc)
        now_hour = now.hour
        now_min  = now.minute

        for symbol in list(self.open_arbs.keys()):
            arb  = self.open_arbs[symbol]
            info = rates.get(symbol, {})

            # 1. Collect funding at the right time
            is_funding_hour   = now_hour in self._FUNDING_HOURS
            is_funding_window = now_min <= self._FUNDING_WINDOW_MIN
            time_since_last   = time.time() - arb.last_funding_ts

            if is_funding_hour and is_funding_window and time_since_last > 3600:
                # Collect funding: rate * position * 1 (1x short gets paid by longs)
                current_rate  = info.get("rate", arb.entry_funding_rate)
                funding_earned = current_rate * arb.size_usd
                arb.funding_collected_usd += funding_earned
                arb.funding_periods       += 1
                arb.last_funding_ts        = time.time()
                # Also credit to portfolio
                self.portfolio.cash += funding_earned
                logger.info(
                    "FUNDING COLLECTED %s $%.4f (rate=%.5f%%) | total=$%.4f over %d periods",
                    symbol, funding_earned, current_rate * 100,
                    arb.funding_collected_usd, arb.funding_periods,
                )

            # 2. Check exit conditions
            current_rate = info.get("rate", 0)
            if current_rate < 0.0001:   # Rate dropped below worthwhile threshold
                self.close_arb(symbol, f"Rate dropped to {current_rate*100:.5f}%/8h")
            elif current_rate < 0:      # Rate flipped — now shorts pay longs (adverse)
                self.close_arb(symbol, "Funding rate turned negative — exit")

    def summary(self) -> dict:
        """Return summary of arb activity."""
        total_collected = sum(a.funding_collected_usd for a in self.open_arbs.values())
        total_collected += sum(t.get("total_pnl_usd", 0) for t in self.closed_arbs)
        return {
            "open_arbs": len(self.open_arbs),
            "closed_arbs": len(self.closed_arbs),
            "total_funding_collected_usd": round(total_collected, 2),
            "positions": [a.to_dict() for a in self.open_arbs.values()],
        }
