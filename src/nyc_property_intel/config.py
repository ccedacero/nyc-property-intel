"""Application configuration via pydantic-settings.

Reads from environment variables and .env file. All tool modules
import settings from here rather than reading os.environ directly.
"""

from __future__ import annotations

from pydantic import field_validator, model_validator
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
    # Transactional email ID for the web chat activation email
    # Create in Loops: Transactional → New → add {{activationUrl}} variable
    loops_chat_transactional_id: str = ""
    # Transactional email ID for the "watch this building" change alert (1.9).
    # Create in Loops: Transactional → New → add {{address}}, {{changes}},
    # {{reportUrl}} variables. Until set, alerts are computed but not sent.
    loops_watch_transactional_id: str = ""

    # ── PostHog analytics ────────────────────────────────────────────────
    # Project API key from https://us.posthog.com/settings/project
    posthog_api_key: str = ""

    # ── Sentry error tracking ────────────────────────────────────────────
    # DSN from https://sentry.io/settings/<org>/projects/<project>/keys/
    # Empty = no-op (local dev). Set in Railway env vars to enable.
    sentry_dsn: str = ""
    # "production" / "staging" / "development" — surfaces in Sentry UI
    sentry_environment: str = "production"
    # Fraction of transactions to sample for performance tracing.
    # 0.1 = 10%, plenty at our traffic; raise if you need to debug latency.
    sentry_traces_sample_rate: float = 0.1

    # ── Web chat ─────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    # HMAC-SHA256 secret for signing free-tier session cookies
    cookie_secret: str = ""
    # Fernet key for encrypting plaintext token in web_magic_links rows
    web_chat_token_key: str = ""
    # Comma-separated origins allowed to call /api/chat (CORS)
    chat_allowed_origins: str = "https://nycpropertyintel.com"
    # Free queries before email gate (no token required).
    # IP-hash scoped over a 24h rolling window. 3 is the chosen balance:
    # enough for one curious user to try a couple of properties, tight
    # enough to bound COGS exposure given the ~$0.32 cost of full DD
    # reports. Trial value-prop (10/day for 30 days = ~300 total) creates
    # a clear signup incentive. Shared NATs still hit it eventually but
    # the average household / small office is only 1-2 active users at once.
    chat_free_query_limit: int = 3
    # Total queries/day for trial tokens on web chat (resets at midnight UTC).
    # Must match auth.PLAN_LIMITS["trial"] so the chat path and MCP path agree.
    chat_daily_query_limit: int = 10
    # Max analyze_property calls/day for trial tokens (sub-cap of the daily total).
    # Resets at midnight UTC. The remaining (chat_daily_query_limit -
    # chat_analyze_trial_limit) queries are available for other tools / chat.
    chat_analyze_trial_limit: int = 5
    # Secret used when hashing visitor IPs for the anonymous chat tracking
    # table (anon_chat_queries). Hash is sha256(ip || secret)[:32], so we never
    # store the raw IP. If empty we fall back to a random per-process value and
    # log a warning — set ANON_IP_HASH_SECRET in Railway to keep hashes stable
    # across deploys (so the same visitor produces the same ip_hash).
    anon_ip_hash_secret: str = ""

    # Global hard ceiling on anonymous /api/chat queries per rolling hour,
    # server-wide and IP-independent. Backstops the per-IP limit against
    # IP-spoofing / NAT-rotation attacks that grant a fresh allowance per
    # forged IP. When tripped, anon requests return 429 with
    # `free_global_limit_reached`. Authenticated requests are unaffected.
    # Tune up before launch spikes; this is a cost cap, not a quality signal.
    chat_anon_global_hourly_cap: int = 200

    # ── /api/signup Cloudflare Turnstile (deferred — accepted but NOT enforced) ──
    # When True, the new POST /api/signup handler will validate the
    # `turnstile_token` field server-side via Cloudflare's siteverify API
    # using `signup_turnstile_secret`. Today both default to off — see
    # docs/signup-rebuild-plan-2026-05-06.md §1 (deferred for follow-up
    # PR; requires Cloudflare account setup first).
    signup_require_turnstile: bool = False
    signup_turnstile_secret: str = ""

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

    @model_validator(mode="after")
    def validate_web_chat_secrets(self) -> "Settings":
        """Fail loudly at startup if required web-chat secrets are missing.

        Empty cookie_secret means every anonymous cookie is treated as invalid
        (always 0 count → unlimited free queries). Empty web_chat_token_key
        causes a fatal Fernet ValueError on first magic-link activation.
        Both must be set in any non-stdio (HTTP) deployment.
        """
        import os
        transport = os.environ.get("MCP_TRANSPORT", "").lower()
        is_http = transport in ("http", "sse", "streamable-http") or bool(
            os.environ.get("PORT")
        )
        if is_http:
            missing = []
            if not self.cookie_secret:
                missing.append("COOKIE_SECRET")
            if not self.web_chat_token_key:
                missing.append("WEB_CHAT_TOKEN_KEY")
            if missing:
                raise ValueError(
                    f"Required secret(s) not set for HTTP transport: {', '.join(missing)}. "
                    "Set these environment variables in Railway before deploying."
                )
        return self

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
