"""Application configuration via pydantic-settings.

Reads from environment variables and .env file. All tool modules
import settings from here rather than reading os.environ directly.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseSettings):
    """Central configuration for the NYC Property Intel server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = "postgresql://nycdb:nycdb@localhost:5432/nycdb"

    # ── NYC GeoClient API ─────────────────────────────────────────────
    nyc_geoclient_app_id: str = ""
    nyc_geoclient_app_key: str = ""
    nyc_geoclient_subscription_key: str = ""

    # ── Socrata Open Data ─────────────────────────────────────────────
    socrata_app_token: str = ""
    socrata_rate_limit_per_hour: int = 5000

    # ── Logging ───────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Auth (HTTP/SSE transport only) ───────────────────────────────
    # Legacy single-token mode — kept for local dev convenience.
    # In production (Railway) this is superseded by DB-backed per-customer auth.
    mcp_server_token: str = ""

    # Set MCP_AUTH_DISABLED=true to bypass all auth checks (local dev only).
    # Never set this in production.
    mcp_auth_disabled: bool = False

    # ── Loops.so integration ─────────────────────────────────────────────
    # API key from https://app.loops.so/settings?page=api
    loops_api_key: str = ""
    # Signing secret from your Loops webhook settings (optional but recommended)
    loops_webhook_secret: str = ""

    # ── PostHog analytics ────────────────────────────────────────────────
    # Project API key from https://us.posthog.com/settings/project
    posthog_api_key: str = ""

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql://") and not v.startswith("postgres://"):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL connection string "
                "(starts with postgresql:// or postgres://)"
            )
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        normalized = v.upper()
        if normalized not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}"
            )
        return normalized

    @property
    def geoclient_configured(self) -> bool:
        """Return True if GeoClient credentials are present."""
        return bool(self.nyc_geoclient_subscription_key) or bool(
            self.nyc_geoclient_app_id and self.nyc_geoclient_app_key
        )

    @property
    def socrata_configured(self) -> bool:
        """Return True if a Socrata app token is present."""
        return bool(self.socrata_app_token)


# Singleton — import this from anywhere.
settings = Settings()
