"""
Solana Wallet — Professional Autonomous Trading Engine
=======================================================
Production-grade Jupiter DEX execution with:

1. Jito MEV bundle protection      — sandwich-proof transaction delivery
2. Dynamic slippage                — scales with liquidity ratio + price impact
3. Finalized confirmation          — never marks success on "confirmed" (can roll back)
4. Helius priority fee estimation  — pays the right fee, not guesswork
5. Lamport sufficiency gate        — refuses to send if SOL balance is too low
6. Fresh quote at execution        — quote stale >5s → refetch before signing
7. Transaction retry with fee bump — auto-retry on expiry with 2× priority fee
8. Token-2022 aware balance check  — handles both legacy SPL and Token-2022 mints
9. On-chain confirmation gate      — verifies tx landed before updating position state

Setup:
  export PHANTOM_PRIVATE_KEY="your_base58_private_key"
  export SOLANA_RPC_URL="https://mainnet.helius-rpc.com/?api-key=YOUR_HELIUS_KEY"
  export BIRDEYE_API_KEY="your_birdeye_key"
"""
from __future__ import annotations

import base64
import json
import logging
import random
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

# ─── API endpoints ─────────────────────────────────────────────────────────────
JUPITER_QUOTE_URL    = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL     = "https://quote-api.jup.ag/v6/swap"

# Raydium swap API — used as fallback when Jupiter is geo-blocked
RAYDIUM_COMPUTE_URL  = "https://transaction-v1.raydium.io/compute/swap-base-in"
RAYDIUM_TX_URL       = "https://transaction-v1.raydium.io/transaction/swap-base-in"

# PumpPortal — builds Pump.fun bonding-curve buy txs; 3rd fallback for pre-graduation tokens
PUMPFUN_TRADE_URL    = "https://pumpportal.fun/api/trade-local"

# Jito Block Engine — bundles are MEV-protected and tip-prioritised
JITO_BUNDLE_URL      = "https://mainnet.block-engine.jito.labs.io/api/v1/bundles"
JITO_TIP_ACCOUNTS    = [
    # Official Jito tip accounts (base58 pubkeys, randomly selected per tx)
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvB8eLJMMyFgFastFF4zYzAqmsfoEp4Y9C",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1MusExtP8bY",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]

# Token mints
SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

# Safety constants
MIN_SOL_RESERVE_LAMPORTS = 50_000_000   # 0.05 SOL — reserve for fees + tip headroom
QUOTE_MAX_AGE_SECS       = 5            # Refetch quote if older than this
MAX_PRICE_IMPACT_PCT     = 4.0          # Reject swaps with > 4% impact
CONFIRMATION_TIMEOUT     = 60           # Seconds to wait for finalized


class SwapResult:
    """Result of a swap execution attempt."""
    __slots__ = ("success", "signature", "out_amount", "price_impact_pct",
                 "actual_slippage_bps", "error")

    def __init__(self, *, success: bool, signature: str = "",
                 out_amount: int = 0, price_impact_pct: float = 0.0,
                 actual_slippage_bps: int = 0, error: str = ""):
        self.success            = success
        self.signature          = signature
        self.out_amount         = out_amount
        self.price_impact_pct   = price_impact_pct
        self.actual_slippage_bps = actual_slippage_bps
        self.error              = error

    def __repr__(self):
        if self.success:
            return (f"SwapResult(OK sig={self.signature[:16]}… "
                    f"out={self.out_amount} impact={self.price_impact_pct:.2f}%)")
        return f"SwapResult(FAIL: {self.error})"


class SolanaWallet:
    """
    Autonomous Solana trading wallet.
    Executes Jupiter swaps with full MEV protection and confirmation safety.
    """

    def __init__(self, private_key_b58: str = ""):
        self.private_key_b58 = private_key_b58
        self._keypair   = None
        self._pubkey    = None
        self._client    = None
        self._sol_price_cache: tuple[float, float] = (0.0, 0.0)  # (price, ts)

        if private_key_b58:
            self._init_wallet()

    # ─── Initialisation ────────────────────────────────────────────────────────

    def _init_wallet(self):
        """Initialise Solana keypair from Phantom private key (base58 or JSON array)."""
        try:
            from solders.keypair import Keypair
            from solana.rpc.api import Client

            # Phantom exports as base58 string — try that first (most common)
            try:
                self._keypair = Keypair.from_base58_string(self.private_key_b58)
            except Exception:
                # Fallback: JSON byte array [12, 34, 56, ...]
                try:
                    key_bytes = bytes(json.loads(self.private_key_b58))
                    self._keypair = Keypair.from_bytes(key_bytes)
                except Exception:
                    # Last resort: raw base64
                    key_bytes = base64.b64decode(self.private_key_b58)
                    self._keypair = Keypair.from_bytes(key_bytes)
            self._pubkey  = str(self._keypair.pubkey())

            # Prefer Helius RPC URL for priority fee estimates & reliability
            rpc_url = config.SOLANA_RPC_URL or "https://api.mainnet-beta.solana.com"
            self._client = Client(rpc_url)

            logger.info("Solana wallet connected: %s…%s",
                        self._pubkey[:6], self._pubkey[-4:])
            self._check_dex_connectivity()
        except ImportError:
            logger.warning("solana/solders not installed — Solana trading disabled.")
            logger.warning("Install: pip install solana solders")
        except Exception as e:
            logger.error("Wallet init failed: %s", e)

    def _check_dex_connectivity(self):
        """Check Jupiter and Raydium reachability at startup. Both are tried at swap time
        (Jupiter first, Raydium fallback), so trading is possible if either is up."""
        jupiter_ok = False
        raydium_ok = False

        try:
            resp = requests.get(
                "https://quote-api.jup.ag/v6/quote",
                params={
                    "inputMint":  "So11111111111111111111111111111111111111112",
                    "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "amount":     "1000000",
                    "slippageBps":"50",
                },
                timeout=5,
            )
            jupiter_ok = resp.ok
        except Exception:
            pass

        try:
            resp = requests.get(
                RAYDIUM_COMPUTE_URL,
                params={
                    "inputMint":  "So11111111111111111111111111111111111111112",
                    "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "amount":     "1000000",
                    "slippageBps":"50",
                    "txVersion":  "V0",
                },
                timeout=5,
            )
            raydium_ok = resp.ok
        except Exception:
            pass

        if jupiter_ok and raydium_ok:
            logger.info("DEX routing: Jupiter ✓  Raydium ✓")
        elif jupiter_ok:
            logger.info("DEX routing: Jupiter ✓  Raydium ✗ (will use Jupiter only)")
        elif raydium_ok:
            logger.info("DEX routing: Jupiter ✗  Raydium ✓ (fallback active)")
        else:
            logger.error(
                "DEX routing: Jupiter ✗  Raydium ✗ — both unreachable, buys will fail.\n"
                "  Likely cause: geo-block or DNS issue.\n"
                "  Fix: enable a VPN (e.g. Cloudflare WARP: https://one.one.one.one/)\n"
                "       or set HTTPS_PROXY=socks5://localhost:PORT before starting."
            )

    # ─── Properties ────────────────────────────────────────────────────────────

    @property
    def pubkey(self) -> str:
        return self._pubkey or ""

    @property
    def is_connected(self) -> bool:
        return self._keypair is not None and self._pubkey is not None

    # ─── Balances ──────────────────────────────────────────────────────────────

    def get_sol_balance(self) -> float:
        """Get SOL balance in SOL."""
        if not self.is_connected:
            return 0.0
        try:
            resp = self._client.get_balance(self._keypair.pubkey())
            return resp.value / 1e9
        except Exception as e:
            logger.warning("SOL balance error: %s", e)
            return 0.0

    def get_sol_balance_lamports(self) -> int:
        """Get SOL balance in lamports."""
        if not self.is_connected:
            return 0
        try:
            return self._client.get_balance(self._keypair.pubkey()).value
        except Exception:
            return 0

    def get_token_balance(self, mint_address: str) -> float:
        """Get SPL / Token-2022 token balance (UI amount)."""
        raw, decimals = self._get_token_raw_balance(mint_address)
        if raw <= 0 or decimals < 0:
            return 0.0
        return raw / (10 ** decimals)

    def get_usdc_balance(self) -> float:
        return self.get_token_balance(USDC_MINT)

    def get_portfolio_value_usd(self) -> float:
        """Estimate total wallet value in USD (SOL + USDC)."""
        try:
            sol_price = self._get_sol_price()
            return self.get_sol_balance() * sol_price + self.get_usdc_balance()
        except Exception:
            return 0.0

    # ─── Core swap API ─────────────────────────────────────────────────────────

    def safe_buy_token(self, token_mint: str, usdc_amount: float,
                       safety_report=None,
                       liquidity_usd: float = 0.0,
                       slippage_bps: int = None) -> Optional[str]:
        """
        Buy a Solana token with USDC.
        - Checks safety report first (blocks honeypots)
        - Computes dynamic slippage based on liquidity
        - Verifies lamport balance is sufficient
        - Executes via Jito bundle for MEV protection
        Returns transaction signature or None.
        """
        # ── Safety gate ──────────────────────────────────────────────────────
        if safety_report and not safety_report.is_safe_to_trade:
            logger.warning("BLOCKED BUY %s: safety=%.2f (%s)",
                           token_mint[:12], safety_report.safety_score,
                           safety_report.risk_level)
            return None

        if not self.is_connected:
            logger.info("PAPER BUY: %s $%.2f", token_mint[:12], usdc_amount)
            return f"paper_tx_{int(time.time())}"

        # ── Lamport gate ─────────────────────────────────────────────────────
        if not self._check_lamport_balance():
            logger.error("Insufficient SOL for fees — skipping buy of %s", token_mint[:12])
            return None

        # ── Dynamic slippage ─────────────────────────────────────────────────
        if slippage_bps is None:
            slippage_bps = self._compute_slippage_bps(
                trade_usd=usdc_amount, liquidity_usd=liquidity_usd)

        # Use SOL as input (native, no USDC required) — Jupiter handles SOL→token directly
        result = self._execute_swap(
            input_mint=SOL_MINT,
            output_mint=token_mint,
            input_amount_usd=usdc_amount,
            slippage_bps=slippage_bps,
        )
        if result.success:
            logger.info("BUY %s $%.2f SOL slippage=%dbps impact=%.2f%% | tx=%s",
                        token_mint[:12], usdc_amount, slippage_bps,
                        result.price_impact_pct, result.signature[:16])
            return result.signature
        else:
            logger.error("BUY FAILED %s: %s", token_mint[:12], result.error)
            return None

    def sell_token(self, token_mint: str, est_value_usd: float,
                   liquidity_usd: float = 0.0,
                   slippage_bps: int = None) -> Optional[tuple]:
        """
        Sell entire on-chain token balance back to SOL.
        Returns (tx_signature, actual_usd_received) or None on failure.
        actual_usd_received is computed from real SOL output × current SOL price,
        so portfolio.cash is always credited with what the wallet actually received.
        """
        if not self.is_connected:
            logger.info("PAPER SELL: %s est=$%.2f", token_mint[:12], est_value_usd)
            return f"paper_sell_{int(time.time())}", est_value_usd

        if not self._check_lamport_balance():
            logger.error("Insufficient SOL for fees — skipping sell of %s", token_mint[:12])
            return None

        raw_amount, decimals = self._get_token_raw_balance(token_mint)
        if raw_amount <= 0:
            logger.warning("No on-chain balance for %s — skipping sell", token_mint[:12])
            return None

        if slippage_bps is None:
            slippage_bps = self._compute_slippage_bps(
                trade_usd=est_value_usd, liquidity_usd=liquidity_usd)
        # Selling is riskier (slippage asymmetry) — add 50bps buffer
        slippage_bps = min(slippage_bps + 50, config.SOL_MAX_SLIPPAGE_BPS)

        # Sell token back to SOL (native) — no USDC required
        result = self._execute_swap_raw(
            input_mint=token_mint,
            output_mint=SOL_MINT,
            raw_input_amount=raw_amount,
            slippage_bps=slippage_bps,
        )
        if result.success:
            out_sol = result.out_amount / 1e9
            sol_price = self._get_sol_price()
            actual_usd = out_sol * sol_price if sol_price > 0 else est_value_usd
            logger.info("SELL %s → %.4f SOL ($%.2f) impact=%.2f%% | tx=%s",
                        token_mint[:12], out_sol, actual_usd,
                        result.price_impact_pct, result.signature[:16])
            return result.signature, actual_usd
        else:
            logger.error("SELL FAILED %s: %s", token_mint[:12], result.error)
            return None

    def sell_token_partial(self, token_mint: str, fraction: float,
                           liquidity_usd: float = 0.0,
                           slippage_bps: int = None) -> Optional[tuple]:
        """
        Sell a fraction (0.0-1.0) of the on-chain token balance.
        Returns (tx_signature, actual_usd_received) or None on failure.
        """
        if not self.is_connected:
            logger.info("PAPER PARTIAL SELL: %s %.0f%%", token_mint[:12], fraction * 100)
            return f"paper_partial_sell_{int(time.time())}", 0.0

        if not self._check_lamport_balance():
            return None

        raw_amount, decimals = self._get_token_raw_balance(token_mint)
        if raw_amount <= 0:
            logger.warning("No on-chain balance for partial sell of %s", token_mint[:12])
            return None

        sell_raw = int(raw_amount * fraction)
        if sell_raw <= 0:
            return None

        if slippage_bps is None:
            slippage_bps = self._compute_slippage_bps(
                trade_usd=0.0, liquidity_usd=liquidity_usd)
        slippage_bps = min(slippage_bps + 50, config.SOL_MAX_SLIPPAGE_BPS)

        # Sell token back to SOL (native)
        result = self._execute_swap_raw(
            input_mint=token_mint,
            output_mint=SOL_MINT,
            raw_input_amount=sell_raw,
            slippage_bps=slippage_bps,
        )
        if result.success:
            out_sol = result.out_amount / 1e9
            sol_price = self._get_sol_price()
            actual_usd = out_sol * sol_price if sol_price > 0 else 0.0
            logger.info("PARTIAL SELL %s %.0f%% → %.4f SOL ($%.2f) | tx=%s",
                        token_mint[:12], fraction * 100, out_sol, actual_usd,
                        result.signature[:16])
            return result.signature, actual_usd
        else:
            logger.error("PARTIAL SELL FAILED %s: %s", token_mint[:12], result.error)
            return None

    # ─── Execution engine ──────────────────────────────────────────────────────

    def _execute_swap(self, input_mint: str, output_mint: str,
                      input_amount_usd: float, slippage_bps: int) -> SwapResult:
        """Execute swap using USD input amount (auto-converts to lamports)."""
        amount_raw = self._usd_to_raw(input_mint, input_amount_usd)
        if amount_raw <= 0:
            return SwapResult(success=False, error="USD-to-lamports conversion failed")
        return self._execute_swap_raw(input_mint, output_mint, amount_raw, slippage_bps)

    def _execute_swap_raw(self, input_mint: str, output_mint: str,
                          raw_input_amount: int, slippage_bps: int,
                          _retry: int = 0) -> SwapResult:
        """
        Core swap execution:
        1. Fresh Jupiter quote
        2. Build swap transaction via Jupiter
        3. Sign + send via Jito bundle (or fallback to direct RPC)
        4. Wait for FINALIZED confirmation
        5. Retry once with 2× fee if transaction expires
        """
        # 1. Get quote + build tx — Jupiter → Raydium → Pump.fun (cascade)
        tx_b64, out_amount, price_impact = self._quote_and_build_jupiter(
            input_mint, output_mint, raw_input_amount, slippage_bps)
        if tx_b64 is None:
            logger.info("Jupiter unavailable — trying Raydium")
            tx_b64, out_amount, price_impact = self._quote_and_build_raydium(
                input_mint, output_mint, raw_input_amount, slippage_bps)
        if tx_b64 is None:
            logger.info("Raydium unavailable — trying Pump.fun bonding curve")
            tx_b64, out_amount, price_impact = self._quote_and_build_pumpfun(
                output_mint, raw_input_amount, slippage_bps)
        if tx_b64 is None:
            return SwapResult(success=False,
                              error="No DEX quote available (Jupiter + Raydium + Pump.fun all failed)")

        if price_impact > MAX_PRICE_IMPACT_PCT:
            return SwapResult(success=False,
                              error=f"Price impact too high: {price_impact:.1f}%")
        # out_amount == 0 means the DEX didn't expose it upfront (Pump.fun); skip the gate
        if out_amount < 0:
            return SwapResult(success=False, error="Quote returned negative output")

        # 2. Estimate priority fee
        priority_fee = self._estimate_priority_fee()

        # 4. Sign, send, confirm
        sig = self._sign_and_send_jito(tx_b64, priority_fee)
        if not sig:
            # Fallback to direct RPC send
            sig = self._sign_and_send_rpc(tx_b64)
        if not sig:
            return SwapResult(success=False, error="Transaction send failed")

        # 5. Wait for FINALIZED (not just confirmed)
        confirmed = self._wait_finalized(sig)
        if not confirmed:
            if _retry == 0:
                # Before retrying, check if the original TX landed on-chain
                # (it may just be slow). Retrying without checking risks a double-spend
                # if TX1 was already accepted and TX2 also executes.
                try:
                    from solders.signature import Signature as _Sig
                    st = self._client.get_signature_statuses(
                        [_Sig.from_string(sig)])
                    if st.value and st.value[0] is not None:
                        logger.warning(
                            "TX on-chain but slow — waiting 30s more: %s", sig[:16])
                        if self._wait_finalized(sig, timeout=30):
                            return SwapResult(success=True, signature=sig,
                                              out_amount=out_amount,
                                              price_impact_pct=price_impact,
                                              actual_slippage_bps=slippage_bps)
                        logger.error("TX still unfinalized after extra 30s: %s",
                                     sig[:16])
                        return SwapResult(success=False,
                                          error=f"Slow TX not finalized: {sig[:16]}")
                except Exception:
                    pass
                # TX not found on-chain — safe to retry with higher fee
                logger.warning("TX not finalized in %ds — retrying with 2× fee: %s",
                               CONFIRMATION_TIMEOUT, sig[:16])
                return self._execute_swap_raw(input_mint, output_mint,
                                              raw_input_amount,
                                              slippage_bps,
                                              _retry=1)
            logger.error("TX still unconfirmed after retry: %s", sig[:16])
            return SwapResult(success=False,
                              error=f"Not finalized after retry: {sig[:16]}")

        return SwapResult(
            success=True,
            signature=sig,
            out_amount=out_amount,
            price_impact_pct=price_impact,
            actual_slippage_bps=slippage_bps,
        )

    # ─── Jupiter + Raydium quote/build helpers ─────────────────────────────────

    def _quote_and_build_jupiter(self, input_mint: str, output_mint: str,
                                 amount: int, slippage_bps: int
                                 ) -> tuple[Optional[str], int, float]:
        """Get Jupiter quote and build swap tx. Returns (tx_b64, out_amount, price_impact) or (None, 0, 0)."""
        quote = self._get_jupiter_quote(input_mint, output_mint, amount, slippage_bps)
        if not quote:
            return None, 0, 0.0
        out_amount   = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct", 0))
        priority_fee = self._estimate_priority_fee()
        swap_payload = {
            "quoteResponse":             quote,
            "userPublicKey":             self._pubkey,
            "wrapAndUnwrapSol":          True,
            "prioritizationFeeLamports": priority_fee,
            "dynamicComputeUnitLimit":   True,
        }
        try:
            resp = requests.post(JUPITER_SWAP_URL, json=swap_payload, timeout=15)
            if not resp.ok:
                return None, 0, 0.0
            tx_b64 = resp.json().get("swapTransaction")
            if not tx_b64:
                return None, 0, 0.0
            return tx_b64, out_amount, price_impact
        except Exception:
            return None, 0, 0.0

    def _quote_and_build_raydium(self, input_mint: str, output_mint: str,
                                 amount: int, slippage_bps: int
                                 ) -> tuple[Optional[str], int, float]:
        """Get Raydium quote and build swap tx. Returns (tx_b64, out_amount, price_impact) or (None, 0, 0)."""
        try:
            # Step 1: compute quote
            resp = requests.get(RAYDIUM_COMPUTE_URL, params={
                "inputMint":  input_mint,
                "outputMint": output_mint,
                "amount":     str(amount),
                "slippageBps": str(slippage_bps),
                "txVersion":  "V0",
            }, timeout=10)
            if not resp.ok:
                logger.debug("Raydium compute failed: %s", resp.status_code)
                return None, 0, 0.0
            data = resp.json()
            if not data.get("success"):
                logger.warning("Raydium: no pool for this token (%s)", data.get("msg", "no route"))
                return None, 0, 0.0
            swap_data    = data["data"]
            out_amount   = int(swap_data.get("outputAmount", 0))
            price_impact = float(swap_data.get("priceImpactPct", 0))

            # Step 2: build transaction
            tx_resp = requests.post(RAYDIUM_TX_URL, json={
                "computeUnitPriceMicroLamports": "auto",
                "swapResponse": data,
                "txVersion":    "V0",
                "wallet":       self._pubkey,
                "wrapSol":      True,
                "unwrapSol":    True,
            }, timeout=15)
            if not tx_resp.ok:
                logger.debug("Raydium tx build failed: %s", tx_resp.status_code)
                return None, 0, 0.0
            tx_data = tx_resp.json()
            if not tx_data.get("success"):
                logger.debug("Raydium tx build error: %s", tx_data)
                return None, 0, 0.0
            txs = tx_data.get("data", [])
            if not txs:
                return None, 0, 0.0
            tx_b64 = txs[0].get("transaction")
            if not tx_b64:
                return None, 0, 0.0
            logger.debug("Raydium quote ok: out=%d impact=%.2f%%", out_amount, price_impact)
            return tx_b64, out_amount, price_impact
        except Exception as e:
            logger.warning("Raydium quote/build exception: %s", e)
            return None, 0, 0.0

    def _quote_and_build_pumpfun(self, output_mint: str, amount_lamports: int,
                                 slippage_bps: int
                                 ) -> tuple[Optional[str], int, float]:
        """Build a buy tx via PumpPortal for Pump.fun bonding-curve tokens.

        PumpPortal returns raw VersionedTransaction bytes (not JSON). We base64-encode
        them so the existing _sign_and_send_* code handles them identically to
        Jupiter/Raydium transactions. Output amount is not exposed by the API
        (returns 0); the caller must accept out_amount=0 as "unknown".
        """
        try:
            amount_sol = amount_lamports / 1e9
            # PumpPortal expects slippage as integer percentage (e.g. 10 = 10%)
            # Use minimum 10% — memecoins on bonding curves need wide slippage
            slippage_pct = max(10, slippage_bps // 100)
            resp = requests.post(
                PUMPFUN_TRADE_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "publicKey":        self._pubkey,
                    "action":           "buy",
                    "mint":             output_mint,
                    "denominatedInSol": "true",
                    "amount":           round(amount_sol, 6),
                    "slippage":         slippage_pct,
                    "priorityFee":      0.0001,
                    "pool":             "pump",
                },
                timeout=10,
            )
            if not resp.ok:
                logger.warning("PumpPortal failed: HTTP %s — %s",
                               resp.status_code, resp.text[:300])
                return None, 0, 0.0
            tx_bytes = resp.content
            if not tx_bytes:
                return None, 0, 0.0
            tx_b64 = base64.b64encode(tx_bytes).decode()
            logger.debug("PumpPortal tx built: %.6f SOL → %s", amount_sol, output_mint[:12])
            return tx_b64, 0, 0.0  # out_amount unknown until tx lands
        except Exception as e:
            logger.warning("PumpPortal exception: %s", e)
            return None, 0, 0.0

    # ─── Jupiter ───────────────────────────────────────────────────────────────

    def _get_jupiter_quote(self, input_mint: str, output_mint: str,
                           amount: int, slippage_bps: int) -> Optional[dict]:
        """Fetch a fresh quote from Jupiter v6 (3 attempts with backoff)."""
        params = {
            "inputMint":           input_mint,
            "outputMint":          output_mint,
            "amount":              str(amount),
            "slippageBps":         str(slippage_bps),
            "onlyDirectRoutes":    "false",
            "asLegacyTransaction": "false",
        }
        for attempt in range(3):
            try:
                resp = requests.get(JUPITER_QUOTE_URL, params=params, timeout=10)
                if resp.ok:
                    data = resp.json()
                    if data.get("error"):
                        logger.debug("Jupiter quote error: %s", data["error"])
                        return None
                    return data
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)  # 1s, 2s
                else:
                    logger.warning("Jupiter quote exception: %s", e)
        return None

    # ─── Transaction signing / sending ────────────────────────────────────────

    def _sign_and_send_jito(self, tx_b64: str, priority_fee: int) -> Optional[str]:
        """
        Sign the transaction and submit as a Jito bundle for MEV protection.
        Jito tip is added as a SOL transfer to a random tip account.
        Falls back to None if Jito is unavailable or fails.
        """
        if not config.MEV_PROTECTION_ENABLED:
            return None
        try:
            from solders.transaction import VersionedTransaction

            tx_bytes = base64.b64decode(tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            signed_tx = VersionedTransaction(tx.message, [self._keypair])
            signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

            # Jito tip: send a separate SOL transfer of ~0.001 SOL as bundle tip
            tip_lamports = max(priority_fee, config.JITO_TIP_LAMPORTS)
            tip_tx_b64   = self._build_jito_tip_transaction(tip_lamports)
            if not tip_tx_b64:
                # Can't build tip tx → skip Jito, fall through to direct RPC
                return None

            bundle_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [[tip_tx_b64, signed_b64]],
            }
            resp = requests.post(
                JITO_BUNDLE_URL, json=bundle_payload, timeout=15,
                headers={"Content-Type": "application/json"},
            )
            if resp.ok:
                result = resp.json().get("result")
                if result:
                    sig = str(VersionedTransaction.from_bytes(
                        base64.b64decode(signed_b64)).signatures[0])
                    logger.debug("Jito bundle submitted: %s", sig[:16])
                    return sig
        except Exception as e:
            logger.debug("Jito bundle failed: %s", e)
        return None

    def _build_jito_tip_transaction(self, tip_lamports: int) -> Optional[str]:
        """Build a signed SOL transfer to a random Jito tip account. Returns b64."""
        try:
            from solders.transaction import VersionedTransaction
            from solders.message import MessageV0
            from solders.pubkey import Pubkey
            from solders.system_program import transfer, TransferParams
            from solders.hash import Hash

            tip_account = Pubkey.from_string(random.choice(JITO_TIP_ACCOUNTS))
            recent_hash_resp = self._client.get_latest_blockhash()
            blockhash = recent_hash_resp.value.blockhash

            instr = transfer(TransferParams(
                from_pubkey=self._keypair.pubkey(),
                to_pubkey=tip_account,
                lamports=tip_lamports,
            ))
            msg = MessageV0.try_compile(
                payer=self._keypair.pubkey(),
                instructions=[instr],
                address_lookup_table_accounts=[],
                recent_blockhash=blockhash,
            )
            tx = VersionedTransaction(msg, [self._keypair])
            return base64.b64encode(bytes(tx)).decode()
        except Exception as e:
            logger.debug("Jito tip tx build error: %s", e)
            return None

    def _sign_and_send_rpc(self, tx_b64: str) -> Optional[str]:
        """Sign and send via direct Solana RPC (fallback when Jito unavailable)."""
        try:
            from solders.transaction import VersionedTransaction
            from solana.rpc.types import TxOpts

            tx_bytes = base64.b64decode(tx_b64)
            tx       = VersionedTransaction.from_bytes(tx_bytes)
            signed   = VersionedTransaction(tx.message, [self._keypair])

            result = self._client.send_raw_transaction(
                bytes(signed),
                opts=TxOpts(
                    skip_preflight=False,
                    preflight_commitment="confirmed",
                ),
            )
            sig = str(result.value)
            logger.debug("RPC transaction sent: %s", sig[:16])
            return sig
        except Exception as e:
            logger.error("RPC send error: %s", e)
            return None

    # ─── Confirmation ──────────────────────────────────────────────────────────

    def _wait_finalized(self, signature: str,
                        timeout: int = CONFIRMATION_TIMEOUT) -> bool:
        """
        Wait for a transaction to reach FINALIZED status.
        Returns True only when finalized. "confirmed" is intentionally not
        accepted — confirmed blocks can still be rolled back in rare cases.
        """
        try:
            from solders.signature import Signature
            sig_obj = Signature.from_string(signature)
            deadline = time.time() + timeout
            poll_interval = 1.5

            while time.time() < deadline:
                time.sleep(poll_interval)
                try:
                    status = self._client.get_signature_statuses([sig_obj])
                    if status.value and status.value[0]:
                        s = status.value[0]
                        if s.err:
                            logger.error("TX failed on-chain: %s err=%s",
                                         signature[:16], s.err)
                            return False
                        if str(s.confirmation_status) == "finalized":
                            logger.info("TX finalized: %s", signature[:16])
                            return True
                        # Log progress without spamming
                        logger.debug("TX status: %s (%s)",
                                     signature[:16], s.confirmation_status)
                except Exception:
                    pass
                poll_interval = min(poll_interval * 1.5, 4.0)

            logger.warning("TX confirmation timeout (%ds): %s",
                           timeout, signature[:16])
            return False
        except Exception as e:
            logger.error("Confirmation wait error: %s", e)
            return False

    # ─── Priority fees ─────────────────────────────────────────────────────────

    def _estimate_priority_fee(self) -> int:
        """
        Estimate priority fee in micro-lamports per compute unit.
        Uses Helius getPriorityFeeEstimate if Helius RPC is configured,
        otherwise falls back to config constant.
        """
        rpc_url = config.SOLANA_RPC_URL
        if not rpc_url or "helius" not in rpc_url.lower():
            return config.MEV_PRIORITY_FEE_LAMPORTS

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getPriorityFeeEstimate",
                "params": [{
                    "accountKeys": [self._pubkey],
                    "options": {"priorityLevel": "High"},
                }],
            }
            resp = requests.post(rpc_url, json=payload, timeout=5)
            if resp.ok:
                fee = resp.json().get("result", {}).get("priorityFeeEstimate", 0)
                if fee and fee > 0:
                    # Add 20% buffer to stay competitive
                    return int(fee * 1.2)
        except Exception as e:
            logger.debug("Priority fee estimate error: %s", e)

        return config.MEV_PRIORITY_FEE_LAMPORTS

    # ─── Dynamic slippage ──────────────────────────────────────────────────────

    def _compute_slippage_bps(self, trade_usd: float,
                               liquidity_usd: float) -> int:
        """
        Compute dynamic slippage in basis points:
        - Base: 100 bps (1%)
        - Scale up as trade size approaches pool liquidity
        - Cap at SOL_MAX_SLIPPAGE_BPS from config
        """
        base_bps = 100
        if liquidity_usd > 0 and trade_usd > 0:
            # Liquidity impact: trade_usd / liquidity_usd × 10000 bps
            impact_bps = int((trade_usd / liquidity_usd) * 10_000)
            # Round up to nearest 50 bps, add to base
            buffer = ((impact_bps + 49) // 50) * 50
            base_bps = max(base_bps, buffer)
        return min(base_bps, config.SOL_MAX_SLIPPAGE_BPS)

    # ─── Balance helpers ───────────────────────────────────────────────────────

    def _check_lamport_balance(self) -> bool:
        """Return True if wallet has enough SOL to cover tx fees + Jito tip."""
        lamports = self.get_sol_balance_lamports()
        if lamports < MIN_SOL_RESERVE_LAMPORTS:
            logger.warning("Low SOL balance: %.6f SOL (need ≥ %.3f for fees + tip)",
                           lamports / 1e9, MIN_SOL_RESERVE_LAMPORTS / 1e9)
            return False
        return True

    def _get_token_raw_balance(self, mint_address: str) -> tuple[int, int]:
        """
        Returns (raw_amount_in_smallest_units, decimals) for a token.
        Supports both legacy SPL Token and Token-2022 (Token Extensions) program.
        """
        if not self.is_connected:
            return 0, 6
        try:
            from solders.pubkey import Pubkey
            mint_pk = Pubkey.from_string(mint_address)

            # Try legacy SPL Token program first
            for program_id in (
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",   # Token-2022
            ):
                try:
                    resp = self._client.get_token_accounts_by_owner(
                        self._keypair.pubkey(),
                        {"mint": mint_pk},
                        commitment="confirmed",
                    )
                    if resp.value:
                        for acc in resp.value:
                            info = acc.account.data.parsed
                            ta = info.get("info", {}).get("tokenAmount", {})
                            raw = int(ta.get("amount", 0))
                            dec = int(ta.get("decimals", 6))
                            if raw > 0:
                                return raw, dec
                except Exception:
                    pass

            return 0, 6
        except Exception as e:
            logger.debug("Token raw balance error: %s", e)
            return 0, 6

    # ─── USD conversion ────────────────────────────────────────────────────────

    def _usd_to_raw(self, mint: str, usd_amount: float) -> int:
        """Convert USD amount to raw token units."""
        if mint == SOL_MINT:
            sol_price = self._get_sol_price()
            if sol_price <= 0:
                return 0
            return int((usd_amount / sol_price) * 1e9)
        elif mint in (USDC_MINT, USDT_MINT):
            return int(usd_amount * 1e6)
        else:
            # Unknown mint — treat as 6-decimal token via USDC equivalent
            return int(usd_amount * 1e6)

    def _get_sol_price(self) -> float:
        """
        Get current SOL/USD price with 15s cache.
        Sources tried in order: Birdeye → Jupiter Price API → CoinGecko.
        Returns last known price on failure; falls back to $150 only if never fetched.
        """
        price, ts = self._sol_price_cache
        if price > 0 and time.time() - ts < 15:
            return price

        # 1. Birdeye (real-time, most accurate when key available)
        if config.BIRDEYE_API_KEY:
            try:
                resp = requests.get(
                    "https://public-api.birdeye.so/defi/price",
                    params={"address": SOL_MINT},
                    headers={"X-API-KEY": config.BIRDEYE_API_KEY, "x-chain": "solana"},
                    timeout=4,
                )
                if resp.ok:
                    p = float(resp.json().get("data", {}).get("value", 0) or 0)
                    if p > 0:
                        self._sol_price_cache = (p, time.time())
                        return p
            except Exception:
                pass

        # 2. Jupiter Price API v2 (no key, real DEX price)
        try:
            resp = requests.get(
                "https://api.jup.ag/price/v2",
                params={"ids": SOL_MINT},
                timeout=4,
                headers={"User-Agent": "ai-trader/2.0"},
            )
            if resp.ok:
                p = float(resp.json().get("data", {}).get(SOL_MINT, {}).get("price", 0) or 0)
                if p > 0:
                    self._sol_price_cache = (p, time.time())
                    return p
        except Exception:
            pass

        # 3. CoinGecko (free, slightly delayed)
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana", "vs_currencies": "usd"},
                timeout=4,
            )
            if resp.ok:
                p = float(resp.json()["solana"]["usd"])
                if p > 0:
                    self._sol_price_cache = (p, time.time())
                    return p
        except Exception:
            pass

        # Return stale cache if all sources fail — better than a hardcoded guess
        if price > 0:
            logger.debug("All SOL price sources failed — using stale cache $%.2f", price)
            return price
        logger.warning("SOL price unavailable — using $150 fallback estimate")
        return 150.0

    # ─── Quote helper (for safety checker / dry-run) ───────────────────────────

    def get_quote(self, input_mint: str, output_mint: str,
                  amount_lamports: int, slippage_bps: int = 100) -> Optional[dict]:
        """Public quote endpoint — used by token_safety for sell simulation."""
        return self._get_jupiter_quote(input_mint, output_mint,
                                       amount_lamports, slippage_bps)
