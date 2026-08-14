"""Database safety guard tests."""

from unittest.mock import Mock

import pytest
from sqlalchemy.engine import Connection

from app.core.settings import AppEnvironment, Settings
from app.db.guards import (
    DatabaseIdentity,
    DatabaseSafetyError,
    require_disposable_database,
    require_schema_mutation_allowed,
    validated_database_url,
    verify_connected_database,
)


def settings_for(environment: AppEnvironment, database_url: str) -> Settings:
    values: dict[str, object] = {"APP_ENV": environment, "DATABASE_URL": database_url}
    if environment in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}:
        values.update(
            PRIVATE_STORAGE_ROOT="C:/private",
            IMPORT_EVIDENCE_STORAGE_ROOT="C:/private/import-evidence",
        )
    return Settings(**values)


def test_development_accepts_only_exact_local_database() -> None:
    settings = settings_for(
        AppEnvironment.DEVELOPMENT,
        "postgresql+psycopg://wedding_app:synthetic@127.0.0.1/wedding_ecard_dev",
    )

    _, identity = validated_database_url(settings)

    assert identity.database == "wedding_ecard_dev"
    assert "synthetic" not in identity.display()


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///local.db",
        "postgresql+psycopg://wedding_app:synthetic@db/wedding_ecard_dev",
        "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_staging",
        "postgresql+psycopg://wedding_app:CHANGE_ME@localhost/wedding_ecard_dev",
    ],
)
def test_development_rejects_unsafe_database_targets(database_url: str) -> None:
    with pytest.raises(DatabaseSafetyError):
        validated_database_url(settings_for(AppEnvironment.DEVELOPMENT, database_url))


def test_disposable_guard_requires_test_name_and_environment() -> None:
    _, identity = validated_database_url(
        settings_for(
            AppEnvironment.TEST,
            "postgresql+psycopg://wedding_app:synthetic@localhost/"
            "wedding_ecard_disposable_foundation",
        )
    )

    require_disposable_database(identity)

    _, development_identity = validated_database_url(
        settings_for(
            AppEnvironment.DEVELOPMENT,
            "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev",
        )
    )
    with pytest.raises(DatabaseSafetyError, match="APP_ENV=test"):
        require_disposable_database(development_identity)


def test_schema_mutation_is_disabled_for_staging() -> None:
    _, identity = validated_database_url(
        settings_for(
            AppEnvironment.STAGING,
            "postgresql+psycopg://wedding_app:synthetic@staging-db/wedding_ecard_staging",
        )
    )

    with pytest.raises(DatabaseSafetyError, match="disabled for staging"):
        require_schema_mutation_allowed(identity)


def test_production_database_access_is_disabled() -> None:
    with pytest.raises(DatabaseSafetyError, match="disabled during Milestone M1"):
        validated_database_url(
            settings_for(
                AppEnvironment.PRODUCTION,
                "postgresql+psycopg://wedding_app:synthetic@production-db/wedding_ecard_prod",
            )
        )


def test_connected_database_must_match_configured_identity() -> None:
    connection = Mock(spec=Connection)
    connection.execute.return_value.scalar_one.return_value = "wrong_database"

    with pytest.raises(DatabaseSafetyError, match="identity mismatch"):
        verify_connected_database(connection, "wedding_ecard_dev")


def test_connected_database_accepts_expected_identity() -> None:
    connection = Mock(spec=Connection)
    connection.execute.return_value.scalar_one.return_value = "wedding_ecard_dev"

    verify_connected_database(connection, "wedding_ecard_dev")


@pytest.mark.parametrize(
    "database_url",
    [
        "not a URL",
        "postgresql+psycopg://wedding_app@localhost/wedding_ecard_dev",
    ],
)
def test_invalid_or_incomplete_url_is_rejected(database_url: str) -> None:
    with pytest.raises(DatabaseSafetyError):
        validated_database_url(settings_for(AppEnvironment.DEVELOPMENT, database_url))


def test_staging_requires_exact_database_name() -> None:
    with pytest.raises(DatabaseSafetyError, match="wedding_ecard_staging"):
        validated_database_url(
            settings_for(
                AppEnvironment.STAGING,
                "postgresql+psycopg://wedding_app:synthetic@db/wedding_ecard_stage_typo",
            )
        )


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (
            DatabaseIdentity(
                environment=AppEnvironment.TEST,
                host="remote-db",
                port=5432,
                database="wedding_ecard_disposable_guard",
                username="wedding_app",
            ),
            "local database host",
        ),
        (
            DatabaseIdentity(
                environment=AppEnvironment.TEST,
                host="localhost",
                port=5432,
                database="wedding_ecard_test",
                username="wedding_app",
            ),
            "name is not allowed",
        ),
    ],
)
def test_disposable_guard_rechecks_identity(
    identity: DatabaseIdentity,
    message: str,
) -> None:
    with pytest.raises(DatabaseSafetyError, match=message):
        require_disposable_database(identity)
