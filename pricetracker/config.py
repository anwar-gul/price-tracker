"""Runtime configuration: env vars (PRICETRACKER_*) and .env, validated at startup.

Legacy SMTP/alert env names (SMTP_HOST, ALERT_TO, ...) keep working via
AliasChoices so existing deployments do not break.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_FORMATS = ("console", "json")


def _aliased(legacy: str, help_text: str = "") -> Field:
    return Field(
        default=None,
        validation_alias=AliasChoices(legacy, f"PRICETRACKER_{legacy}"),
        description=help_text,
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRICETRACKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage ---
    database_url: str = "sqlite:///data/prices.db"
    alert_log: str = "data/alerts.log"

    # --- logging ---
    log_level: str = "INFO"
    log_format: str = "console"

    # --- alerting ---
    webhook_url: str | None = None
    webhook_retries: int = 3
    webhook_timeout_seconds: float = 10.0
    webhook_backoff_seconds: float = 1.0
    alert_cooldown_hours: float = 24.0
    alert_on_zero_items: bool = True
    alert_max_attempts: int = 5
    alert_retry_base_seconds: int = 60

    smtp_host: str | None = _aliased("SMTP_HOST")
    smtp_port: int = Field(
        default=587, validation_alias=AliasChoices("SMTP_PORT", "PRICETRACKER_SMTP_PORT")
    )
    smtp_user: str | None = _aliased("SMTP_USER")
    smtp_pass: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SMTP_PASS", "PRICETRACKER_SMTP_PASS"),
    )
    alert_from: str | None = _aliased("ALERT_FROM")
    alert_to: str | None = _aliased("ALERT_TO")

    # --- crawl politeness / transport ---
    user_agent: str = "pricetracker/0.2 (set PRICETRACKER_USER_AGENT with contact info)"
    download_delay: float = 0.5
    concurrent_requests_per_domain: int = 4
    autothrottle_target: float = 2.0
    retry_times: int = 3
    download_timeout: int = 30
    proxy_url: str | None = None
    playwright_connect_url: str | None = None

    # --- ops ---
    metrics_port: int = 0
    prune_keep_days: int = 90
    prune_every_hours: float = 24.0

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, value: str) -> str:
        value = value.upper()
        if value not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {', '.join(LOG_LEVELS)}")
        return value

    @field_validator("log_format")
    @classmethod
    def _check_format(cls, value: str) -> str:
        value = value.lower()
        if value not in LOG_FORMATS:
            raise ValueError(f"log_format must be one of {', '.join(LOG_FORMATS)}")
        return value

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("database_url must not be empty")
        if "://" in value:
            scheme = value.split("://", 1)[0]
            if not scheme.startswith(("sqlite", "postgresql", "postgres")):
                raise ValueError(f"unsupported database URL scheme: {scheme}")
        return value

    @field_validator("webhook_url")
    @classmethod
    def _check_webhook(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("webhook_url must be http(s)")
        return value

    @field_validator("alert_cooldown_hours", "webhook_backoff_seconds", "webhook_timeout_seconds",
                     "prune_every_hours")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("must be >= 0")
        return value

    @field_validator("alert_max_attempts", "prune_keep_days")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be >= 1")
        return value

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.alert_to)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
