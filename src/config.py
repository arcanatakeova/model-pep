"""ARCANA AI — Configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

_env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
load_dotenv(_env_path)

ROOT_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT_DIR / "memory"
SOUL_PATH = ROOT_DIR / "SOUL.md"
HEARTBEAT_PATH = ROOT_DIR / "HEARTBEAT.md"
STOP_FILE = ROOT_DIR / "STOP"


class Config(BaseModel):
    # LLM
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    haiku_model: str = "anthropic/claude-3.5-haiku"
    sonnet_model: str = "anthropic/claude-sonnet-4-20250514"
    opus_model: str = "anthropic/claude-opus-4-20250514"

    # X / Twitter
    x_api_key: str = Field(default_factory=lambda: os.getenv("X_API_KEY", ""))
    x_api_secret: str = Field(default_factory=lambda: os.getenv("X_API_SECRET", ""))
    x_access_token: str = Field(default_factory=lambda: os.getenv("X_ACCESS_TOKEN", ""))
    x_access_secret: str = Field(default_factory=lambda: os.getenv("X_ACCESS_SECRET", ""))

    # Payments
    stripe_secret_key: str = Field(default_factory=lambda: os.getenv("STRIPE_SECRET_KEY", ""))
    gumroad_access_token: str = Field(default_factory=lambda: os.getenv("GUMROAD_ACCESS_TOKEN", ""))

    # Notifications
    discord_webhook_url: str = Field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))
    telegram_bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Content
    heygen_api_key: str = Field(default_factory=lambda: os.getenv("HEYGEN_API_KEY", ""))
    elevenlabs_api_key: str = Field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))

    # Operational
    dry_run: bool = Field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")
    morning_report_hour: int = 15  # 7 AM PT = 15:00 UTC
    nightly_review_hour: int = 7   # 11 PM PT = 07:00 UTC next day

    def load_soul(self) -> str:
        if SOUL_PATH.exists():
            return SOUL_PATH.read_text()
        return "You are ARCANA AI, the autonomous AI CEO of Arcana Operations."


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
