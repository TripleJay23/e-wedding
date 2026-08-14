"""Database runtime construction tests."""

from unittest.mock import Mock

import pytest
from sqlalchemy import Engine

from app.core.settings import Settings
from app.db import runtime


def test_runtime_builds_guarded_engine_and_disposes_it(
    development_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = Mock(spec=Engine)
    monkeypatch.setattr(runtime, "create_engine", lambda *args, **kwargs: engine)

    database = runtime.DatabaseRuntime(development_settings)
    database.close()

    assert database.identity.database == "wedding_ecard_dev"
    assert database.session_factory.kw["autoflush"] is False
    engine.dispose.assert_called_once_with()
