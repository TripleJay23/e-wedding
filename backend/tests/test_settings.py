"""Application settings safety tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import settings as settings_module
from app.core.settings import AppEnvironment, Settings

SYNTHETIC_AUTH_SETTINGS = {
    "JWT_SECRET": "synthetic-jwt-signing-secret-at-least-32-bytes",
    "JWT_ISSUER": "synthetic-issuer",
    "JWT_AUDIENCE": "synthetic-audience",
}


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    for key, value in SYNTHETIC_AUTH_SETTINGS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_protected_environment_requires_absolute_storage_paths(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="PRIVATE_STORAGE_ROOT must be absolute"):
        Settings(
            APP_ENV=AppEnvironment.STAGING,
            DATABASE_URL="postgresql+psycopg://wedding_app:synthetic@db/wedding_ecard_staging",
            PRIVATE_STORAGE_ROOT=Path("relative/private"),
            IMPORT_EVIDENCE_STORAGE_ROOT=tmp_path / "evidence",
            **SYNTHETIC_AUTH_SETTINGS,
        )


def test_api_prefix_cannot_drift(development_settings: Settings) -> None:
    values = development_settings.model_dump()
    values["API_V1_PREFIX"] = "/v2"

    with pytest.raises(ValidationError, match="API_V1_PREFIX must remain /api/v1"):
        Settings(**values)


def test_secret_is_masked_in_settings_representation(development_settings: Settings) -> None:
    representation = repr(development_settings)

    assert development_settings.DATABASE_URL.get_secret_value() not in representation
    assert development_settings.JWT_SECRET.get_secret_value() not in representation
    assert "**********" in representation


def test_settings_can_load_only_an_explicit_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(
        "APP_ENV=development\n"
        "DATABASE_URL=postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev\n"
        "JWT_SECRET=synthetic-jwt-signing-secret-at-least-32-bytes\n"
        "JWT_ISSUER=synthetic-issuer\n"
        "JWT_AUDIENCE=synthetic-audience\n",
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.APP_ENV is AppEnvironment.DEVELOPMENT


def test_cached_settings_load_from_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev",
    )
    for key, value in SYNTHETIC_AUTH_SETTINGS.items():
        monkeypatch.setenv(key, value)

    first = settings_module.get_settings()
    second = settings_module.get_settings()

    assert first is second
    settings_module.get_settings.cache_clear()
