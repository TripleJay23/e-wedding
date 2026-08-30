"""Shared fail-closed safety primitives for the local staging operator commands."""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.engine import URL

from app.core.settings import AppEnvironment, Settings
from app.db.guards import (
    LOCAL_DATABASE_HOSTS,
    DatabaseIdentity,
    DatabaseSafetyError,
    validated_database_url,
    verify_connected_database,
)

CANONICAL_STAGING_DATABASE = "wedding_ecard_staging"
CANONICAL_STAGING_USERNAME = "wedding_staging_app"
POSTGRESQL_17_PATTERN = re.compile(r"^17(?:\.|$)")


class StagingRuntimeSafetyError(DatabaseSafetyError):
    """Raised when the canonical local staging identity cannot be proven."""


def require_local_staging_database(settings: Settings) -> tuple[URL, DatabaseIdentity]:
    """Resolve only the explicit canonical local staging database identity."""
    if settings.APP_ENV is not AppEnvironment.STAGING:
        raise StagingRuntimeSafetyError("local staging operation requires APP_ENV=staging")

    try:
        url, identity = validated_database_url(settings)
    except DatabaseSafetyError as exc:
        raise StagingRuntimeSafetyError(str(exc)) from exc
    if identity.host.lower() not in LOCAL_DATABASE_HOSTS:
        raise StagingRuntimeSafetyError("local staging operation requires a local database host")
    if identity.database != CANONICAL_STAGING_DATABASE:
        raise StagingRuntimeSafetyError(
            f"local staging operation requires database {CANONICAL_STAGING_DATABASE}"
        )
    if identity.username != CANONICAL_STAGING_USERNAME:
        raise StagingRuntimeSafetyError(
            f"local staging operation requires database user {CANONICAL_STAGING_USERNAME}"
        )
    return url, identity


def build_alembic_config(connection: Connection | None = None) -> Config:
    """Build repository Alembic configuration, optionally bound to a verified connection."""
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(backend_root / "alembic.ini")
    if connection is not None:
        config.attributes["connection"] = connection
        config.attributes["identity_verified"] = True
    return config


def repository_solo_head() -> str:
    """Return the repository's sole accepted Alembic head or fail closed."""
    heads = ScriptDirectory.from_config(build_alembic_config()).get_heads()
    if len(heads) != 1:
        raise StagingRuntimeSafetyError("staging operations require exactly one Alembic head")
    return heads[0]


def verify_staging_connection(
    connection: Connection,
    identity: DatabaseIdentity,
) -> str:
    """Verify connected database identity and the required PostgreSQL 17 server."""
    verify_connected_database(connection, identity.database)
    server_version = str(connection.execute(text("SHOW server_version")).scalar_one())
    if POSTGRESQL_17_PATTERN.match(server_version) is None:
        raise StagingRuntimeSafetyError("local staging requires PostgreSQL 17")
    return server_version
