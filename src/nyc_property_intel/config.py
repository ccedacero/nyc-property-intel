"""Application configuration via pydantic-settings.

Reads from environment variables and .env file. All tool modules
import settings from here rather than reading os.environ directly.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── Logging ───────────────────────────────────────────────────────
    log_level: str = "INFO"

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
