"""ARCANA AI — Centralized configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env from config/ directory
_env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
load_dotenv(_env_path)


class LLMConfig(BaseModel):
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    haiku_model: str = "anthropic/claude-3.5-haiku"
    sonnet_model: str = "anthropic/claude-sonnet-4-20250514"
    opus_model: str = "anthropic/claude-opus-4-20250514"


class TradingConfig(BaseModel):
    dry_run: bool = Field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")
    trading_capital: float = Field(default_factory=lambda: float(os.getenv("TRADING_CAPITAL", "1000")))
    max_position_pct: float = Field(default_factory=lambda: float(os.getenv("MAX_POSITION_PCT", "5")))
    stop_loss_pct: float = Field(default_factory=lambda: float(os.getenv("STOP_LOSS_PCT", "15")))
    daily_drawdown_limit_pct: float = Field(default_factory=lambda: float(os.getenv("DAILY_DRAWDOWN_LIMIT_PCT", "10")))
    max_open_solana_positions: int = 3
    rugcheck_max_score: int = 50

    # Solana
    solana_private_key: str = Field(default_factory=lambda: os.getenv("SOLANA_PRIVATE_KEY", ""))
    solana_rpc_url: str = Field(default_factory=lambda: os.getenv("SOLANA_RPC_URL", ""))

    # Polymarket
    polymarket_private_key: str = Field(default_factory=lambda: os.getenv("POLYMARKET_PRIVATE_KEY", ""))

    # Coinbase
    coinbase_api_key: str = Field(default_factory=lambda: os.getenv("COINBASE_API_KEY", ""))
    coinbase_api_secret: str = Field(default_factory=lambda: os.getenv("COINBASE_API_SECRET", ""))


class MarketDataConfig(BaseModel):
    birdeye_api_key: str = Field(default_factory=lambda: os.getenv("BIRDEYE_API_KEY", ""))
    unusual_whales_api_key: str = Field(default_factory=lambda: os.getenv("UNUSUAL_WHALES_API_KEY", ""))
    finnhub_api_key: str = Field(default_factory=lambda: os.getenv("FINNHUB_API_KEY", ""))


class XConfig(BaseModel):
    api_key: str = Field(default_factory=lambda: os.getenv("X_API_KEY", ""))
    api_secret: str = Field(default_factory=lambda: os.getenv("X_API_SECRET", ""))
    access_token: str = Field(default_factory=lambda: os.getenv("X_ACCESS_TOKEN", ""))
    access_secret: str = Field(default_factory=lambda: os.getenv("X_ACCESS_SECRET", ""))


class ContentConfig(BaseModel):
    heygen_api_key: str = Field(default_factory=lambda: os.getenv("HEYGEN_API_KEY", ""))
    makeugc_api_key: str = Field(default_factory=lambda: os.getenv("MAKEUGC_API_KEY", ""))
    elevenlabs_api_key: str = Field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))


class PaymentsConfig(BaseModel):
    stripe_secret_key: str = Field(default_factory=lambda: os.getenv("STRIPE_SECRET_KEY", ""))
    gumroad_access_token: str = Field(default_factory=lambda: os.getenv("GUMROAD_ACCESS_TOKEN", ""))


class DatabaseConfig(BaseModel):
    supabase_url: str = Field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_anon_key: str = Field(default_factory=lambda: os.getenv("SUPABASE_ANON_KEY", ""))
    supabase_service_key: str = Field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_KEY", ""))


class NotificationConfig(BaseModel):
    discord_webhook_url: str = Field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))
    telegram_bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


class ArcanaConfig(BaseModel):
    """Root configuration for ARCANA AI."""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    x: XConfig = Field(default_factory=XConfig)
    content: ContentConfig = Field(default_factory=ContentConfig)
    payments: PaymentsConfig = Field(default_factory=PaymentsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    orchestrator_interval_minutes: int = Field(
        default_factory=lambda: int(os.getenv("ORCHESTRATOR_INTERVAL_MINUTES", "15"))
    )
    soul_md_path: Path = Path(__file__).resolve().parent.parent / "SOUL.md"

    def load_soul(self) -> str:
        """Load SOUL.md personality file."""
        if self.soul_md_path.exists():
            return self.soul_md_path.read_text()
        return "You are ARCANA AI, the Chief Intelligence Officer of Arcana Operations."


def get_config() -> ArcanaConfig:
    """Get the singleton configuration."""
    return ArcanaConfig()
