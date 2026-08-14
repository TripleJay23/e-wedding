"""Opt-in disposable PostgreSQL migration lifecycle verification."""

import os

import pytest

from app.cli import disposable_alembic

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
]


def test_clean_baseline_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DISPOSABLE_URL is not None
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)

    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["current"]) == 0
    assert disposable_alembic.main(["check"]) == 0
    assert disposable_alembic.main(["downgrade", "base"]) == 0
    assert disposable_alembic.main(["upgrade", "head"]) == 0
