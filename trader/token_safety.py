"""
Token Safety — Rug Pull Detection, Honeypot Simulation, On-Chain Verification
==============================================================================
Aggregates data from multiple sources to produce a composite safety score:

1. Birdeye API (preferred)       — real-time security data, mint/freeze authority,
                                   top-holder concentration, Token-2022 detection
2. RugCheck API (free fallback)  — risk assessment, holder analysis
3. Solana RPC (getAccountInfo)   — on-chain mint/freeze authority (ground truth)
4. Jupiter Quote API             — sell simulation for honeypot detection

Critical rules:
- Confirmed honeypots (sell simulation fails) → score = 0, hard block
- Mint authority active          → -0.25 (can inflate supply)
- Freeze authority active        → -0.20 (can freeze wallets)
- Creator holds > 20%            → -0.30 (rug risk)
- Top 10 holders > 70%           → -0.30 (extreme concentration for memecoins)
- RugCheck DANGER rating         → -0.20

Safety bypass fix: is_safe_to_trade = score >= MIN_SAFETY_SCORE (raised to 0.45).
Any token that is a confirmed honeypot gets score=0 regardless of momentum.
"""
from __future__ import annotations

import base64
import concurrent.futures
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from requests.adapters import HTTPAdapter as _HTTPAdapter

import config

_session = requests.Session()
_session.mount("https://", _HTTPAdapter(pool_connections=20, pool_maxsize=50))
_session.mount("http://",  _HTTPAdapter(pool_connections=20, pool_maxsize=50))

logger = logging.getLogger(__name__)

RUGCHECK_BASE       = "https://api.rugcheck.xyz/v1"
JUPITER_QUOTE_URL   = "https://quote-api.jup.ag/v6/quote"
JUPITER_TOKEN_LIST  = "https://token.jup.ag/all"
USDC_MINT           = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# SPL Token Mint layout offsets
_SPL_MINT_AUTH_OPTION  = 0    # u32 (COption: 0=None, 1=Some)
_SPL_MINT_AUTH_KEY     = 4    # Pubkey 32 bytes
_SPL_FREEZE_AUTH_OPTION = 46  # u32 (COption)
_SPL_FREEZE_AUTH_KEY   = 50   # Pubkey 32 bytes
_SPL_MINT_MIN_LEN      = 82

# Token-2022 extended mint: header is same 82 bytes, followed by extension data.
# We only need the first 82 bytes to read authority options.
_TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


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
    is_token_2022: bool = False

    # RugCheck data
    rugcheck_risk: Optional[str] = None
    rugcheck_score: Optional[float] = None
    top_10_holder_pct: Optional[float] = None
    creator_pct: Optional[float] = None
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
    Priority: Birdeye (paid) → RugCheck (free) → Solana RPC → Jupiter
    """

    def __init__(self, solana_rpc_url: str = None):
        self._rpc_url  = solana_rpc_url or config.SOLANA_RPC_URL
        self._session  = requests.Session()
        self._session.headers["User-Agent"] = "ai-trader-safety/2.0"
        self._cache: dict[str, tuple[TokenSafetyReport, float]] = {}
        self._cache_ttl = config.RUGCHECK_CACHE_TTL
        self._verified_tokens: Optional[set] = None
        self._verified_loaded_at = 0.0

        # Lazy-import Birdeye client to avoid circular deps
        self._birdeye = None
        self._session.mount("https://", _HTTPAdapter(pool_connections=20, pool_maxsize=100))
        self._session.mount("http://",  _HTTPAdapter(pool_connections=20, pool_maxsize=100))

    def _get_birdeye(self):
        """Lazy-load Birdeye client."""
        if self._birdeye is None and config.BIRDEYE_API_KEY:
            try:
                from birdeye import BirdeyeClient
                self._birdeye = BirdeyeClient(config.BIRDEYE_API_KEY)
            except Exception:
                pass
        return self._birdeye

    # ─── Public API ────────────────────────────────────────────────────────────

    def check_token_safety(self, mint_address: str) -> TokenSafetyReport:
        """
        Run all safety checks concurrently and return a composite report.
        Cached for RUGCHECK_CACHE_TTL seconds.

        All 4 external checks (Birdeye, RugCheck, RPC on-chain, sell simulation)
        fire in parallel so total latency ≈ slowest single check (~8s) not sum (~32s).
        """
        now = time.time()
        if mint_address in self._cache:
            report, ts = self._cache[mint_address]
            if now - ts < self._cache_ttl:
                return report

        score = 1.0
        flags: list[str] = []

        # ── Run all checks concurrently ───────────────────────────────────
        with concurrent.futures.ThreadPoolExecutor(max_workers=5,
                                                   thread_name_prefix="safety") as ex:
            # Birdeye security
            def _birdeye():
                be = self._get_birdeye()
                if be:
                    try:
                        return be.get_security(mint_address)
                    except Exception as e:
                        logger.debug("Birdeye security error %s: %s", mint_address[:12], e)
                return None

            f_birdeye  = ex.submit(_birdeye)
            f_rugcheck = ex.submit(self._check_rugcheck,         mint_address)
            f_onchain  = ex.submit(self._check_on_chain_mint,    mint_address)
            f_jup      = ex.submit(self._check_jupiter_verified, mint_address)
            f_sell     = ex.submit(
                self._simulate_sell if config.ENABLE_SELL_SIMULATION
                else lambda _: {},
                mint_address)

            timeout = config.SAFETY_CHECK_TIMEOUT + 2   # slight buffer over per-check timeout
            try: birdeye_sec  = f_birdeye.result(timeout=timeout)
            except Exception: birdeye_sec = None
            try: rc           = f_rugcheck.result(timeout=timeout)
            except Exception: rc = {"available": False}
            try: onchain      = f_onchain.result(timeout=timeout)
            except Exception: onchain = {"available": False}
            try: jup_verified = f_jup.result(timeout=timeout)
            except Exception: jup_verified = False
            try: sell_sim     = f_sell.result(timeout=timeout)
            except Exception: sell_sim = {"passed": None}

        # ─── HARD BLOCK: Confirmed honeypot ─────────────────────────────────
        # Only hard-block when we KNOW it's a honeypot:
        #   False  = buy route exists but sell route doesn't → can buy, can't sell = honeypot
        #   None   = API error / new token not yet indexed  → inconclusive, apply penalty only
        sell_passed    = sell_sim.get("passed")
        round_trip_tax = sell_sim.get("round_trip_tax_pct")
        if sell_passed is False and config.BLOCK_HONEYPOTS:
            report = TokenSafetyReport(
                mint_address=mint_address,
                safety_score=0.0,
                is_safe_to_trade=False,
                risk_level="CRITICAL",
                sell_simulation_passed=False,
                round_trip_tax_pct=1.0,
                risk_flags=["HONEYPOT: buy route exists but no sell route"],
                checked_at=now,
            )
            self._store(mint_address, report, now)
            return report
        # Inconclusive sell sim (None = timeout / new token not yet indexed by Jupiter)
        # → apply a -0.25 safety penalty but do not hard-block
        if sell_passed is None:
            score -= 0.25
            flags.append("Sell simulation inconclusive (new token or API timeout)")

        # ─── Resolve authority data (Birdeye > RPC > RugCheck) ───────────────
        mint_disabled   = self._resolve(birdeye_sec, "mint_authority",   onchain, rc, "mint_authority_disabled", invert=True)
        freeze_disabled = self._resolve(birdeye_sec, "freeze_authority", onchain, rc, "freeze_authority_disabled", invert=True)
        is_token_2022   = (birdeye_sec.is_token_2022 if birdeye_sec else
                           onchain.get("is_token_2022", False))

        # Mint authority
        if mint_disabled is False:
            score -= 0.25
            flags.append("Mint authority active — can inflate supply")
        elif mint_disabled is True and freeze_disabled is True:
            score += 0.08  # Both disabled = clean

        # Freeze authority
        if freeze_disabled is False:
            score -= 0.20
            flags.append("Freeze authority active — can freeze wallets")

        # ─── Creator concentration (Birdeye) ──────────────────────────────────
        creator_pct = birdeye_sec.creator_balance_pct if birdeye_sec else None
        if creator_pct is not None:
            if creator_pct > 0.20:
                score -= 0.30
                flags.append(f"Creator holds {creator_pct:.0%} of supply (rug risk)")
            elif creator_pct > 0.10:
                score -= 0.12
                flags.append(f"Creator holds {creator_pct:.0%}")

        # ─── Top holder concentration ──────────────────────────────────────────
        # Birdeye is more accurate; fall back to RugCheck
        top10 = (birdeye_sec.top10_holder_pct if birdeye_sec and birdeye_sec.top10_holder_pct
                 else rc.get("top_10_holder_pct"))
        if top10 is not None:
            # Normalize to [0, 1] — some APIs return integer percentage (e.g. 75 instead of 0.75)
            if top10 > 1:
                top10 = top10 / 100.0
            if top10 > 0.90:
                score -= 0.35
                flags.append(f"Top 10 holders own {top10:.0%} (extreme concentration)")
            elif top10 > 0.80:
                score -= 0.20
                flags.append(f"Top 10 holders own {top10:.0%} (high concentration)")
            elif top10 > 0.65:
                score -= 0.08
                flags.append(f"Top 10 holders own {top10:.0%}")

        # ─── LP lock status ────────────────────────────────────────────────────
        lp_locked = rc.get("lp_locked")
        if lp_locked is True:
            score += 0.05
        elif lp_locked is False:
            score -= 0.12
            flags.append("Liquidity not locked")

        # ─── RugCheck risk level ───────────────────────────────────────────────
        rc_risk = rc.get("risk_level")
        if rc_risk == "Good":
            score += 0.08
        elif rc_risk == "Danger":
            score -= 0.20
            flags.append("RugCheck: DANGER rating")
        elif rc_risk == "Warning":
            score -= 0.08
            flags.append("RugCheck: WARNING rating")

        # ─── Data unavailability penalties ────────────────────────────────────
        if not birdeye_sec and not rc.get("available", False):
            score -= 0.08  # No data from either source
        if not onchain.get("available", False):
            score -= 0.05

        # ─── Jupiter verified bonus ────────────────────────────────────────────
        if jup_verified:
            score += 0.05

        # ─── Round-trip tax penalty ────────────────────────────────────────────
        if round_trip_tax is not None:
            if round_trip_tax > config.MAX_ROUND_TRIP_TAX_PCT:
                score -= 0.20   # Was -0.30: too harsh for memecoins with built-in fees
                flags.append(f"High tax: {round_trip_tax:.0%} round-trip loss")
            elif round_trip_tax > 0.15:
                score -= 0.08
                flags.append(f"Moderate tax: {round_trip_tax:.0%} round-trip")

        # ─── Clamp and classify ────────────────────────────────────────────────
        score = max(0.0, min(1.0, score))
        if score >= 0.80:
            risk_level = "SAFE"
        elif score >= 0.60:
            risk_level = "LOW"
        elif score >= 0.45:
            risk_level = "MEDIUM"
        elif score >= 0.25:
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
            is_token_2022=bool(is_token_2022),
            rugcheck_risk=rc_risk,
            rugcheck_score=rc.get("score"),
            top_10_holder_pct=top10,
            creator_pct=creator_pct,
            lp_locked=lp_locked,
            sell_simulation_passed=sell_passed,
            round_trip_tax_pct=round_trip_tax,
            risk_flags=flags,
            checked_at=now,
        )
        self._store(mint_address, report, now)
        return report

    # ─── Data sources ──────────────────────────────────────────────────────────

    def _check_rugcheck(self, mint_address: str) -> dict:
        """Query RugCheck API for token risk assessment."""
        try:
            resp = None
            for _rc_attempt in range(3):
                resp = self._session.get(
                    f"{RUGCHECK_BASE}/tokens/{mint_address}/report/summary",
                    timeout=config.SAFETY_CHECK_TIMEOUT,
                )
                if resp.status_code != 429:
                    break
                time.sleep(2 ** _rc_attempt)
            if resp is None or resp.status_code != 200:
                return {"available": False}

            data = resp.json()
            risks = data.get("risks", [])

            # Top-10 holder concentration
            top10_pct = None
            top_holders = data.get("topHolders", [])
            if top_holders:
                total = sum(h.get("pct", 0) for h in top_holders[:10])
                # RugCheck returns % as 0-100 if total > 1, else 0-1
                top10_pct = total / 100.0 if total > 1 else total

            # Risk level from numeric score
            score_val = data.get("score", 0)
            if score_val >= 800:
                risk_level = "Good"
            elif score_val >= 400:
                risk_level = "Warning"
            else:
                risk_level = "Danger"

            # Mint/freeze from tokenMeta
            mint_disabled = freeze_disabled = None
            token_meta = data.get("tokenMeta", {})
            if "mintAuthority" in token_meta:
                mint_disabled = token_meta["mintAuthority"] is None
            if "freezeAuthority" in token_meta:
                freeze_disabled = token_meta["freezeAuthority"] is None

            # LP lock from risk names
            lp_locked = None
            for r in risks:
                name = r.get("name", "").lower()
                if "lp" in name and "unlocked" in name:
                    lp_locked = False
                elif "lp" in name and ("locked" in name or "burned" in name):
                    lp_locked = True

            return {
                "available":               True,
                "score":                   score_val,
                "risk_level":              risk_level,
                "top_10_holder_pct":       top10_pct,
                "mint_authority_disabled": mint_disabled,
                "freeze_authority_disabled": freeze_disabled,
                "lp_locked":               lp_locked,
            }
        except Exception as e:
            logger.debug("RugCheck error for %s: %s", mint_address[:12], e)
            return {"available": False}

    def _check_on_chain_mint(self, mint_address: str) -> dict:
        """
        Parse SPL Token mint account on-chain via Solana RPC.
        Supports both legacy SPL Token (82 bytes) and Token-2022 (>82 bytes).
        """
        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
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
            value  = result.get("value")
            if not value:
                return {"available": False}

            # Detect Token-2022 by owner program
            owner = value.get("owner", "")
            is_t22 = (owner == _TOKEN_2022_PROGRAM)

            data_b64 = value.get("data", [None])[0]
            if not data_b64:
                return {"available": False}

            raw = base64.b64decode(data_b64)
            if len(raw) < _SPL_MINT_MIN_LEN:
                return {"available": False}

            # Both SPL Token and Token-2022 share the same first 82-byte layout
            mint_auth_option   = struct.unpack_from("<I", raw, _SPL_MINT_AUTH_OPTION)[0]
            freeze_auth_option = struct.unpack_from("<I", raw, _SPL_FREEZE_AUTH_OPTION)[0]

            return {
                "available":               True,
                "mint_authority_disabled": mint_auth_option == 0,
                "freeze_authority_disabled": freeze_auth_option == 0,
                "is_token_2022":           is_t22,
            }
        except Exception as e:
            logger.debug("On-chain mint check error %s: %s", mint_address[:12], e)
            return {"available": False}

    def _check_jupiter_verified(self, mint_address: str) -> bool:
        """Check if token is on Jupiter's verified token list (cached 1h)."""
        try:
            now = time.time()
            if self._verified_tokens is None or now - self._verified_loaded_at > 3600:
                resp = self._session.get(JUPITER_TOKEN_LIST, timeout=15)
                if resp.status_code == 200:
                    tokens = resp.json()
                    self._verified_tokens = {
                        t.get("address", "") for t in tokens if t.get("address")
                    }
                else:
                    self._verified_tokens = set()
                self._verified_loaded_at = now
            return mint_address in (self._verified_tokens or set())
        except Exception as e:
            logger.debug("Jupiter token list error: %s", e)
            return False

    def _simulate_sell(self, mint_address: str) -> dict:
        """
        Honeypot detection: simulate buy → sell via Jupiter quotes.
        No sell route = confirmed honeypot.
        High round-trip loss = excessive taxes.
        """
        try:
            buy_amount = int(config.SELL_SIM_AMOUNT_USD * 1_000_000)

            # Step 1: Quote USDC → Token
            buy_resp = self._session.get(JUPITER_QUOTE_URL, params={
                "inputMint":  USDC_MINT,
                "outputMint": mint_address,
                "amount":     str(buy_amount),
                "slippageBps": "500",
            }, timeout=config.SAFETY_CHECK_TIMEOUT)

            if buy_resp.status_code != 200:
                return {"passed": None}

            buy_data   = buy_resp.json()
            out_amount = buy_data.get("outAmount")
            if not out_amount or int(out_amount) <= 0:
                # No buy route — token not yet indexed by Jupiter (brand-new token).
                # This is NOT a honeypot signal; return None so the caller applies a
                # soft penalty instead of a hard block.
                return {"passed": None}

            # Buy route confirmed. Now check if we can sell back.
            sell_resp = self._session.get(JUPITER_QUOTE_URL, params={
                "inputMint":  mint_address,
                "outputMint": USDC_MINT,
                "amount":     str(out_amount),
                "slippageBps": "500",
            }, timeout=config.SAFETY_CHECK_TIMEOUT)

            if sell_resp.status_code != 200:
                # Buy route exists but no sell route → confirmed honeypot
                return {"passed": False, "round_trip_tax_pct": 1.0}

            sell_data = sell_resp.json()
            sell_out  = sell_data.get("outAmount")
            if not sell_out or int(sell_out) <= 0:
                return {"passed": False, "round_trip_tax_pct": 1.0}

            round_trip_tax = max(0.0, 1.0 - int(sell_out) / buy_amount)
            return {
                "passed":            True,
                "round_trip_tax_pct": round(round_trip_tax, 4),
            }
        except Exception as e:
            logger.debug("Sell simulation error %s: %s", mint_address[:12], e)
            return {"passed": None}

    # ─── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve(birdeye_sec, be_attr: str,
                 onchain: dict, rc: dict, rc_key: str,
                 invert: bool = False) -> Optional[bool]:
        """
        Resolve an authority status with Birdeye > RPC > RugCheck priority.
        For 'mint_authority': birdeye returns the key string or None.
        invert=True → None means disabled=True.
        """
        # Birdeye: attribute is the key string (None = disabled)
        if birdeye_sec is not None:
            val = getattr(birdeye_sec, be_attr, "MISSING")
            if val != "MISSING":
                return (val is None) if invert else val

        # On-chain RPC (most reliable after Birdeye)
        if onchain.get("available") and rc_key in onchain:
            return onchain[rc_key]

        # RugCheck fallback
        if rc.get("available") and rc_key in rc:
            return rc[rc_key]

        return None

    def _store(self, mint_address: str, report: TokenSafetyReport, now: float):
        """Store report in cache with eviction."""
        self._cache[mint_address] = (report, now)
        if len(self._cache) > 500:
            self._cache = {
                k: v for k, v in self._cache.items()
                if now - v[1] < self._cache_ttl
            }
