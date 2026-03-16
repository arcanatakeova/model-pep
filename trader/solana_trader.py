"""
SolanaTrader — Standalone autonomous Solana DEX memecoin trading system.
========================================================================
Extracted from the monolithic main.py into an independent trader that can
run standalone or be managed by the orchestrator.

Handles:
  - DEX token discovery (DexScreener + Birdeye)
  - Token safety checks (rug detection, honeypot)
  - Position management (open, partial TP, stop-loss, time exits)
  - Jupiter DEX execution with MEV protection
  - Real-time price monitoring (Birdeye multi-price)
  - Wallet reconciliation and drift detection

Usage (standalone):
  python solana_trader.py                # Live mode (default)
  python solana_trader.py --paper        # Paper trading
  python solana_trader.py --scan         # One-shot scan, exit
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import config
from core.base_trader import BaseTrader
from core.state_manager import StateManager
from portfolio import Portfolio
from risk_manager import RiskManager
from compounding_engine import CompoundingEngine
from dex_screener import DexScreener
from solana_wallet import SolanaWallet, SOL_MINT, USDC_MINT, USDT_MINT
from token_safety import TokenSafetyChecker

logger = logging.getLogger(__name__)


class SolanaTrader(BaseTrader):
    """
    Autonomous Solana DEX memecoin trader.

    Scans for new token opportunities, validates safety, executes swaps
    via Jupiter with Jito MEV protection, and manages positions with
    dynamic stop-loss, partial profit-taking, and time-based exits.
    """

    @property
    def name(self) -> str:
        return "solana"

    @property
    def scan_interval_sec(self) -> float:
        return config.DEX_SCAN_INTERVAL_SEC

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager,
                 state_manager: StateManager, live: bool = True,
                 compounder: CompoundingEngine = None):
        super().__init__(portfolio, risk_manager, state_manager, live)
        self.compounder = compounder or CompoundingEngine(portfolio, risk_manager)

        # Solana-specific components (initialized in _init_components)
        self.solana: Optional[SolanaWallet] = None
        self.dex_screener: Optional[DexScreener] = None
        self.safety_checker: Optional[TokenSafetyChecker] = None

        # Position tracking
        self._dex_positions: dict = {}
        self._dex_lock = threading.Lock()
        self._wallet_balance_cache = (0.0, 0.0, 0.0, 0.0)  # sol, usdc, sol_usd, ts

        # Day boundary tracking
        self._day_start_eq = 0.0
        self._last_day = datetime.now(timezone.utc).date()

    def _init_components(self):
        """Initialize Solana wallet, screener, and safety checker."""
        self.solana = SolanaWallet(private_key_b58=config.PHANTOM_PRIVATE_KEY)
        self.dex_screener = DexScreener()
        self.safety_checker = TokenSafetyChecker()

        # Load persisted positions
        self._load_dex_positions()

        # Sync with wallet on live start
        if self.live and self.solana.is_connected:
            self._sync_initial_wallet()
            self._reconcile_wallet_positions()

        self._day_start_eq = self.portfolio.equity()

        # Start background threads
        if self.live and self.solana.is_connected:
            self._start_background_thread(self._fast_price_monitor, "FastPriceMonitor")
            self._start_background_thread(self._live_wallet_sync, "WalletSync")

        logger.info("[solana] Initialized | wallet=%s | positions=%d",
                    self.solana.pubkey if self.solana.is_connected else "disconnected",
                    len(self._dex_positions))

    def _run_cycle(self):
        """One full Solana DEX scan/trade/exit cycle."""
        now_dt = datetime.now(timezone.utc)

        # Day boundary reset
        today = now_dt.date()
        if today != self._last_day:
            self._last_day = today
            self.risk_mgr.reset_daily_loss_tracker()
            self._day_start_eq = self._compute_equity()
            logger.info("[solana] Daily reset: equity=$%.2f", self._day_start_eq)

        # 1. Update existing positions (price refresh, exits, partial TPs)
        self._update_dex_positions()

        # 2. Scan for new opportunities
        self._run_dex_scan()

        # 3. Report state
        equity = self._compute_equity()
        self.state_mgr.append_equity(equity, self._cycle)
        self._report_state(equity)

        # 4. Periodic saves
        if self._cycle % 60 == 0:  # Every ~4 minutes at 4s intervals
            self.portfolio.save()
            self._save_dex_positions()
            self.state_mgr.save_equity_curve()

    def _cleanup(self):
        """Save state on shutdown."""
        self.portfolio.save()
        self._save_dex_positions()
        self.state_mgr.save_equity_curve()
        logger.info("[solana] State saved, %d open positions",
                    len(self._dex_positions))

    def get_status(self) -> dict:
        """Return current Solana trader status."""
        with self._dex_lock:
            positions = list(self._dex_positions.values())
        equity = self._compute_equity()
        _sol, _usdc, _sol_usd, _ = self._wallet_balance_cache
        return {
            "running": self.running,
            "cycle": self._cycle,
            "last_cycle_ms": round(self._last_cycle_ms, 1),
            "equity": round(equity, 2),
            "positions": len(positions),
            "wallet_connected": self.solana.is_connected if self.solana else False,
            "wallet_address": str(self.solana.pubkey) if self.solana and self.solana.is_connected else "",
            "wallet_sol": round(_sol, 4),
            "wallet_usdc": round(_usdc, 2),
            "wallet_sol_usd": round(_sol_usd, 2),
            "daily_pnl_usd": round(equity - self._day_start_eq, 2),
            "dex_position_details": self._snapshot_dex_positions(),
        }

    # ── Core Trading Logic ──────────────────────────────────────────────────

    def _run_dex_scan(self):
        """DEX scan — multi-source discovery, safety checks, execution."""
        try:
            tokens = self.dex_screener.get_multi_chain_opportunities()
            if not tokens:
                return

            budget = self.compounder.max_position_for_market("crypto_dex")
            traded = 0

            held_addrs = set(self._dex_positions.keys())
            held_symbols = {pos.get("symbol", "").upper()
                            for pos in self._dex_positions.values()}

            candidates = []
            for token in tokens:
                if token.score < config.DEX_MIN_SCORE:
                    break
                if token.pair_address in held_addrs:
                    continue
                if token.base_symbol.upper() in held_symbols:
                    continue
                if token.base_address in {p.get("address", "") for p in self._dex_positions.values()}:
                    continue
                candidates.append(token)
                if len(candidates) >= 12:
                    break

            logger.info("[solana] DEX scan: %d total, %d candidates above %.2f",
                        len(tokens), len(candidates), config.DEX_MIN_SCORE)

            # Pre-filter: concentration + safety
            prefiltered = []
            for token in candidates:
                allowed, reason = self.risk_mgr.check_dex_concentration(
                    self._dex_positions, token.dex_id)
                if not allowed:
                    continue
                safety = getattr(token, "safety_report", None)
                if safety and not safety.is_safe_to_trade:
                    logger.warning("[solana] SKIP %s: %s", token.base_symbol, safety.risk_level)
                    continue
                safety_score = safety.safety_score if safety else 0.5
                size_usd = self.risk_mgr.dex_position_size_usd(
                    token_score=token.score,
                    safety_score=safety_score,
                    liquidity_usd=token.liquidity_usd,
                    price_change_h1=token.price_change_h1,
                    price_change_h6=token.price_change_h6,
                )
                size_usd = min(size_usd, config.DEX_MAX_POSITION_USD)
                if size_usd < config.DEX_MIN_POSITION_USD:
                    continue
                prefiltered.append((token, size_usd, safety))

            # Parallel validation
            def _validate(args):
                tok, sz, sfty = args
                if sfty and sfty.holder_count and 0 < sfty.holder_count < 20:
                    return None
                return (tok, sz, sfty)

            validated = []
            if prefiltered:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(len(prefiltered), 20),
                        thread_name_prefix="dex-validate") as ex:
                    futs = [ex.submit(_validate, args) for args in prefiltered]
                    for f in concurrent.futures.as_completed(futs, timeout=15):
                        try:
                            result = f.result()
                            if result is not None:
                                validated.append(result)
                        except Exception:
                            pass
                validated.sort(key=lambda x: x[0].score, reverse=True)

            for token, size_usd, safety in validated:
                if traded >= 8:
                    break
                self._open_dex_position(token, size_usd, safety)
                held_symbols.add(token.base_symbol.upper())
                held_addrs.add(token.pair_address)
                traded += 1

        except Exception as e:
            logger.warning("[solana] DEX scan error: %s", e)

    def _open_dex_position(self, token, size_usd: float, safety=None):
        """Open a DEX position with safety verification and MEV protection."""
        try:
            if not token.pair_address:
                return

            if size_usd > self.portfolio.cash * 0.99:
                logger.warning("[solana] Insufficient cash $%.2f for $%.2f buy",
                               self.portfolio.cash, size_usd)
                return

            # Verify SOL balance
            if token.chain_id == "solana" and self.solana.is_connected:
                _sol, _usdc, _sol_usd, _cache_ts = self._wallet_balance_cache
                sol_bal_usd = _sol_usd if (time.time() - _cache_ts) < 10 else self.portfolio.cash
                if sol_bal_usd < size_usd * 1.05:
                    logger.warning("[solana] SOL balance $%.2f insufficient for $%.2f",
                                   sol_bal_usd, size_usd)
                    return

            safety_score = safety.safety_score if safety else 0.5
            stop_pct = self.risk_mgr.dynamic_dex_stop_pct(
                price_change_h1=getattr(token, "price_change_h1", 0) or 0,
                price_change_h6=getattr(token, "price_change_h6", 0) or 0,
                price_change_h24=getattr(token, "price_change_h24", 0) or 0,
                safety_score=safety_score,
            )
            target_pct = self.risk_mgr.dynamic_dex_target_pct(
                price_change_h24=getattr(token, "price_change_h24", 0) or 0,
                score=token.score,
            )

            pos_data = {
                "symbol": token.base_symbol,
                "address": token.base_address,
                "chain": token.chain_id,
                "dex_id": token.dex_id,
                "entry_price": token.price_usd,
                "current_price": token.price_usd,
                "size_usd": size_usd,
                "remaining_fraction": 1.0,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "stop_pct": stop_pct,
                "target_pct": target_pct,
                "score": token.score,
                "safety_score": safety.safety_score if safety else None,
                "risk_level": safety.risk_level if safety else None,
                "signals": token.signals,
                "partial_profits_taken": [],
                "peak_price": token.price_usd,
                "current_pnl_pct": 0.0,
                "liquidity_usd": token.liquidity_usd,
            }

            if token.chain_id == "solana" and self.solana.is_connected:
                tx = self.solana.safe_buy_token(
                    token.base_address, size_usd,
                    safety_report=safety,
                    liquidity_usd=token.liquidity_usd,
                    pair_address=token.pair_address if token.dex_id == "pumpswap" else None)
                if tx and not tx.startswith("paper_"):
                    pos_data["tx"] = tx
                    # Parse on-chain fill price
                    try:
                        fill = self.solana.get_transaction_token_change(
                            tx, token.base_address)
                        if fill.get("price_usd", 0) > 0:
                            pos_data["entry_price"] = fill["price_usd"]
                            pos_data["current_price"] = fill["price_usd"]
                            pos_data["peak_price"] = fill["price_usd"]
                            if fill.get("tokens_received"):
                                pos_data["qty"] = fill["tokens_received"]
                            if fill.get("sol_spent"):
                                pos_data["sol_spent"] = fill["sol_spent"]
                    except Exception:
                        pass
                    # Fallback: Birdeye price
                    if pos_data["entry_price"] == token.price_usd and config.BIRDEYE_API_KEY:
                        try:
                            from dex_screener import _get_birdeye
                            be = _get_birdeye()
                            if be:
                                bp = be.get_price(token.base_address)
                                if bp and bp.price_usd > 0:
                                    pos_data["entry_price"] = bp.price_usd
                                    pos_data["current_price"] = bp.price_usd
                                    pos_data["peak_price"] = bp.price_usd
                        except Exception:
                            pass
                    with self._dex_lock:
                        self._dex_positions[token.pair_address] = pos_data
                    self.portfolio.cash -= size_usd
                    risk_str = safety.risk_level if safety else "?"
                    logger.info("[solana] BUY %s $%.2f @ $%.8f score=%.2f safety=%s | tx=%s",
                                token.base_symbol, size_usd, pos_data["entry_price"],
                                token.score, risk_str, tx[:16])
            else:
                pos_data["tx"] = f"paper_{int(time.time())}"
                with self._dex_lock:
                    self._dex_positions[token.pair_address] = pos_data
                self.portfolio.cash -= size_usd
                logger.info("[solana] PAPER BUY %s $%.2f @ $%.8f score=%.2f",
                            token.base_symbol, size_usd, token.price_usd, token.score)

        except Exception as e:
            logger.warning("[solana] Position open failed (%s): %s",
                           token.base_symbol, e)

    def _update_dex_positions(self):
        """Check open DEX positions for exits, partial profits, stop/TP."""
        for pair_addr, pos in list(self._dex_positions.items()):
            try:
                if pair_addr not in self._dex_positions:
                    continue

                token = self.dex_screener.get_token_info(pos["address"], pos["chain"])
                if not token or token.price_usd <= 0:
                    continue

                entry = pos["entry_price"]
                current = token.price_usd
                pnl_pct = (current - entry) / entry if entry > 0 else 0
                pos["current_price"] = current
                pos["current_pnl_pct"] = pnl_pct
                if token.liquidity_usd > 0:
                    pos["liquidity_usd"] = token.liquidity_usd

                pos["peak_price"] = max(pos.get("peak_price", entry), current)
                peak = pos["peak_price"]
                trail_pct = (peak - current) / peak if peak > 0 else 0

                # Spike exit
                prev_price = pos.get("prev_price", entry)
                if prev_price > 0 and pnl_pct > 0:
                    cycle_surge = (current - prev_price) / prev_price
                    if cycle_surge >= 0.15:
                        self._try_close_dex_position(
                            pair_addr, pos, current,
                            f"Spike exit: +{cycle_surge:.0%} (PnL +{pnl_pct:.0%})")
                        continue
                pos["prev_price"] = current

                # Time exits
                should_exit, time_reason = self.risk_mgr.check_time_exit(pos)
                if should_exit:
                    self._try_close_dex_position(pair_addr, pos, current, time_reason)
                    continue

                # Dust cleanup
                if pos.get("remaining_fraction", 1.0) < 0.02:
                    self._try_close_dex_position(
                        pair_addr, pos, current, "Dust cleanup (<2% remaining)")
                    continue

                # Partial profit-taking
                sell_frac, partial_reason, partial_threshold = \
                    self.risk_mgr.get_partial_profit_action(pos)
                if sell_frac is not None:
                    self._execute_partial_profit(
                        pair_addr, pos, sell_frac, partial_reason, partial_threshold)

                # Full exit conditions
                remaining = pos.get("remaining_fraction", 1.0)
                adj_target = pos["target_pct"] * (1 + (1 - remaining) * 0.5)

                # Momentum extension
                m5_chg = pos.get("price_change_m5", 0)
                if pnl_pct > 0.15 and abs(m5_chg) > 5:
                    adj_target *= 1.30

                pos_stop = pos.get("stop_pct", 0.20)
                trail_thr = pos_stop * 0.80
                profit_thr = pos_stop * 0.50
                reason = None
                if pnl_pct >= adj_target:
                    reason = f"Take profit +{pnl_pct:.0%}"
                elif pnl_pct <= -pos_stop:
                    reason = f"Stop loss {pnl_pct:.0%}"
                elif trail_pct > trail_thr and pnl_pct > profit_thr:
                    reason = f"Trailing stop (peak=${peak:.8f}, trail={trail_pct:.0%})"

                if reason:
                    self._try_close_dex_position(pair_addr, pos, current, reason)

            except Exception as e:
                logger.debug("[solana] Position update error: %s", e)

    def _try_close_dex_position(self, pair_addr: str, pos: dict,
                                current_price: float, reason: str) -> bool:
        """Atomically claim and close a position. Thread-safe."""
        with self._dex_lock:
            if pair_addr not in self._dex_positions:
                return False
            self._dex_positions.pop(pair_addr)
        result = self._close_dex_position(pair_addr, pos, current_price, reason)
        if result is False:
            with self._dex_lock:
                if pair_addr not in self._dex_positions:
                    self._dex_positions[pair_addr] = pos
        return result

    def _close_dex_position(self, pair_addr: str, pos: dict,
                            current_price: float, reason: str) -> bool:
        """Close a DEX position. Returns False if on-chain sell failed."""
        entry = pos["entry_price"]
        remaining = pos.get("remaining_fraction", 1.0)
        size = pos["size_usd"] * remaining
        pnl_pct = (current_price - entry) / entry if entry > 0 else 0
        pnl_usd = size * pnl_pct
        proceeds = max(size + pnl_usd, 0.0)

        liq_usd = pos.get("liquidity_usd", 0.0)
        if pos["chain"] == "solana" and self.solana.is_connected and "paper" not in pos.get("tx", ""):
            sell_result = self.solana.sell_token(
                pos["address"], proceeds,
                liquidity_usd=liq_usd,
                pair_address=pair_addr if pos.get("dex_id") == "pumpswap" else None)
            if sell_result:
                _sig, actual_usd = sell_result
                self.portfolio.cash += actual_usd
                pnl_usd = actual_usd - size
                pnl_pct = pnl_usd / size if size > 0 else 0
                if pos.get("qty", 0) > 0:
                    current_price = actual_usd / pos["qty"] if pos["qty"] > 0 else current_price
            else:
                logger.error("[solana] SELL FAILED %s — keeping for retry", pos["symbol"])
                return False
        else:
            self.portfolio.cash += proceeds

        sign = "+" if pnl_usd >= 0 else ""
        logger.info("[solana] CLOSE %s %s%.2f%% ($%s%.2f) | %s",
                    pos["symbol"], sign, pnl_pct * 100, sign, pnl_usd, reason)

        trade_record = {
            "asset_id": pair_addr,
            "symbol": pos["symbol"],
            "market": "dex",
            "side": "long",
            "entry_price": entry,
            "exit_price": current_price,
            "qty": pos.get("qty", 0),
            "size_usd": pos.get("size_usd", 0),
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct * 100, 2),
            "close_reason": reason,
            "chain": pos.get("chain", "solana"),
            "safety_score": pos.get("safety_score"),
            "opened_at": pos["opened_at"],
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.portfolio.closed_trades.append(trade_record)

        # Supabase persistence
        try:
            import secrets_manager as _sm
            _sm.persist_trade(trade_record)
        except Exception:
            pass

        from portfolio import _MAX_CLOSED_TRADES_MEMORY
        if len(self.portfolio.closed_trades) > _MAX_CLOSED_TRADES_MEMORY:
            self.portfolio._archive_old_trades()
        return True

    def _execute_partial_profit(self, pair_addr: str, pos: dict,
                                fraction: float, reason: str,
                                threshold_pct: float = 0):
        """Execute a partial profit-take on a DEX position."""
        try:
            size_usd = pos.get("size_usd", 0)
            if size_usd <= 0:
                pos["partial_profits_taken"].append(threshold_pct)
                return

            remaining = pos.get("remaining_fraction", 1.0)
            actual_frac = fraction * remaining
            pnl_pct = pos.get("current_pnl_pct", 0)
            proceeds = size_usd * actual_frac * (1 + pnl_pct)

            liq_usd = pos.get("liquidity_usd", 0.0)
            if (pos["chain"] == "solana" and self.solana.is_connected
                    and "paper" not in pos.get("tx", "")):
                sell_result = self.solana.sell_token_partial(
                    pos["address"], fraction,
                    liquidity_usd=liq_usd,
                    pair_address=pair_addr if pos.get("dex_id") == "pumpswap" else None)
                if not sell_result:
                    return
                _sig, actual_usd = sell_result
                self.portfolio.cash += actual_usd
            else:
                self.portfolio.cash += proceeds

            pos["remaining_fraction"] = remaining - actual_frac
            pos["partial_profits_taken"].append(threshold_pct)
            logger.info("[solana] PARTIAL TP %s: sold %.0f%% ($%.2f) | %s",
                        pos["symbol"], fraction * 100, proceeds, reason)
        except Exception as e:
            logger.warning("[solana] Partial profit failed for %s: %s",
                           pos.get("symbol", "?"), e)

    # ── Background Threads ──────────────────────────────────────────────────

    def _fast_price_monitor(self):
        """Background: polls Birdeye every 3s for held token prices."""
        from dex_screener import _get_birdeye
        while self.running:
            try:
                time.sleep(3)
                if not self._dex_positions:
                    continue
                be = _get_birdeye()
                if not be or not be.enabled:
                    continue

                mints = [pos["address"] for pos in self._dex_positions.values()
                         if pos.get("chain") == "solana"]
                if not mints:
                    continue

                prices = be.get_multi_price(mints)

                to_close = []
                for pair_addr, pos in list(self._dex_positions.items()):
                    mint = pos.get("address")
                    bp = prices.get(mint)
                    if not bp or bp.price_usd <= 0:
                        continue

                    entry = pos["entry_price"]
                    current = bp.price_usd
                    pnl_pct = (current - entry) / entry if entry > 0 else 0
                    pos["current_price"] = current
                    pos["current_pnl_pct"] = pnl_pct
                    pos["peak_price"] = max(pos.get("peak_price", entry), current)

                    prev = pos.get("fast_prev_price", entry)
                    pos["fast_prev_price"] = current
                    if prev > 0:
                        pos["price_change_m5"] = (current - prev) / prev * 100

                    # Spike capture (only when already well in profit)
                    if prev > 0 and pnl_pct > 0.25 and current > prev:
                        surge = (current - prev) / prev
                        if surge >= 0.15:
                            to_close.append((
                                pair_addr, pos, current,
                                f"FastMonitor spike: +{surge:.0%} (PnL +{pnl_pct:.0%})"))
                            continue

                    # Reversal from peak
                    peak = pos.get("peak_price", entry)
                    if peak > entry and pnl_pct > 0:
                        reversal = (peak - current) / peak
                        reversal_threshold = min(0.18 + pnl_pct * 0.07, 0.30)
                        if reversal >= reversal_threshold and pnl_pct > 0.08:
                            to_close.append((
                                pair_addr, pos, current,
                                f"FastMonitor reversal: -{reversal:.0%} from peak (PnL +{pnl_pct:.0%})"))
                            continue

                    # Volume dry-up
                    if bp.volume_24h_usd > 0:
                        entry_vol = pos.get("entry_volume_24h", 0)
                        if entry_vol <= 0:
                            pos["entry_volume_24h"] = bp.volume_24h_usd
                        else:
                            vol_drop = (entry_vol - bp.volume_24h_usd) / entry_vol
                            prev_drop = pos.get("prev_vol_drop", 0.0)
                            pos["prev_vol_drop"] = vol_drop
                            if vol_drop > 0.60 and prev_drop > 0.60 and pnl_pct > 0:
                                to_close.append((
                                    pair_addr, pos, current,
                                    f"FastMonitor vol dry-up: -{vol_drop:.0%} (PnL +{pnl_pct:.0%})"))
                                continue

                    # Instant stop-loss
                    stop_pct = pos.get("stop_pct", 0.20)
                    if pnl_pct <= -stop_pct:
                        to_close.append((
                            pair_addr, pos, current,
                            f"FastMonitor stop-loss {pnl_pct:.0%}"))

                for pair_addr, pos, current, reason in to_close:
                    logger.info("[solana] FAST EXIT %s @ $%.8f | %s",
                                pos.get("symbol", "?"), current, reason)
                    self._try_close_dex_position(pair_addr, pos, current, reason)

            except Exception as e:
                logger.debug("[solana] FastPriceMonitor error: %s", e)

    def _live_wallet_sync(self):
        """Background: syncs SOL balance and reconciles positions every 5s."""
        _last_reconcile = 0.0
        while self.running:
            try:
                time.sleep(5)
                if not self.live or not self.solana.is_connected:
                    continue

                # Refresh SOL balance
                sol_raw = self.solana.get_sol_balance()
                sol_price = self.solana._get_sol_price()
                sol_usd = sol_raw * sol_price if sol_price > 0 else 0.0
                if sol_usd > 0:
                    with self._dex_lock:
                        _positions = list(self._dex_positions.values())
                    dex_deployed = sum(
                        pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
                        for pos in _positions
                        if pos.get("chain") == "solana"
                    )
                    available = sol_usd - dex_deployed
                    if available > 0:
                        cash_diff = available - self.portfolio.cash
                        if cash_diff > 2.0 or cash_diff < -10.0:
                            self.portfolio.cash = available
                    try:
                        _usdc = self.solana.get_usdc_balance()
                    except Exception:
                        _usdc = self._wallet_balance_cache[1]
                    self._wallet_balance_cache = (sol_raw, _usdc, sol_usd, time.time())

                # Token reconciliation (every 30s)
                now_ts = time.time()
                if now_ts - _last_reconcile > 30 and self._dex_positions:
                    _last_reconcile = now_ts
                    self._reconcile_on_chain()

            except Exception as e:
                logger.debug("[solana] WalletSync error: %s", e)

    # ── Wallet Helpers ──────────────────────────────────────────────────────

    def _sync_initial_wallet(self):
        """Sync portfolio cash with Phantom wallet on startup."""
        try:
            sol_bal = self.solana.get_sol_balance()
            sol_usd = self.solana.get_portfolio_value_usd()
            if sol_usd > 0.50:
                self.portfolio.cash = sol_usd
                self.portfolio.initial_capital = sol_usd
                self.portfolio.peak_equity = sol_usd
                self.risk_mgr.reset_daily_loss_tracker()
                logger.info("[solana] Wallet synced: SOL=%.4f ($%.2f)", sol_bal, sol_usd)
        except Exception as e:
            logger.warning("[solana] Wallet sync failed: %s", e)

    def _reconcile_wallet_positions(self):
        """Startup: compare on-chain balances with tracked positions."""
        if not self.live or not self.solana.is_connected:
            return
        try:
            on_chain = self.solana.get_all_token_balances()
            if not on_chain:
                return
            on_chain_mints = set(on_chain.keys())
            _skip = {SOL_MINT, USDC_MINT, USDT_MINT}
            tracked_mints = {pos.get("address", "") for pos in self._dex_positions.values()}

            ghost = 0
            for pair_addr, pos in list(self._dex_positions.items()):
                mint = pos.get("address", "")
                if not mint or mint in _skip:
                    continue
                if mint not in on_chain_mints:
                    ghost += 1
                    logger.warning("[solana] RECONCILE: %s tracked but no on-chain balance",
                                   pos.get("symbol", "?"))

            untracked = on_chain_mints - tracked_mints - _skip
            for mint in untracked:
                bal = on_chain[mint]
                logger.info("[solana] RECONCILE: untracked token %s… balance=%.6f",
                            mint[:12], bal["ui_amount"])

            logger.info("[solana] Reconciliation: %d tracked, %d ghost, %d untracked",
                        len(tracked_mints - {""}), ghost, len(untracked))
        except Exception as e:
            logger.debug("[solana] Reconciliation error: %s", e)

    def _reconcile_on_chain(self):
        """Remove positions whose on-chain balance is zero."""
        try:
            on_chain = self.solana.get_all_token_balances()
            if not on_chain:
                return
            for pair_addr, pos in list(self._dex_positions.items()):
                mint = pos.get("address", "")
                if not mint or mint in {SOL_MINT, USDC_MINT, USDT_MINT}:
                    continue
                if pos.get("chain") != "solana":
                    continue
                if mint not in on_chain:
                    entry = pos.get("entry_price", 0)
                    size = pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
                    current = pos.get("current_price", entry)
                    symbol = pos.get("symbol", "?")
                    logger.warning("[solana] SYNC: %s zero on-chain — removed", symbol)
                    with self._dex_lock:
                        self._dex_positions.pop(pair_addr, None)
                    proceeds = max(size * (current / entry) if entry > 0 else size, 0.0)
                    self.portfolio.cash += proceeds
                    self.portfolio.closed_trades.append({
                        "asset_id": pair_addr,
                        "symbol": symbol,
                        "side": "long",
                        "entry_price": entry,
                        "exit_price": current,
                        "pnl_usd": round(proceeds - size, 4),
                        "pnl_pct": round((current / entry - 1) * 100, 2) if entry > 0 else 0,
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "reason": "externally_closed",
                        "chain": "solana",
                    })
        except Exception as e:
            logger.debug("[solana] On-chain reconcile error: %s", e)

    # ── State Persistence ───────────────────────────────────────────────────

    def _save_dex_positions(self):
        """Persist open DEX positions to disk."""
        try:
            with self._dex_lock:
                data = dict(self._dex_positions)
            StateManager._atomic_json("dex_positions.json", data, indent=2)
        except Exception as e:
            logger.warning("[solana] Failed to save positions: %s", e)

    def _load_dex_positions(self):
        """Load DEX positions from disk with validation."""
        try:
            with open("dex_positions.json") as f:
                raw = json.load(f)
            validated = {}
            for pair_addr, pos in raw.items():
                if not isinstance(pos, dict):
                    continue
                entry = float(pos.get("entry_price", 0))
                size = float(pos.get("size_usd", 0))
                if entry <= 0 or size <= 0:
                    continue
                validated[pair_addr] = pos
            with self._dex_lock:
                self._dex_positions = validated
            if validated:
                logger.info("[solana] Restored %d positions", len(validated))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("[solana] Failed to load positions: %s", e)

    def _compute_equity(self) -> float:
        """Compute total equity: cash + DEX position values."""
        with self._dex_lock:
            positions = list(self._dex_positions.values())
        dex_value = sum(
            pos.get("size_usd", 0) * pos.get("remaining_fraction", 1.0)
            * (1 + pos.get("current_pnl_pct", 0))
            for pos in positions
        )
        return self.portfolio.cash + dex_value

    def _snapshot_dex_positions(self) -> list:
        """Thread-safe snapshot of DEX positions for dashboard."""
        with self._dex_lock:
            positions = list(self._dex_positions.values())
        return [
            {
                "symbol": pos.get("symbol", "?"),
                "address": pos.get("address", ""),
                "size_usd": round(pos.get("size_usd", 0), 2),
                "entry": pos.get("entry_price", 0),
                "current": pos.get("current_price", pos.get("entry_price", 0)),
                "pnl_pct": round(pos.get("current_pnl_pct", 0) * 100, 2),
                "remaining": round(pos.get("remaining_fraction", 1.0) * 100, 1),
                "chain": pos.get("chain", "solana"),
            }
            for pos in positions
        ]

    def _report_state(self, equity: float):
        """Report state to the state manager for dashboard."""
        risk = self.risk_mgr.risk_report()
        self.state_mgr.update_trader_state(self.name, {
            **self.get_status(),
            "risk": risk,
        })

    # ── Manual Close (dashboard command) ────────────────────────────────────

    def close_position(self, pair_addr: str, reason: str = "Manual close"):
        """Close a specific position by pair address (called by orchestrator)."""
        pos = self._dex_positions.get(pair_addr)
        if pos:
            current = pos.get("current_price") or pos.get("entry_price", 0)
            self._try_close_dex_position(pair_addr, pos, current, reason)


# ── Standalone entry point ──────────────────────────────────────────────────

def main():
    """Run the Solana trader as a standalone program."""
    import argparse
    import signal as sig_mod

    # Load environment
    import os as _os, pathlib as _pathlib
    def _parse_env(path):
        if not path.exists():
            return
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    _os.environ[k] = v

    here = _pathlib.Path(__file__).resolve().parent
    root = here.parent
    _parse_env(root / ".env")
    _parse_env(here / ".env")

    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
        load_dotenv(here / ".env", override=True)
    except ImportError:
        pass

    try:
        from secrets_manager import load_secrets
        load_secrets()
    except Exception:
        pass

    from core.logging_setup import setup_logging
    setup_logging(config.LOG_FILE, config.LOG_LEVEL)

    parser = argparse.ArgumentParser(description="Solana DEX Autonomous Trader")
    parser.add_argument("--paper", action="store_true", help="Paper trading mode")
    parser.add_argument("--scan", action="store_true", help="One-shot scan, exit")
    args = parser.parse_args()

    live = not args.paper
    config.PAPER_TRADING = not live

    portfolio = Portfolio(config.INITIAL_CAPITAL)
    risk_mgr = RiskManager(portfolio)
    state_mgr = StateManager()
    compounder = CompoundingEngine(portfolio, risk_mgr)

    if args.scan:
        # One-shot scan
        screener = DexScreener()
        tokens = screener.get_multi_chain_opportunities()[:10]
        print(f"\nSolana DEX — Top {len(tokens)} tokens:\n")
        for t in tokens:
            age = f"{t.age_hours:.0f}h" if t.age_hours else "?"
            print(f"  {t.base_symbol:<12} score={t.score:.3f} "
                  f"+{t.price_change_h1:.1f}%/1h vol=${t.volume_h1:,.0f} "
                  f"liq=${t.liquidity_usd:,.0f} age={age}")
        return

    trader = SolanaTrader(portfolio, risk_mgr, state_mgr,
                          live=live, compounder=compounder)

    def shutdown(*_):
        trader.stop()

    sig_mod.signal(sig_mod.SIGINT, shutdown)
    sig_mod.signal(sig_mod.SIGTERM, shutdown)

    trader.start()


if __name__ == "__main__":
    main()
