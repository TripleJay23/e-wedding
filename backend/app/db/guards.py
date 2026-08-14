"""Environment and database identity safety guards."""

import re
from dataclasses import dataclass

from sqlalchemy import Connection, text
from sqlalchemy.engine import URL, make_url

from app.core.settings import AppEnvironment, Settings

DISPOSABLE_DATABASE_PATTERN = re.compile(r"^wedding_ecard_disposable_[a-z0-9_]+$")
LOCAL_DATABASE_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DatabaseSafetyError(RuntimeError):
    """Raised when a database operation cannot prove it is safe."""


@dataclass(frozen=True)
class DatabaseIdentity:
    """Credential-free configured database identity."""

    environment: AppEnvironment
    host: str
    port: int | None
    database: str
    username: str

    def display(self) -> str:
        """Return a credential-free identity string for operator output."""
        port = f":{self.port}" if self.port is not None else ""
        return f"{self.username}@{self.host}{port}/{self.database}"


def validated_database_url(settings: Settings) -> tuple[URL, DatabaseIdentity]:
    """Validate the explicit PostgreSQL URL against the selected environment."""
    raw_url = settings.DATABASE_URL.get_secret_value()
    try:
        url = make_url(raw_url)
    except Exception as exc:
        raise DatabaseSafetyError("DATABASE_URL is not a valid SQLAlchemy URL") from exc

    if url.drivername != "postgresql+psycopg":
        raise DatabaseSafetyError("DATABASE_URL must use postgresql+psycopg")
    if not all((url.host, url.database, url.username, url.password)):
        raise DatabaseSafetyError(
            "DATABASE_URL must include host, database, username, and password"
        )
    if url.password == "CHANGE_ME":
        raise DatabaseSafetyError("DATABASE_URL still contains the CHANGE_ME placeholder")

    host = url.host.lower()
    database = url.database
    environment = settings.APP_ENV

    if environment is AppEnvironment.DEVELOPMENT:
        if host not in LOCAL_DATABASE_HOSTS or database != "wedding_ecard_dev":
            raise DatabaseSafetyError(
                "development requires a local host and database wedding_ecard_dev"
            )
    elif environment is AppEnvironment.TEST:
        if host not in LOCAL_DATABASE_HOSTS or not DISPOSABLE_DATABASE_PATTERN.fullmatch(database):
            raise DatabaseSafetyError("test requires a local wedding_ecard_disposable_* database")
    elif environment is AppEnvironment.STAGING:
        if database != "wedding_ecard_staging":
            raise DatabaseSafetyError("staging requires database wedding_ecard_staging")
    else:
        raise DatabaseSafetyError("production database access is disabled during Milestone M1")

    identity = DatabaseIdentity(
        environment=environment,
        host=url.host,
        port=url.port,
        database=database,
        username=url.username,
    )
    return url, identity


def require_schema_mutation_allowed(identity: DatabaseIdentity) -> None:
    """Permit schema changes only in development and disposable test databases."""
    if identity.environment not in {AppEnvironment.DEVELOPMENT, AppEnvironment.TEST}:
        raise DatabaseSafetyError(f"schema mutation is disabled for {identity.environment.value}")


def require_disposable_database(identity: DatabaseIdentity) -> None:
    """Require the strict disposable database identity contract."""
    if identity.environment is not AppEnvironment.TEST:
        raise DatabaseSafetyError("disposable commands require APP_ENV=test")
    if identity.host.lower() not in LOCAL_DATABASE_HOSTS:
        raise DatabaseSafetyError("disposable commands require a local database host")
    if not DISPOSABLE_DATABASE_PATTERN.fullmatch(identity.database):
        raise DatabaseSafetyError("disposable database name is not allowed")


def verify_connected_database(connection: Connection, expected_database: str) -> None:
    """Confirm the connected PostgreSQL database matches the configured identity."""
    actual_database = connection.execute(text("SELECT current_database()"), {}).scalar_one()
    if actual_database != expected_database:
        raise DatabaseSafetyError(
            "connected database identity mismatch: "
            f"expected {expected_database}, got {actual_database}"
        )
