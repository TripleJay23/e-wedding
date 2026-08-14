"""Synthetic test fixtures."""

from pathlib import Path

import pytest

from app.core.settings import AppEnvironment, Settings

SYNTHETIC_JWT_SECRET = "synthetic-jwt-signing-secret-at-least-32-bytes"


@pytest.fixture
def development_settings(tmp_path: Path) -> Settings:
    """Return private, synthetic development settings."""
    return Settings(
        APP_ENV=AppEnvironment.DEVELOPMENT,
        DATABASE_URL="postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev",
        JWT_SECRET=SYNTHETIC_JWT_SECRET,
        JWT_ISSUER="synthetic-issuer",
        JWT_AUDIENCE="synthetic-audience",
        PRIVATE_STORAGE_ROOT=tmp_path / "private",
        IMPORT_EVIDENCE_STORAGE_ROOT=tmp_path / "private" / "import-evidence",
    )
