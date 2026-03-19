"""ARCANA AI — Configuration from environment variables.

Production-grade configuration with:
- Startup validation and missing key warnings
- Service readiness checks (which channels are operational)
- Grouped key validation (all X keys or none)
- Sensitive key masking for logs
- Runtime config reload support
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("arcana.config")

_env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
load_dotenv(_env_path)

ROOT_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT_DIR / "memory"
SOUL_PATH = ROOT_DIR / "SOUL.md"
HEARTBEAT_PATH = ROOT_DIR / "HEARTBEAT.md"
STOP_FILE = ROOT_DIR / "STOP"
DB_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"


# ── Service groups — all keys in a group must be set together ─────
SERVICE_GROUPS = {
    "llm": ["openrouter_api_key"],
    "x_twitter": ["x_api_key", "x_api_secret", "x_access_token", "x_access_secret"],
    "stripe": ["stripe_secret_key"],
    "discord": ["discord_webhook_url"],
    "telegram": ["telegram_bot_token", "telegram_chat_id"],
    "imessage": ["sendblue_api_key", "sendblue_api_secret", "imessage_ian_number"],
    "email": ["sendgrid_api_key"],
    "outreach": ["apollo_api_key", "instantly_api_key"],
    "newsletter": ["beehiiv_api_key", "beehiiv_publication_id"],
    "ugc": ["heygen_api_key"],
}

# Keys that are CRITICAL — system degrades significantly without them
CRITICAL_KEYS = ["openrouter_api_key"]

# Keys that are IMPORTANT — major features disabled without them
IMPORTANT_KEYS = [
    "x_api_key", "stripe_secret_key",
    "discord_webhook_url", "sendgrid_api_key",
]


class Config(BaseModel):
    """Validated configuration with service readiness checks."""

    model_config = {"arbitrary_types_allowed": True}

    # ── LLM (OpenRouter) ─────────────────────────────────────────
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    haiku_model: str = "anthropic/claude-3.5-haiku"
    sonnet_model: str = "anthropic/claude-sonnet-4-20250514"
    opus_model: str = "anthropic/claude-opus-4-20250514"

    # ── X / Twitter ──────────────────────────────────────────────
    x_api_key: str = Field(default_factory=lambda: os.getenv("X_API_KEY", ""))
    x_api_secret: str = Field(default_factory=lambda: os.getenv("X_API_SECRET", ""))
    x_access_token: str = Field(default_factory=lambda: os.getenv("X_ACCESS_TOKEN", ""))
    x_access_secret: str = Field(default_factory=lambda: os.getenv("X_ACCESS_SECRET", ""))

    # ── Payments ─────────────────────────────────────────────────
    stripe_secret_key: str = Field(default_factory=lambda: os.getenv("STRIPE_SECRET_KEY", ""))
    gumroad_access_token: str = Field(default_factory=lambda: os.getenv("GUMROAD_ACCESS_TOKEN", ""))

    # ── Notifications ────────────────────────────────────────────
    discord_webhook_url: str = Field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))
    discord_bot_token: str = Field(default_factory=lambda: os.getenv("DISCORD_BOT_TOKEN", ""))
    discord_guild_id: str = Field(default_factory=lambda: os.getenv("DISCORD_GUILD_ID", ""))
    telegram_bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # ── iMessage (Sendblue) ────────────────────────────────────
    sendblue_api_key: str = Field(default_factory=lambda: os.getenv("SENDBLUE_API_KEY", ""))
    sendblue_api_secret: str = Field(default_factory=lambda: os.getenv("SENDBLUE_API_SECRET", ""))
    imessage_ian_number: str = Field(default_factory=lambda: os.getenv("IMESSAGE_IAN_NUMBER", ""))
    imessage_tan_number: str = Field(default_factory=lambda: os.getenv("IMESSAGE_TAN_NUMBER", ""))

    # ── Content Production / UGC ────────────────────────────────
    heygen_api_key: str = Field(default_factory=lambda: os.getenv("HEYGEN_API_KEY", ""))
    makeugc_api_key: str = Field(default_factory=lambda: os.getenv("MAKEUGC_API_KEY", ""))
    elevenlabs_api_key: str = Field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))

    # ── Newsletter (Beehiiv) ─────────────────────────────────────
    beehiiv_api_key: str = Field(default_factory=lambda: os.getenv("BEEHIIV_API_KEY", ""))
    beehiiv_publication_id: str = Field(default_factory=lambda: os.getenv("BEEHIIV_PUBLICATION_ID", ""))

    # ── SEO / Publishing ─────────────────────────────────────────
    vercel_token: str = Field(default_factory=lambda: os.getenv("VERCEL_TOKEN", ""))
    google_adsense_id: str = Field(default_factory=lambda: os.getenv("GOOGLE_ADSENSE_ID", ""))

    # ── Email (SendGrid) ────────────────────────────────────────
    sendgrid_api_key: str = Field(default_factory=lambda: os.getenv("SENDGRID_API_KEY", ""))

    # ── Lead Gen / Outreach ──────────────────────────────────────
    apollo_api_key: str = Field(default_factory=lambda: os.getenv("APOLLO_API_KEY", ""))
    instantly_api_key: str = Field(default_factory=lambda: os.getenv("INSTANTLY_API_KEY", ""))

    # ── LinkedIn ─────────────────────────────────────────────────
    linkedin_token: str = Field(default_factory=lambda: os.getenv("LINKEDIN_TOKEN", ""))

    # ── Social Media Management ──────────────────────────────────
    buffer_api_key: str = Field(default_factory=lambda: os.getenv("BUFFER_API_KEY", ""))

    # ── Review Platforms ─────────────────────────────────────────
    google_business_token: str = Field(default_factory=lambda: os.getenv("GOOGLE_BUSINESS_TOKEN", ""))

    # ── Deal Monitoring ──────────────────────────────────────────
    keepa_api_key: str = Field(default_factory=lambda: os.getenv("KEEPA_API_KEY", ""))
    amazon_pa_key: str = Field(default_factory=lambda: os.getenv("AMAZON_PA_KEY", ""))
    amazon_pa_secret: str = Field(default_factory=lambda: os.getenv("AMAZON_PA_SECRET", ""))

    # ── Translation ──────────────────────────────────────────────
    deepl_api_key: str = Field(default_factory=lambda: os.getenv("DEEPL_API_KEY", ""))

    # ── Reddit ───────────────────────────────────────────────────
    reddit_client_id: str = Field(default_factory=lambda: os.getenv("REDDIT_CLIENT_ID", ""))
    reddit_client_secret: str = Field(default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET", ""))
    reddit_refresh_token: str = Field(default_factory=lambda: os.getenv("REDDIT_REFRESH_TOKEN", ""))

    # ── Operational ──────────────────────────────────────────────
    dry_run: bool = Field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")
    morning_report_hour: int = 15   # 7 AM PT = 15:00 UTC
    nightly_review_hour: int = 7    # 11 PM PT = 07:00 UTC next day
    ops_cycle_minutes: int = 15     # Daily ops cycle interval
    max_llm_calls_per_hour: int = Field(
        default_factory=lambda: int(os.getenv("MAX_LLM_CALLS_PER_HOUR", "200")),
    )
    max_x_posts_per_day: int = Field(
        default_factory=lambda: int(os.getenv("MAX_X_POSTS_PER_DAY", "50")),
    )

    # ── Validation ───────────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_and_warn(self) -> "Config":
        """Log warnings for missing keys at startup."""
        # Critical keys
        for key in CRITICAL_KEYS:
            if not getattr(self, key, ""):
                logger.critical("CRITICAL: %s is not set — ARCANA cannot function", key)

        # Important keys
        for key in IMPORTANT_KEYS:
            if not getattr(self, key, ""):
                logger.warning("Missing key: %s — related features disabled", key)

        # Partial service groups (some keys set, others missing)
        for group, keys in SERVICE_GROUPS.items():
            values = [getattr(self, k, "") for k in keys]
            set_count = sum(1 for v in values if v)
            if 0 < set_count < len(keys):
                missing = [k for k, v in zip(keys, values) if not v]
                logger.warning(
                    "Partial config for '%s': missing %s",
                    group, ", ".join(missing),
                )

        if self.dry_run:
            logger.info("DRY RUN mode — no external API calls will be made")

        return self

    # ── Service Readiness ────────────────────────────────────────

    def is_service_ready(self, service: str) -> bool:
        """Check if a service group has all required keys configured."""
        keys = SERVICE_GROUPS.get(service, [])
        if not keys:
            return False
        return all(getattr(self, k, "") for k in keys)

    def get_ready_services(self) -> list[str]:
        """Get list of all fully configured services."""
        return [name for name in SERVICE_GROUPS if self.is_service_ready(name)]

    def get_disabled_services(self) -> list[str]:
        """Get list of services missing configuration."""
        return [name for name in SERVICE_GROUPS if not self.is_service_ready(name)]

    def get_status_report(self) -> str:
        """Generate a config status report for logs/dashboards."""
        ready = self.get_ready_services()
        disabled = self.get_disabled_services()
        lines = [
            f"**Config Status**: {len(ready)}/{len(SERVICE_GROUPS)} services ready",
            f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}",
        ]
        if ready:
            lines.append(f"Ready: {', '.join(ready)}")
        if disabled:
            lines.append(f"Disabled: {', '.join(disabled)}")
        return "\n".join(lines)

    # ── Key Masking ──────────────────────────────────────────────

    @staticmethod
    def mask_key(key: str) -> str:
        """Mask a key for safe logging: sk_live_abc123 → sk_l***123"""
        if not key or len(key) < 8:
            return "***"
        return f"{key[:4]}***{key[-3:]}"

    # ── SOUL Loading ─────────────────────────────────────────────

    def load_soul(self) -> str:
        if SOUL_PATH.exists():
            return SOUL_PATH.read_text()
        return "You are ARCANA AI, the autonomous AI CEO of Arcana Operations."

    # ── Ensure Directories ───────────────────────────────────────

    def ensure_directories(self) -> None:
        """Create all required directories on startup."""
        for d in [MEMORY_DIR, DB_DIR, LOG_DIR,
                  MEMORY_DIR / "daily", MEMORY_DIR / "life",
                  MEMORY_DIR / "tacit"]:
            d.mkdir(parents=True, exist_ok=True)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
        _config.ensure_directories()
    return _config


def reload_config() -> Config:
    """Force reload config from environment (useful after .env changes)."""
    global _config
    load_dotenv(_env_path, override=True)
    _config = Config()
    _config.ensure_directories()
    logger.info("Configuration reloaded")
    return _config
