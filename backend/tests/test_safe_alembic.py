"""Guarded Alembic CLI tests."""

import argparse
from types import SimpleNamespace

import pytest

from app.cli import disposable_alembic, safe_alembic
from app.core.settings import AppEnvironment, Settings
from app.db.guards import DatabaseSafetyError


def test_mutating_command_requires_explicit_revision() -> None:
    parser = safe_alembic.build_parser()
    args = parser.parse_args(["upgrade"])

    with pytest.raises(SystemExit):
        safe_alembic._validate_arguments(args, parser)


def test_read_only_command_rejects_revision() -> None:
    parser = safe_alembic.build_parser()
    args = parser.parse_args(["current", "head"])

    with pytest.raises(SystemExit):
        safe_alembic._validate_arguments(args, parser)


def test_disposable_wrapper_rejects_development_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        APP_ENV=AppEnvironment.DEVELOPMENT,
        DATABASE_URL="postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev",
    )
    monkeypatch.setattr(disposable_alembic, "Settings", lambda: settings)

    with pytest.raises(DatabaseSafetyError, match="APP_ENV=test"):
        disposable_alembic.main(["current"])


def test_execute_refuses_staging_mutation_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        APP_ENV=AppEnvironment.STAGING,
        DATABASE_URL="postgresql+psycopg://wedding_app:synthetic@db/wedding_ecard_staging",
        PRIVATE_STORAGE_ROOT="C:/private",
        IMPORT_EVIDENCE_STORAGE_ROOT="C:/private/evidence",
    )
    args = argparse.Namespace(command="upgrade", revision="head")
    engine_called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal engine_called
        engine_called = True

    monkeypatch.setattr(safe_alembic, "create_engine", fail_if_called)

    with pytest.raises(DatabaseSafetyError, match="disabled for staging"):
        safe_alembic.execute(args, settings)
    assert engine_called is False


@pytest.mark.parametrize("command", ["upgrade", "downgrade", "current", "check"])
def test_execute_dispatches_with_verified_connection(
    command: str,
    development_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(commit=lambda: None)

    class ConnectionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *args: object) -> None:
            return None

    class FakeEngine:
        disposed = False

        def connect(self) -> ConnectionContext:
            return ConnectionContext()

        def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(safe_alembic, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(safe_alembic, "verify_connected_database", lambda *args: None)
    monkeypatch.setattr(
        safe_alembic.MigrationContext,
        "configure",
        lambda connection: SimpleNamespace(get_current_revision=lambda: None),
    )
    monkeypatch.setattr(safe_alembic, "_alembic_config", lambda connection: object())
    monkeypatch.setattr(
        safe_alembic.command,
        "upgrade",
        lambda config, revision: calls.append(("upgrade", revision)),
    )
    monkeypatch.setattr(
        safe_alembic.command,
        "downgrade",
        lambda config, revision: calls.append(("downgrade", revision)),
    )
    monkeypatch.setattr(
        safe_alembic.command,
        "current",
        lambda config, verbose: calls.append(("current", str(verbose))),
    )
    monkeypatch.setattr(
        safe_alembic.command,
        "check",
        lambda config: calls.append(("check", None)),
    )
    revision = "head" if command in {"upgrade", "downgrade"} else None

    safe_alembic.execute(
        argparse.Namespace(command=command, revision=revision), development_settings
    )

    assert calls[0][0] == command
    assert engine.disposed is True


def test_safe_main_parses_and_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(safe_alembic, "Settings", lambda: object())
    monkeypatch.setattr(
        safe_alembic,
        "execute",
        lambda args, settings: calls.append(args.command),
    )

    assert safe_alembic.main(["current"]) == 0
    assert calls == ["current"]


def test_disposable_main_executes_for_valid_test_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        APP_ENV=AppEnvironment.TEST,
        DATABASE_URL="postgresql+psycopg://wedding_app:synthetic@localhost/"
        "wedding_ecard_disposable_cli",
    )
    calls: list[str] = []
    monkeypatch.setattr(disposable_alembic, "Settings", lambda: settings)
    monkeypatch.setattr(
        disposable_alembic,
        "execute",
        lambda args, resolved_settings: calls.append(args.command),
    )

    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert calls == ["upgrade"]


@pytest.mark.parametrize("arguments", [["upgrade"], ["current", "head"]])
def test_disposable_main_rejects_invalid_revision_shape(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        disposable_alembic.main(arguments)
