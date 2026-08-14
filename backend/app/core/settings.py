"""Fail-closed application settings."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import SecretStr, model_validator
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
    )

    APP_ENV: AppEnvironment
    DATABASE_URL: SecretStr
    APP_NAME: str = "Wedding E-Card API"
    API_V1_PREFIX: str = "/api/v1"
    PRIVATE_STORAGE_ROOT: Path = Path("./private-storage")
    IMPORT_EVIDENCE_STORAGE_ROOT: Path = Path("./private-storage/import-evidence")

    @model_validator(mode="after")
    def validate_environment_paths(self) -> Self:
        """Require explicit absolute private roots outside local-only environments."""
        if self.API_V1_PREFIX != "/api/v1":
            raise ValueError("API_V1_PREFIX must remain /api/v1")

        protected = {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}
        if self.APP_ENV in protected:
            for field_name in ("PRIVATE_STORAGE_ROOT", "IMPORT_EVIDENCE_STORAGE_ROOT"):
                value = getattr(self, field_name)
                if not value.is_absolute():
                    raise ValueError(f"{field_name} must be absolute in {self.APP_ENV.value}")
        return self

    @classmethod
    def from_env_file(cls, env_file: Path) -> Self:
        """Load an explicitly selected environment file."""
        return cls(_env_file=env_file)


@lru_cache
def get_settings() -> Settings:
    """Load process settings exactly once."""
    return Settings()
