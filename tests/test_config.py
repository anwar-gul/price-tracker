from __future__ import annotations

import pytest
from pydantic import ValidationError

from pricetracker.config import Settings


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_defaults():
    settings = make_settings()
    assert settings.database_url == "sqlite:///data/prices.db"
    assert settings.alert_cooldown_hours == 24.0
    assert settings.retry_times == 3
    assert settings.email_configured is False
    assert settings.webhook_configured is False


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("PRICETRACKER_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    monkeypatch.setenv("PRICETRACKER_WEBHOOK_URL", "https://hooks.example.com/abc")
    monkeypatch.setenv("PRICETRACKER_ALERT_COOLDOWN_HOURS", "6")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/x"
    assert settings.webhook_configured is True
    assert settings.alert_cooldown_hours == 6.0


def test_legacy_smtp_env_names(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_TO", "ops@example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    settings = Settings(_env_file=None)
    assert settings.smtp_host == "smtp.example.com"
    assert settings.alert_to == "ops@example.com"
    assert settings.smtp_port == 2525
    assert settings.email_configured is True


def test_prefixed_smtp_names_also_work(monkeypatch):
    monkeypatch.setenv("PRICETRACKER_SMTP_HOST", "smtp.internal")
    monkeypatch.setenv("PRICETRACKER_ALERT_TO", "team@example.com")
    settings = Settings(_env_file=None)
    assert settings.email_configured is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PRICETRACKER_DATABASE_URL", "mysql://x"),
        ("PRICETRACKER_WEBHOOK_URL", "ftp://x"),
        ("PRICETRACKER_LOG_LEVEL", "LOUD"),
        ("PRICETRACKER_LOG_FORMAT", "xml"),
    ],
)
def test_invalid_values_rejected(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_negative_cooldown_rejected():
    with pytest.raises(ValidationError):
        make_settings(alert_cooldown_hours=-1)


def test_bare_paths_allowed_for_sqlite():
    assert make_settings(database_url="data/custom.db").database_url == "data/custom.db"
