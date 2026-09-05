"""Fail-closed local staging operator workflow tests."""

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cli import staging_alembic, staging_bootstrap_admin
from app.cli.staging_alembic import (
    STAGING_MIGRATION_AUTHORIZATION,
    StagingMigrationAuthorizationSettings,
)
from app.cli.staging_bootstrap_admin import StagingAdministratorBootstrapSettings
from app.cli.staging_safety import (
    StagingRuntimeSafetyError,
    repository_solo_head,
    require_local_staging_database,
)
from app.core.settings import AppEnvironment, Settings

SYNTHETIC_AUTH_SETTINGS = {
    "JWT_SECRET": "synthetic-jwt-signing-secret-at-least-32-bytes",
    "JWT_ISSUER": "synthetic-issuer",
    "JWT_AUDIENCE": "synthetic-audience",
}


def runtime_settings(
    tmp_path: Path,
    *,
    environment: AppEnvironment = AppEnvironment.STAGING,
    database_url: str = (
        "postgresql+psycopg://wedding_staging_app:synthetic@127.0.0.1:55432/wedding_ecard_staging"
    ),
) -> Settings:
    return Settings(
        APP_ENV=environment,
        DATABASE_URL=database_url,
        PRIVATE_STORAGE_ROOT=tmp_path / "private",
        IMPORT_EVIDENCE_STORAGE_ROOT=tmp_path / "private" / "import-evidence",
        **SYNTHETIC_AUTH_SETTINGS,
    )


def valid_authorization() -> StagingMigrationAuthorizationSettings:
    return StagingMigrationAuthorizationSettings(
        STAGING_MIGRATION_AUTHORIZATION=STAGING_MIGRATION_AUTHORIZATION
    )


def test_repository_currently_has_one_accepted_head() -> None:
    assert repository_solo_head() == "20260906_0012"


def test_canonical_local_staging_identity_is_explicit(tmp_path: Path) -> None:
    _, identity = require_local_staging_database(runtime_settings(tmp_path))

    assert identity.environment is AppEnvironment.STAGING
    assert identity.host == "127.0.0.1"
    assert identity.port == 55432
    assert identity.database == "wedding_ecard_staging"
    assert identity.username == "wedding_staging_app"


@pytest.mark.parametrize(
    ("environment", "database_url"),
    [
        (
            AppEnvironment.DEVELOPMENT,
            "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev",
        ),
        (
            AppEnvironment.TEST,
            "postgresql+psycopg://wedding_app:synthetic@localhost/"
            "wedding_ecard_disposable_staging_guard",
        ),
        (
            AppEnvironment.PRODUCTION,
            "postgresql+psycopg://wedding_app:synthetic@production-db/wedding_ecard_prod",
        ),
    ],
)
def test_staging_commands_refuse_every_other_environment(
    tmp_path: Path,
    environment: AppEnvironment,
    database_url: str,
) -> None:
    with pytest.raises(StagingRuntimeSafetyError, match="APP_ENV=staging"):
        require_local_staging_database(
            runtime_settings(
                tmp_path,
                environment=environment,
                database_url=database_url,
            )
        )


@pytest.mark.parametrize(
    ("database_url", "message"),
    [
        (
            "postgresql+psycopg://wedding_staging_app:synthetic@localhost/wedding_ecard_dev",
            "wedding_ecard_staging",
        ),
        (
            "postgresql+psycopg://wedding_staging_app:synthetic@localhost/"
            "wedding_ecard_disposable_wrong",
            "wedding_ecard_staging",
        ),
        (
            "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_staging",
            "wedding_staging_app",
        ),
        (
            "postgresql+psycopg://wedding_staging_app:synthetic@remote-db/wedding_ecard_staging",
            "local database host",
        ),
    ],
)
def test_staging_commands_refuse_noncanonical_database_identity(
    tmp_path: Path,
    database_url: str,
    message: str,
) -> None:
    with pytest.raises(StagingRuntimeSafetyError, match=message):
        require_local_staging_database(runtime_settings(tmp_path, database_url=database_url))


def test_missing_application_environment_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
    ):
        monkeypatch.delenv(name, raising=False)

    assert staging_alembic.main(["current"]) == 2
    output = capsys.readouterr().err
    assert "configuration validation failed" in output
    assert "Traceback" not in output


def test_staging_upgrade_requires_explicit_authorization_before_engine_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal engine_called
        engine_called = True

    monkeypatch.setattr(staging_alembic, "create_engine", fail_if_called)

    with pytest.raises(StagingRuntimeSafetyError, match="explicit migration authorization"):
        staging_alembic.execute(
            argparse.Namespace(command="upgrade", revision="head"),
            runtime_settings(tmp_path),
            None,
        )
    assert engine_called is False


def test_invalid_authorization_and_password_values_are_redacted() -> None:
    invalid_authorization = "do-not-print-this-invalid-authorization"
    authorization = StagingMigrationAuthorizationSettings(
        STAGING_MIGRATION_AUTHORIZATION=invalid_authorization
    )
    with pytest.raises(StagingRuntimeSafetyError) as authorization_error:
        authorization.require_valid()
    assert invalid_authorization not in str(authorization_error.value)

    invalid_password = "CHANGE_ME_do-not-print-this-password"
    credentials = StagingAdministratorBootstrapSettings(
        STAGING_ADMIN_BOOTSTRAP_PASSWORD=invalid_password
    )
    with pytest.raises(StagingRuntimeSafetyError) as password_error:
        credentials.password_value()
    assert invalid_password not in str(password_error.value)


@pytest.mark.parametrize(
    "arguments",
    [["upgrade"], ["upgrade", "20260821_0008"], ["current", "head"]],
)
def test_staging_migration_parser_requires_only_explicit_head(
    arguments: list[str],
) -> None:
    parser = staging_alembic.build_parser()
    args = parser.parse_args(arguments)
    with pytest.raises(SystemExit):
        staging_alembic._validate_arguments(args, parser)


def test_authorized_staging_upgrade_reaches_repository_head(
    tmp_path: Path,
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
    revisions = iter([None, "20260821_0008"])
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(staging_alembic, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(
        staging_alembic,
        "verify_staging_connection",
        lambda *args: "17.99",
    )
    monkeypatch.setattr(staging_alembic, "repository_solo_head", lambda: "20260821_0008")
    monkeypatch.setattr(
        staging_alembic.MigrationContext,
        "configure",
        lambda connection: SimpleNamespace(get_current_revision=lambda: next(revisions)),
    )
    monkeypatch.setattr(staging_alembic, "build_alembic_config", lambda connection: object())
    monkeypatch.setattr(
        staging_alembic.command,
        "upgrade",
        lambda config, revision: calls.append(("upgrade", revision)),
    )

    staging_alembic.execute(
        argparse.Namespace(command="upgrade", revision="head"),
        runtime_settings(tmp_path),
        valid_authorization(),
    )

    assert calls == [("upgrade", "head")]
    assert engine.disposed is True


def test_staging_admin_credentials_are_separate_and_masked() -> None:
    credentials = StagingAdministratorBootstrapSettings(
        STAGING_ADMIN_BOOTSTRAP_USERNAME=" ADMIN ",
        STAGING_ADMIN_BOOTSTRAP_PASSWORD="synthetic staging administrator password",
    )

    assert credentials.STAGING_ADMIN_BOOTSTRAP_USERNAME == "admin"
    assert "synthetic staging administrator password" not in repr(credentials)


def test_staging_admin_refuses_wrong_environment_before_runtime_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_called = False

    def fail_runtime(_: Settings) -> None:
        nonlocal runtime_called
        runtime_called = True

    monkeypatch.setattr(staging_bootstrap_admin, "DatabaseRuntime", fail_runtime)
    credentials = StagingAdministratorBootstrapSettings(
        STAGING_ADMIN_BOOTSTRAP_PASSWORD="synthetic staging administrator password"
    )

    with pytest.raises(StagingRuntimeSafetyError, match="APP_ENV=staging"):
        staging_bootstrap_admin.execute(
            runtime_settings(
                tmp_path,
                environment=AppEnvironment.DEVELOPMENT,
                database_url=(
                    "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev"
                ),
            ),
            credentials,
        )
    assert runtime_called is False


def test_staging_admin_requires_database_at_repository_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(
        execute=lambda statement: SimpleNamespace(scalar_one=lambda: "20260821_0007")
    )

    class TransactionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        def __init__(self, settings: Settings) -> None:
            self.engine = SimpleNamespace(begin=lambda: TransactionContext())

        def close(self) -> None:
            return None

    monkeypatch.setattr(staging_bootstrap_admin, "DatabaseRuntime", FakeRuntime)
    monkeypatch.setattr(
        staging_bootstrap_admin,
        "verify_staging_connection",
        lambda *args: "17.99",
    )
    monkeypatch.setattr(
        staging_bootstrap_admin,
        "repository_solo_head",
        lambda: "20260821_0008",
    )
    credentials = StagingAdministratorBootstrapSettings(
        STAGING_ADMIN_BOOTSTRAP_PASSWORD="synthetic staging administrator password"
    )

    with pytest.raises(StagingRuntimeSafetyError, match="repository sole head"):
        staging_bootstrap_admin.execute(runtime_settings(tmp_path), credentials)
