"""Alembic foundation tests."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.base import Base

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_clean_baseline_is_the_only_alembic_head() -> None:
    config = Config(BACKEND_ROOT / "alembic.ini")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260814_0001"]
    assert script.get_base() == "20260814_0001"


def test_alembic_configuration_has_no_database_url() -> None:
    content = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert "sqlalchemy.url" not in content


def test_alembic_environment_requires_verified_connection() -> None:
    content = (BACKEND_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")

    assert 'config.attributes.get("connection")' in content
    assert 'config.attributes.get("identity_verified") is not True' in content
    assert "offline migrations are disabled" in content


def test_foundation_declarative_metadata_is_empty() -> None:
    assert Base.metadata.tables == {}
