"""Example: Trading Execution Patterns

Reference patterns for Jupiter (Solana) and Polymarket trading.
Adapt for src/agents/trader.py.
"""
import httpx
import os
from pydantic import BaseModel


# ============================================================
# JUPITER (Solana) SWAP PATTERN
# ============================================================

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


async def jupiter_quote(
    input_mint: str,
    output_mint: str,
    amount_lamports: int,
    slippage_bps: int = 50,  # 0.5%
) -> dict:
    """Get a swap quote from Jupiter V6."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount_lamports,
                "slippageBps": slippage_bps,
            },
        )
        response.raise_for_status()
        return response.json()


async def jupiter_swap(quote: dict, wallet_pubkey: str) -> dict:
    """Execute a Jupiter swap. Returns serialized transaction to sign."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://lite-api.jup.ag/swap/v1/swap",
            json={
                "quoteResponse": quote,
                "userPublicKey": wallet_pubkey,
                "prioritizationFeeLamports": "auto",
                "dynamicComputeUnitLimit": True,
            },
        )
        response.raise_for_status()
        return response.json()


# Note: After getting swap tx, sign with solders and submit via Helius RPC:
# from solders.keypair import Keypair
# from solders.transaction import VersionedTransaction
# keypair = Keypair.from_base58_string(os.getenv("SOLANA_PRIVATE_KEY"))
# tx = VersionedTransaction.from_bytes(base64.b64decode(swap_result["swapTransaction"]))
# tx.sign([keypair])
# Submit via: POST https://mainnet.helius-rpc.com/?api-key=KEY
# body: {"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":[base64.b64encode(bytes(tx))]}


# ============================================================
# POLYMARKET CLOB PATTERN
# ============================================================

async def polymarket_get_markets(
    tag: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Get active markets from Polymarket Gamma API."""
    async with httpx.AsyncClient() as client:
        params = {"limit": limit, "active": True}
        if tag:
            params["tag"] = tag
        response = await client.get(
            "https://gamma-api.polymarket.com/markets",
            params=params,
        )
        response.raise_for_status()
        return response.json()


# For actual trading, use the py-clob-client SDK:
# from py_clob_client.client import ClobClient
# from py_clob_client.clob_types import OrderArgs
#
# client = ClobClient(
#     "https://clob.polymarket.com",
#     key=os.getenv("POLYMARKET_PRIVATE_KEY"),
#     chain_id=137,  # Polygon
# )
#
# # Place an order
# order = client.create_and_post_order(OrderArgs(
#     token_id=condition_token_id,
#     price=0.55,   # Buy YES at $0.55
#     size=100,      # $100 position
#     side="BUY",
# ))


# ============================================================
# RISK GATE (Must pass ALL checks before executing)
# ============================================================

class RiskCheck(BaseModel):
    passed: bool
    reason: str


async def risk_gate(
    asset: str,
    position_size_usd: float,
    portfolio_total: float,
    daily_pnl: float,
    open_positions: int,
    rugcheck_score: int | None = None,
) -> list[RiskCheck]:
    """Run ALL risk checks. Trade only if ALL pass."""
    checks = []
    
    # Position size check
    pct = (position_size_usd / portfolio_total) * 100
    checks.append(RiskCheck(
        passed=pct <= 5.0,
        reason=f"Position {pct:.1f}% of portfolio (max 5%)",
    ))
    
    # Daily drawdown check
    dd_pct = (daily_pnl / portfolio_total) * 100
    checks.append(RiskCheck(
        passed=dd_pct > -10.0,
        reason=f"Daily P&L {dd_pct:.1f}% (circuit breaker at -10%)",
    ))
    
    # Open positions check (Solana only)
    checks.append(RiskCheck(
        passed=open_positions < 3,
        reason=f"{open_positions}/3 Solana positions open",
    ))
    
    # Rugcheck score (Solana tokens only)
    if rugcheck_score is not None:
        checks.append(RiskCheck(
            passed=rugcheck_score < 50,
            reason=f"Rugcheck score {rugcheck_score}/100 (max 50)",
        ))
    
    return checks


# ============================================================
# SIGNAL AGGREGATION PATTERN
# ============================================================

async def dexscreener_trending() -> list[dict]:
    """Get trending tokens from DexScreener (free, no auth)."""
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.dexscreener.com/token-boosts/latest/v1")
        response.raise_for_status()
        return response.json()


async def rugcheck_report(mint: str) -> dict:
    """Get token safety report from Rugcheck."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")
        response.raise_for_status()
        return response.json()


async def birdeye_token_overview(mint: str) -> dict:
    """Get token overview from Birdeye."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://public-api.birdeye.so/defi/token_overview",
            params={"address": mint},
            headers={"X-API-KEY": os.getenv("BIRDEYE_API_KEY", "")},
        )
        response.raise_for_status()
        return response.json()
