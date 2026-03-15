"""
Phantom Wallet + Jupiter DEX — Autonomous Solana Trading
========================================================
Connects to your Phantom wallet via private key (exported from Phantom).
Executes swaps through Jupiter Aggregator (best price across all Solana DEXes).

Setup:
1. In Phantom: Settings → Security → Export Private Key
2. Add PHANTOM_PRIVATE_KEY to your .env file
3. Bot handles everything else automatically

Supported:
- Any Solana token (SOL, USDC, memecoins, etc.)
- Automatic best-route finding via Jupiter
- Slippage protection
- Priority fees for fast execution
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Optional

import requests

import config as _cfg

logger = logging.getLogger(__name__)

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL  = "https://quote-api.jup.ag/v6/swap"
SOLANA_RPC_URL    = _cfg.SOLANA_RPC_URL

# Common token mints
SOL_MINT   = "So11111111111111111111111111111111111111112"
USDC_MINT  = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT  = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
BONK_MINT  = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# Slippage in basis points (100 = 1%)
DEFAULT_SLIPPAGE_BPS = 100   # 1%
HIGH_VOL_SLIPPAGE_BPS = 300  # 3% for volatile tokens


class SolanaWallet:
    """
    Phantom wallet interface for autonomous trading via Jupiter DEX aggregator.
    Handles quotes, swaps, and balance checking.
    """

    def __init__(self, private_key_b58: str = ""):
        self.private_key_b58 = private_key_b58
        self._keypair = None
        self._client  = None
        self._pubkey  = None

        if private_key_b58:
            self._init_wallet()

    def _init_wallet(self):
        """Initialize Solana keypair from Phantom private key."""
        try:
            from solders.keypair import Keypair
            from solana.rpc.api import Client

            # Phantom exports private key as base58 or byte array
            try:
                key_bytes = base64.b58decode(self.private_key_b58)
            except Exception:
                # Try as JSON array
                key_bytes = bytes(json.loads(self.private_key_b58))

            self._keypair = Keypair.from_bytes(key_bytes)
            self._pubkey  = str(self._keypair.pubkey())
            self._client  = Client(SOLANA_RPC_URL)

            logger.info("Phantom wallet connected: %s...%s",
                        self._pubkey[:6], self._pubkey[-4:])
        except ImportError:
            logger.warning("solana/solders not installed. Solana trading disabled.")
            logger.warning("Install: pip3 install solana solders")
        except Exception as e:
            logger.error("Wallet init failed: %s", e)

    # ─── Balance ──────────────────────────────────────────────────────────────

    @property
    def pubkey(self) -> str:
        return self._pubkey or ""

    @property
    def is_connected(self) -> bool:
        return self._keypair is not None and self._pubkey is not None

    def get_sol_balance(self) -> float:
        """Get SOL balance in SOL (not lamports)."""
        if not self.is_connected:
            return 0.0
        try:
            resp = self._client.get_balance(self._keypair.pubkey())
            lamports = resp.value
            return lamports / 1e9
        except Exception as e:
            logger.warning("SOL balance error: %s", e)
            return 0.0

    def get_token_balance(self, mint_address: str) -> float:
        """Get SPL token balance."""
        if not self.is_connected:
            return 0.0
        try:
            from solders.pubkey import Pubkey
            resp = self._client.get_token_accounts_by_owner(
                self._keypair.pubkey(),
                {"mint": Pubkey.from_string(mint_address)},
                commitment="confirmed",
            )
            if not resp.value:
                return 0.0
            for acc in resp.value:
                info = acc.account.data.parsed
                amount = info.get("info", {}).get("tokenAmount", {}).get("uiAmount", 0)
                if amount:
                    return float(amount)
            return 0.0
        except Exception as e:
            logger.debug("Token balance error: %s", e)
            return 0.0

    def get_usdc_balance(self) -> float:
        return self.get_token_balance(USDC_MINT)

    def get_portfolio_value_usd(self) -> float:
        """Estimate total wallet value in USD."""
        try:
            sol_price = self._get_sol_price()
            sol_bal   = self.get_sol_balance()
            usdc_bal  = self.get_usdc_balance()
            return sol_bal * sol_price + usdc_bal
        except Exception:
            return 0.0

    # ─── Jupiter Swaps ────────────────────────────────────────────────────────

    def get_quote(self, input_mint: str, output_mint: str,
                  amount_lamports: int, slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> Optional[dict]:
        """
        Get best swap quote from Jupiter.
        amount_lamports: amount in smallest denomination (lamports for SOL, micro-USDC, etc.)
        """
        params = {
            "inputMint":          input_mint,
            "outputMint":         output_mint,
            "amount":             str(amount_lamports),
            "slippageBps":        str(slippage_bps),
            "onlyDirectRoutes":   "false",
            "asLegacyTransaction": "false",
        }
        try:
            resp = requests.get(JUPITER_QUOTE_URL, params=params, timeout=10)
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.warning("Jupiter quote error: %s", e)
        return None

    def swap(self, input_mint: str, output_mint: str,
             input_amount_usd: float, slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> Optional[str]:
        """
        Execute a swap via Jupiter.
        Returns transaction signature or None on failure.

        input_amount_usd: dollar amount to swap
        """
        if not self.is_connected:
            logger.info("PAPER SWAP: %s → %s $%.2f", input_mint[:8], output_mint[:8], input_amount_usd)
            return f"paper_tx_{int(time.time())}"

        try:
            # Convert USD to lamports/micro-tokens
            amount_lamports = self._usd_to_lamports(input_mint, input_amount_usd)
            if amount_lamports <= 0:
                return None

            # 1. Get quote
            quote = self.get_quote(input_mint, output_mint, amount_lamports, slippage_bps)
            if not quote:
                logger.error("No Jupiter quote available")
                return None

            out_amount = int(quote.get("outAmount", 0))
            price_impact = float(quote.get("priceImpactPct", 0))

            if price_impact > 5.0:
                logger.warning("Price impact too high: %.2f%%, skipping swap", price_impact)
                return None

            logger.info("Jupiter quote: in=%d → out=%d (impact=%.2f%%)",
                        amount_lamports, out_amount, price_impact)

            # 2. Build swap transaction
            swap_payload = {
                "quoteResponse": quote,
                "userPublicKey": self._pubkey,
                "wrapAndUnwrapSol": True,
                "prioritizationFeeLamports": _cfg.SOL_PRIORITY_FEE_LAMPORTS,
                "dynamicComputeUnitLimit": True,
            }
            resp = requests.post(JUPITER_SWAP_URL, json=swap_payload, timeout=15)
            if not resp.ok:
                logger.error("Jupiter swap build failed: %s", resp.text[:200])
                return None

            swap_data = resp.json()
            tx_b64 = swap_data.get("swapTransaction")
            if not tx_b64:
                return None

            # 3. Sign and send transaction
            sig = self._sign_and_send(tx_b64)
            if sig:
                logger.info("SWAP executed: %s → %s $%.2f | tx: %s",
                            input_mint[:8], output_mint[:8], input_amount_usd, sig[:16])
            return sig

        except Exception as e:
            logger.error("Swap execution error: %s", e)
            return None

    def buy_token(self, token_mint: str, usdc_amount: float,
                  slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> Optional[str]:
        """Buy a token using USDC."""
        logger.info("BUY %s with $%.2f USDC", token_mint[:20], usdc_amount)
        return self.swap(USDC_MINT, token_mint, usdc_amount, slippage_bps)

    def sell_token(self, token_mint: str, token_amount_usd: float,
                   slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> Optional[str]:
        """Sell a token back to USDC."""
        logger.info("SELL %s worth $%.2f", token_mint[:20], token_amount_usd)
        return self.swap(token_mint, USDC_MINT, token_amount_usd, slippage_bps)

    def buy_with_sol(self, token_mint: str, sol_amount: float,
                     slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> Optional[str]:
        """Buy a token using SOL."""
        logger.info("BUY %s with %.4f SOL", token_mint[:20], sol_amount)
        return self.swap(SOL_MINT, token_mint, sol_amount, slippage_bps)

    # ─── Utilities ────────────────────────────────────────────────────────────

    def _sign_and_send(self, tx_b64: str) -> Optional[str]:
        """Sign and broadcast a base64-encoded transaction."""
        try:
            from solders.transaction import VersionedTransaction
            from solana.rpc.types import TxOpts

            tx_bytes = base64.b64decode(tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)

            # Sign the transaction
            signed_tx = VersionedTransaction(tx.message, [self._keypair])

            # Send
            result = self._client.send_raw_transaction(
                bytes(signed_tx),
                opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"),
            )
            sig = str(result.value)
            logger.info("Transaction sent: %s", sig)

            # Wait for confirmation (up to 30 seconds)
            for _ in range(30):
                time.sleep(1)
                status = self._client.get_signature_statuses([result.value])
                if status.value and status.value[0]:
                    conf = status.value[0].confirmation_status
                    if conf in ("confirmed", "finalized"):
                        logger.info("Transaction confirmed: %s", sig[:20])
                        return sig
            return sig  # Return even if not confirmed yet

        except Exception as e:
            logger.error("Sign/send error: %s", e)
            return None

    def _usd_to_lamports(self, mint: str, usd_amount: float) -> int:
        """Convert USD amount to token lamports."""
        if mint == SOL_MINT:
            sol_price = self._get_sol_price()
            if sol_price <= 0:
                return 0
            sol_amount = usd_amount / sol_price
            return int(sol_amount * 1e9)  # SOL has 9 decimals
        elif mint == USDC_MINT or mint == USDT_MINT:
            return int(usd_amount * 1e6)  # USDC/USDT have 6 decimals
        else:
            # For other tokens, convert via USDC
            return int(usd_amount * 1e6)

    def _get_sol_price(self) -> float:
        """Get current SOL price in USD via CoinCap (free, no key)."""
        # Primary: CoinCap (reliable, free, no auth)
        try:
            resp = requests.get(
                "https://api.coincap.io/v2/assets/solana",
                timeout=5,
                headers={"Accept": "application/json"},
            )
            if resp.ok:
                price = float(resp.json()["data"]["priceUsd"])
                if price > 0:
                    return price
        except Exception:
            pass
        # Fallback: CoinGecko
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana", "vs_currencies": "usd"},
                timeout=5,
            )
            if resp.ok:
                return float(resp.json()["solana"]["usd"])
        except Exception:
            pass
        logger.warning("Could not fetch live SOL price from any source")
        return 0.0
