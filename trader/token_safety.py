"""
Token Safety — Rug Pull Detection, Honeypot Simulation, On-Chain Verification
==============================================================================
Aggregates data from three free sources to produce a composite safety score:

1. RugCheck API (https://api.rugcheck.xyz) — risk assessment, holder analysis
2. Solana RPC (getAccountInfo) — mint/freeze authority verification
3. Jupiter Quote API — sell simulation for honeypot detection

Balanced mode: penalize risky tokens with reduced scores/positions rather
than hard-blocking them, but still block confirmed honeypots.
"""
from __future__ import annotations
import base64
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_TOKEN_LIST_URL = "https://token.jup.ag/all"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@dataclass
class TokenSafetyReport:
    """Result of a comprehensive token safety check."""
    mint_address: str
    safety_score: float           # 0.0 = certain rug, 1.0 = maximum safety
    is_safe_to_trade: bool        # safety_score >= MIN_SAFETY_SCORE
    risk_level: str               # "CRITICAL", "HIGH", "MEDIUM", "LOW", "SAFE"

    # Individual checks
    mint_authority_disabled: Optional[bool] = None
    freeze_authority_disabled: Optional[bool] = None
    is_jupiter_verified: bool = False

    # RugCheck data
    rugcheck_risk: Optional[str] = None
    rugcheck_score: Optional[float] = None
    top_10_holder_pct: Optional[float] = None
    lp_locked: Optional[bool] = None

    # Honeypot simulation
    sell_simulation_passed: Optional[bool] = None
    round_trip_tax_pct: Optional[float] = None

    # Derived
    risk_flags: list[str] = field(default_factory=list)
    checked_at: float = 0.0


class TokenSafetyChecker:
    """
    Multi-source token safety analysis for Solana memecoins.
    Uses RugCheck API, Solana RPC, and Jupiter quote simulation.
    """

    def __init__(self, solana_rpc_url: str = None):
        self._rpc_url = solana_rpc_url or config.SOLANA_RPC_URL
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "ai-trader-safety/1.0"
        self._cache: dict = {}  # mint → (TokenSafetyReport, timestamp)
        self._cache_ttl = config.RUGCHECK_CACHE_TTL
        self._verified_tokens: Optional[set] = None
        self._verified_loaded_at = 0.0

    # ─── Public API ──────────────────────────────────────────────────────────

    def check_token_safety(self, mint_address: str) -> TokenSafetyReport:
        """
        Run all safety checks on a token and return a composite report.
        Results are cached for RUGCHECK_CACHE_TTL seconds.
        """
        # Check cache
        if mint_address in self._cache:
            report, ts = self._cache[mint_address]
            if time.time() - ts < self._cache_ttl:
                return report

        score = 1.0
        flags = []

        # 1. RugCheck API
        rc = self._check_rugcheck(mint_address)

        # 2. On-chain mint/freeze authority
        onchain = self._check_on_chain_mint(mint_address)

        # 3. Jupiter verified list
        jup_verified = self._check_jupiter_verified(mint_address)

        # 4. Sell simulation (honeypot detection)
        sell_sim = self._simulate_sell(mint_address) if config.ENABLE_SELL_SIMULATION else {}

        # ── Scoring ──────────────────────────────────────────────────────

        # Mint authority
        mint_disabled = onchain.get("mint_authority_disabled")
        if mint_disabled is None:
            mint_disabled = rc.get("mint_authority_disabled")
        if mint_disabled is False:
            score -= 0.25  # Balanced: penalize, don't hard-block
            flags.append("Mint authority active (can inflate supply)")

        # Freeze authority
        freeze_disabled = onchain.get("freeze_authority_disabled")
        if freeze_disabled is None:
            freeze_disabled = rc.get("freeze_authority_disabled")
        if freeze_disabled is False:
            score -= 0.20
            flags.append("Freeze authority active (can freeze wallets)")

        # Both disabled = bonus
        if mint_disabled is True and freeze_disabled is True:
            score += 0.08

        # Top holder concentration
        top10 = rc.get("top_10_holder_pct")
        if top10 is not None:
            if top10 > 0.90:
                score -= 0.45
                flags.append(f"Top 10 holders own {top10:.0%} (extreme concentration)")
            elif top10 > config.MAX_TOP10_HOLDER_PCT:
                score -= 0.30
                flags.append(f"Top 10 holders own {top10:.0%} (high concentration)")
            elif top10 > 0.60:
                score -= 0.10
                flags.append(f"Top 10 holders own {top10:.0%}")

        # LP lock status
        lp_locked = rc.get("lp_locked")
        if lp_locked is True:
            score += 0.05
        elif lp_locked is False:
            score -= 0.12
            flags.append("Liquidity not locked")

        # RugCheck risk level
        rc_risk = rc.get("risk_level")
        if rc_risk == "Good":
            score += 0.08
        elif rc_risk == "Danger":
            score -= 0.20
            flags.append("RugCheck: DANGER rating")
        elif rc_risk == "Warning":
            score -= 0.08
            flags.append("RugCheck: WARNING rating")

        # Data unavailability penalties
        if not rc.get("available", False):
            score -= 0.05
        if not onchain.get("available", False):
            score -= 0.05

        # Jupiter verified bonus
        if jup_verified:
            score += 0.05

        # Honeypot / sell simulation
        sell_passed = sell_sim.get("passed")
        round_trip_tax = sell_sim.get("round_trip_tax_pct")
        if sell_passed is False and config.BLOCK_HONEYPOTS:
            score = 0.0  # Hard block: can't sell = confirmed honeypot
            flags.append("HONEYPOT: sell simulation failed (no route)")
        elif round_trip_tax is not None:
            if round_trip_tax > config.MAX_ROUND_TRIP_TAX_PCT:
                score -= 0.30
                flags.append(f"High tax: {round_trip_tax:.0%} round-trip loss")
            elif round_trip_tax > 0.10:
                score -= 0.10
                flags.append(f"Moderate tax: {round_trip_tax:.0%} round-trip")

        # Clamp score
        score = max(0.0, min(1.0, score))

        # Risk level
        if score >= 0.8:
            risk_level = "SAFE"
        elif score >= 0.6:
            risk_level = "LOW"
        elif score >= 0.4:
            risk_level = "MEDIUM"
        elif score >= 0.2:
            risk_level = "HIGH"
        else:
            risk_level = "CRITICAL"

        report = TokenSafetyReport(
            mint_address=mint_address,
            safety_score=round(score, 3),
            is_safe_to_trade=score >= config.MIN_SAFETY_SCORE,
            risk_level=risk_level,
            mint_authority_disabled=mint_disabled,
            freeze_authority_disabled=freeze_disabled,
            is_jupiter_verified=jup_verified,
            rugcheck_risk=rc_risk,
            rugcheck_score=rc.get("score"),
            top_10_holder_pct=top10,
            lp_locked=lp_locked,
            sell_simulation_passed=sell_passed,
            round_trip_tax_pct=round_trip_tax,
            risk_flags=flags,
            checked_at=time.time(),
        )

        now = time.time()
        self._cache[mint_address] = (report, now)
        # Evict expired entries when cache grows large (prevents unbounded growth)
        if len(self._cache) > 500:
            self._cache = {k: v for k, v in self._cache.items()
                           if now - v[1] < self._cache_ttl}
        return report

    # ─── Sub-Checks ──────────────────────────────────────────────────────

    def _check_rugcheck(self, mint_address: str) -> dict:
        """Query RugCheck API for token risk assessment."""
        try:
            resp = self._session.get(
                f"{RUGCHECK_BASE}/tokens/{mint_address}/report/summary",
                timeout=config.SAFETY_CHECK_TIMEOUT,
            )
            if resp.status_code != 200:
                return {"available": False}

            data = resp.json()
            risks = data.get("risks", [])
            risk_names = [r.get("name", "") for r in risks]

            # Parse top holder concentration from risks or topHolders
            top10_pct = None
            top_holders = data.get("topHolders", [])
            if top_holders:
                total_pct = sum(h.get("pct", 0) for h in top_holders[:10])
                top10_pct = total_pct / 100.0 if total_pct > 1 else total_pct

            # Determine overall risk level
            score = data.get("score", 0)
            if score >= 800:
                risk_level = "Good"
            elif score >= 400:
                risk_level = "Warning"
            else:
                risk_level = "Danger"

            # Check mint/freeze from RugCheck data
            mint_disabled = None
            freeze_disabled = None
            token_meta = data.get("tokenMeta", {})
            if "mintAuthority" in token_meta:
                mint_disabled = token_meta["mintAuthority"] is None
            if "freezeAuthority" in token_meta:
                freeze_disabled = token_meta["freezeAuthority"] is None

            # LP lock status
            lp_locked = None
            for r in risks:
                name = r.get("name", "").lower()
                if "lp" in name and "unlocked" in name:
                    lp_locked = False
                elif "lp" in name and "locked" in name:
                    lp_locked = True
                elif "lp" in name and "burned" in name:
                    lp_locked = True  # Burned LP = permanently locked

            return {
                "available": True,
                "score": score,
                "risk_level": risk_level,
                "risk_names": risk_names,
                "top_10_holder_pct": top10_pct,
                "mint_authority_disabled": mint_disabled,
                "freeze_authority_disabled": freeze_disabled,
                "lp_locked": lp_locked,
            }
        except Exception as e:
            logger.debug("RugCheck API error for %s: %s", mint_address[:12], e)
            return {"available": False}

    def _check_on_chain_mint(self, mint_address: str) -> dict:
        """
        Check token mint account on-chain via Solana RPC.
        Parses SPL Token mint layout to verify mint/freeze authority.
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [mint_address, {"encoding": "base64"}],
            }
            resp = self._session.post(
                self._rpc_url, json=payload,
                timeout=config.SAFETY_CHECK_TIMEOUT,
            )
            if resp.status_code != 200:
                return {"available": False}

            result = resp.json().get("result", {})
            value = result.get("value")
            if not value:
                return {"available": False}

            data_b64 = value.get("data", [None])[0]
            if not data_b64:
                return {"available": False}

            raw = base64.b64decode(data_b64)
            if len(raw) < 82:
                return {"available": False}

            # SPL Token Mint layout:
            # [0:4]   mintAuthorityOption (u32, COption)
            # [4:36]  mintAuthority (Pubkey, 32 bytes)
            # [36:44] supply (u64)
            # [44:45] decimals (u8)
            # [45:46] isInitialized (bool)
            # [46:50] freezeAuthorityOption (u32, COption)
            # [50:82] freezeAuthority (Pubkey, 32 bytes)

            mint_auth_option = struct.unpack_from("<I", raw, 0)[0]
            freeze_auth_option = struct.unpack_from("<I", raw, 46)[0]

            return {
                "available": True,
                "mint_authority_disabled": mint_auth_option == 0,
                "freeze_authority_disabled": freeze_auth_option == 0,
            }
        except Exception as e:
            logger.debug("On-chain mint check error for %s: %s", mint_address[:12], e)
            return {"available": False}

    def _check_jupiter_verified(self, mint_address: str) -> bool:
        """Check if token is on Jupiter's verified token list."""
        try:
            # Lazy load & cache for 1 hour
            now = time.time()
            if self._verified_tokens is None or now - self._verified_loaded_at > 3600:
                resp = self._session.get(JUPITER_TOKEN_LIST_URL, timeout=15)
                if resp.status_code == 200:
                    tokens = resp.json()
                    self._verified_tokens = {
                        t.get("address", "") for t in tokens
                        if t.get("address")
                    }
                    self._verified_loaded_at = now
                else:
                    self._verified_tokens = set()
                    self._verified_loaded_at = now

            return mint_address in (self._verified_tokens or set())
        except Exception as e:
            logger.debug("Jupiter token list error: %s", e)
            return False

    def _simulate_sell(self, mint_address: str) -> dict:
        """
        Honeypot detection: simulate a buy then sell via Jupiter quotes.
        If the sell quote fails (no route), the token is a honeypot.
        High round-trip loss indicates excessive taxes.
        """
        try:
            # Amount: $1 worth of USDC (6 decimals)
            buy_amount = int(config.SELL_SIM_AMOUNT_USD * 1_000_000)

            # Step 1: Quote USDC -> Token
            buy_quote = self._session.get(JUPITER_QUOTE_URL, params={
                "inputMint": USDC_MINT,
                "outputMint": mint_address,
                "amount": str(buy_amount),
                "slippageBps": "500",
            }, timeout=config.SAFETY_CHECK_TIMEOUT)

            if buy_quote.status_code != 200:
                return {"passed": None}  # Can't determine

            buy_data = buy_quote.json()
            out_amount = buy_data.get("outAmount")
            if not out_amount or int(out_amount) <= 0:
                return {"passed": False, "round_trip_tax_pct": 1.0}

            # Step 2: Quote Token -> USDC (reverse)
            sell_quote = self._session.get(JUPITER_QUOTE_URL, params={
                "inputMint": mint_address,
                "outputMint": USDC_MINT,
                "amount": str(out_amount),
                "slippageBps": "500",
            }, timeout=config.SAFETY_CHECK_TIMEOUT)

            if sell_quote.status_code != 200:
                # No sell route = honeypot
                return {"passed": False, "round_trip_tax_pct": 1.0}

            sell_data = sell_quote.json()
            sell_out = sell_data.get("outAmount")
            if not sell_out or int(sell_out) <= 0:
                return {"passed": False, "round_trip_tax_pct": 1.0}

            # Calculate round-trip loss
            round_trip_tax = 1.0 - (int(sell_out) / buy_amount)
            round_trip_tax = max(0.0, round_trip_tax)

            return {
                "passed": True,
                "round_trip_tax_pct": round(round_trip_tax, 4),
            }
        except Exception as e:
            logger.debug("Sell simulation error for %s: %s", mint_address[:12], e)
            return {"passed": None}
