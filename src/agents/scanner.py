"""ARCANA AI — Scanner Agent
Aggregates market signals from DexScreener, Birdeye, Unusual Whales, Finnhub, Rugcheck.
Stores signals in Supabase. Escalates anomalies for trading decisions.

Five-Layer Signal Pipeline:
1. Data Collection (this agent)
2. Signal Detection (Haiku monitors, anomaly → escalate to Sonnet)
3. Multi-Model Ensemble (3+ models for high-conviction)
4. Risk Gate (all trades must pass)
5. Execution + Documentation
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

from src.config import ArcanaConfig
from src.utils.db import log_action
from src.utils.llm import LLMClient, ModelTier
from src.utils.memory import MemorySystem

logger = logging.getLogger("arcana.scanner")


class Signal(BaseModel):
    source: str
    signal_type: str
    asset: str | None = None
    data: dict[str, Any] = {}
    severity: str = "low"  # low, medium, high
    timestamp: datetime | None = None


class Scanner:
    """Aggregates market signals across multiple data sources."""

    def __init__(
        self,
        config: ArcanaConfig,
        llm: LLMClient,
        db: Any,
        memory: MemorySystem,
    ) -> None:
        self.config = config
        self.llm = llm
        self.db = db
        self.memory = memory
        self._client = httpx.AsyncClient(timeout=30.0)

    async def get_available_actions(self) -> list:
        """Return available scanning actions for the orchestrator."""
        from src.orchestrator import Action
        return [
            Action(
                agent="scanner",
                name="scan_markets",
                description="Aggregate signals from all market data sources",
                expected_revenue=0,
                probability=1.0,
                time_hours=0.05,
                risk=1.0,
            ),
            Action(
                agent="scanner",
                name="scan_trending_tokens",
                description="Check DexScreener for trending Solana tokens",
                expected_revenue=50,
                probability=0.1,
                time_hours=0.05,
                risk=1.0,
            ),
        ]

    async def execute_action(self, action: Any) -> dict[str, Any]:
        """Execute a scanning action."""
        if action.name == "scan_markets":
            return await self.scan_all()
        elif action.name == "scan_trending_tokens":
            return await self.scan_dexscreener_trending()
        return {"status": "unknown_action"}

    async def scan_all(self) -> dict[str, Any]:
        """Run all scanners and aggregate signals."""
        signals: list[Signal] = []
        errors: list[str] = []

        scanners = [
            ("dexscreener", self.scan_dexscreener_trending),
            ("birdeye", self.scan_birdeye_trending),
            ("finnhub", self.scan_finnhub_news),
        ]

        for name, scanner_fn in scanners:
            try:
                result = await scanner_fn()
                if result.get("signals"):
                    signals.extend(result["signals"])
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                logger.error("Scanner %s failed: %s", name, exc)

        # Analyze signals with Haiku for anomalies
        high_severity = [s for s in signals if s.severity == "high"]
        if high_severity:
            await self._escalate_signals(high_severity)

        # Store signals in memory
        if signals:
            summary = f"Market scan: {len(signals)} signals ({len(high_severity)} high severity)"
            await self.memory.store(
                summary,
                category="market_pattern",
                importance_score=min(1.0, len(high_severity) * 0.3),
                metadata={"signal_count": len(signals), "high_count": len(high_severity)},
            )

        await log_action(
            self.db, "scanner", "scan_all",
            details={"signals": len(signals), "high_severity": len(high_severity), "errors": errors},
        )

        return {
            "signals": len(signals),
            "high_severity": len(high_severity),
            "errors": errors,
        }

    async def scan_dexscreener_trending(self) -> dict[str, Any]:
        """Fetch trending tokens from DexScreener."""
        signals = []
        try:
            resp = await self._client.get("https://api.dexscreener.com/token-boosts/latest/v1")
            resp.raise_for_status()
            tokens = resp.json()

            for token in tokens[:20]:  # Top 20
                chain = token.get("chainId", "")
                if chain != "solana":
                    continue

                signal = Signal(
                    source="dexscreener",
                    signal_type="trending_token",
                    asset=token.get("tokenAddress"),
                    data={
                        "description": token.get("description", ""),
                        "amount": token.get("amount", 0),
                        "total_amount": token.get("totalAmount", 0),
                        "url": token.get("url", ""),
                    },
                    severity="medium" if token.get("totalAmount", 0) > 500 else "low",
                    timestamp=datetime.now(timezone.utc),
                )
                signals.append(signal)

            logger.info("DexScreener: %d trending Solana tokens", len(signals))
        except Exception as exc:
            logger.error("DexScreener scan failed: %s", exc)

        return {"signals": signals}

    async def scan_birdeye_trending(self) -> dict[str, Any]:
        """Fetch trending token data from Birdeye."""
        signals = []
        if not self.config.market_data.birdeye_api_key:
            return {"signals": signals}

        try:
            headers = {"X-API-KEY": self.config.market_data.birdeye_api_key}
            resp = await self._client.get(
                "https://public-api.birdeye.so/defi/token_trending",
                headers=headers,
                params={"sort_by": "rank", "sort_type": "asc", "offset": 0, "limit": 20},
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("data", {}).get("items", []):
                signal = Signal(
                    source="birdeye",
                    signal_type="trending_token",
                    asset=item.get("address"),
                    data={
                        "symbol": item.get("symbol", ""),
                        "name": item.get("name", ""),
                        "price": item.get("price", 0),
                        "volume_24h": item.get("volume24h", 0),
                        "price_change_24h": item.get("priceChange24h", 0),
                    },
                    severity="high" if abs(item.get("priceChange24h", 0)) > 50 else "medium",
                    timestamp=datetime.now(timezone.utc),
                )
                signals.append(signal)

            logger.info("Birdeye: %d trending tokens", len(signals))
        except Exception as exc:
            logger.error("Birdeye scan failed: %s", exc)

        return {"signals": signals}

    async def scan_finnhub_news(self) -> dict[str, Any]:
        """Fetch crypto news sentiment from Finnhub."""
        signals = []
        if not self.config.market_data.finnhub_api_key:
            return {"signals": signals}

        try:
            resp = await self._client.get(
                "https://finnhub.io/api/v1/news",
                params={"category": "crypto", "token": self.config.market_data.finnhub_api_key},
            )
            resp.raise_for_status()
            articles = resp.json()

            for article in articles[:10]:
                signal = Signal(
                    source="finnhub",
                    signal_type="news",
                    data={
                        "headline": article.get("headline", ""),
                        "source": article.get("source", ""),
                        "summary": article.get("summary", "")[:200],
                        "url": article.get("url", ""),
                    },
                    severity="low",
                    timestamp=datetime.now(timezone.utc),
                )
                signals.append(signal)

            # Use Haiku to detect sentiment anomalies
            if articles:
                headlines = "\n".join(a.get("headline", "") for a in articles[:10])
                analysis = await self.llm.complete(
                    f"Analyze these crypto headlines for major sentiment shifts. "
                    f"Rate overall sentiment -1.0 (bearish) to 1.0 (bullish). "
                    f"Flag any breaking news. Respond in JSON: "
                    f'{{"sentiment": float, "breaking": bool, "summary": str}}\n\n{headlines}',
                    tier=ModelTier.HAIKU,
                    json_mode=True,
                )
                logger.info("Finnhub sentiment: %s", analysis[:100])

        except Exception as exc:
            logger.error("Finnhub scan failed: %s", exc)

        return {"signals": signals}

    async def check_rugcheck(self, token_mint: str) -> dict[str, Any]:
        """Check a token's safety score on Rugcheck."""
        try:
            resp = await self._client.get(f"https://api.rugcheck.xyz/v1/tokens/{token_mint}/report")
            resp.raise_for_status()
            data = resp.json()

            score = data.get("score", 100)
            risks = data.get("risks", [])
            mint_authority = any(r.get("name") == "Mint Authority" for r in risks)
            freeze_authority = any(r.get("name") == "Freeze Authority" for r in risks)

            top_holders = data.get("topHolders", [])
            top10_pct = sum(h.get("pct", 0) for h in top_holders[:10])

            return {
                "score": score,
                "safe": score < self.config.trading.rugcheck_max_score,
                "mint_authority": mint_authority,
                "freeze_authority": freeze_authority,
                "top10_holder_pct": top10_pct,
                "risks": [r.get("name", "") for r in risks],
            }
        except Exception as exc:
            logger.error("Rugcheck failed for %s: %s", token_mint, exc)
            return {"score": 100, "safe": False, "error": str(exc)}

    async def _escalate_signals(self, signals: list[Signal]) -> None:
        """Escalate high-severity signals to Sonnet for deeper analysis."""
        signal_text = "\n".join(
            f"- [{s.source}] {s.signal_type}: {s.asset or 'N/A'} — {s.data}"
            for s in signals
        )

        analysis = await self.llm.complete(
            f"Analyze these high-severity market signals and recommend actions:\n{signal_text}\n\n"
            f"For each signal, provide:\n"
            f"1. Is this actionable? (yes/no)\n"
            f"2. Recommended action (buy/sell/monitor/ignore)\n"
            f"3. Conviction level (1-10)\n"
            f"4. Key risks\n"
            f"Respond in JSON format.",
            tier=ModelTier.SONNET,
            json_mode=True,
        )
        logger.info("Signal escalation analysis: %s", analysis[:200])

        await self.memory.store(
            f"High-severity signal analysis: {analysis[:500]}",
            category="market_pattern",
            importance_score=0.8,
            metadata={"signals": [s.model_dump() for s in signals]},
        )
