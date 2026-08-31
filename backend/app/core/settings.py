"""Fail-closed application settings."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings with no database or secret fallbacks."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    APP_ENV: AppEnvironment
    DATABASE_URL: SecretStr
    APP_NAME: str = "Wedding E-Card API"
    API_V1_PREFIX: str = "/api/v1"
    JWT_SECRET: SecretStr
    JWT_ISSUER: str = Field(min_length=1, max_length=128)
    JWT_AUDIENCE: str = Field(min_length=1, max_length=128)
    ACCESS_TOKEN_TTL_MINUTES: int = Field(default=30, ge=1, le=60)
    PRIVATE_STORAGE_ROOT: Path = Path("./private-storage")
    IMPORT_EVIDENCE_STORAGE_ROOT: Path = Path("./private-storage/import-evidence")
    QR_IMAGE_STORAGE_ROOT: Path | None = None
    TEMPLATE_STORAGE_ROOT: Path | None = None
    EVENT_PHOTO_STORAGE_ROOT: Path | None = None
    GENERATED_CARD_STORAGE_ROOT: Path | None = None
    MAX_IMPORT_WORKBOOK_BYTES: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    MAX_IMPORT_CANDIDATE_ROWS: int = Field(default=5_000, ge=1, le=25_000)
    MAX_REPORT_EXPORT_ROWS: int = Field(default=5_000, ge=1, le=25_000)
    MAX_EVENT_PHOTO_UPLOAD_BYTES: int = Field(
        default=15 * 1024 * 1024,
        ge=1024,
        le=50 * 1024 * 1024,
    )

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        """Require an explicit high-entropy-sized signing secret."""
        raw_secret = value.get_secret_value()
        if "CHANGE_ME" in raw_secret or len(raw_secret.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET must be at least 32 bytes and not a placeholder")
        return value

    @field_validator("JWT_ISSUER", "JWT_AUDIENCE")
    @classmethod
    def validate_jwt_identity(cls, value: str) -> str:
        """Keep token trust identities explicit and unambiguous."""
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("JWT issuer and audience must not contain whitespace")
        return value

    @model_validator(mode="after")
    def validate_environment_paths(self) -> Self:
        """Require explicit absolute private roots outside local-only environments."""
        if self.API_V1_PREFIX != "/api/v1":
            raise ValueError("API_V1_PREFIX must remain /api/v1")

        if self.QR_IMAGE_STORAGE_ROOT is None:
            self.QR_IMAGE_STORAGE_ROOT = self.PRIVATE_STORAGE_ROOT / "qr-codes"
        if self.TEMPLATE_STORAGE_ROOT is None:
            self.TEMPLATE_STORAGE_ROOT = self.PRIVATE_STORAGE_ROOT / "templates"
        if self.EVENT_PHOTO_STORAGE_ROOT is None:
            self.EVENT_PHOTO_STORAGE_ROOT = self.PRIVATE_STORAGE_ROOT / "event-photos"
        if self.GENERATED_CARD_STORAGE_ROOT is None:
            self.GENERATED_CARD_STORAGE_ROOT = self.PRIVATE_STORAGE_ROOT / "generated-cards"

        protected = {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}
        if self.APP_ENV in protected:
            for field_name in (
                "PRIVATE_STORAGE_ROOT",
                "IMPORT_EVIDENCE_STORAGE_ROOT",
                "QR_IMAGE_STORAGE_ROOT",
                "TEMPLATE_STORAGE_ROOT",
                "EVENT_PHOTO_STORAGE_ROOT",
                "GENERATED_CARD_STORAGE_ROOT",
            ):
                value = getattr(self, field_name)
                assert isinstance(value, Path)
                if not value.is_absolute():
                    raise ValueError(f"{field_name} must be absolute in {self.APP_ENV.value}")

        private_root = self.PRIVATE_STORAGE_ROOT.resolve()
        for field_name in (
            "IMPORT_EVIDENCE_STORAGE_ROOT",
            "QR_IMAGE_STORAGE_ROOT",
            "TEMPLATE_STORAGE_ROOT",
            "EVENT_PHOTO_STORAGE_ROOT",
            "GENERATED_CARD_STORAGE_ROOT",
        ):
            value = getattr(self, field_name)
            assert isinstance(value, Path)
            resolved = value.resolve()
            if resolved != private_root and private_root not in resolved.parents:
                raise ValueError(f"{field_name} must be within PRIVATE_STORAGE_ROOT")
        return self

    @classmethod
    def from_env_file(cls, env_file: Path) -> Self:
        """Load an explicitly selected environment file."""
        return cls(_env_file=env_file)


@lru_cache
def get_settings() -> Settings:
    """Load process settings exactly once."""
    return Settings()
