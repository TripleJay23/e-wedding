"""Alembic runtime configured only by a verified guarded connection."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from app.db.base import Base
from app.models import (  # noqa: F401
    Administrator,
    Event,
    EventGuestField,
    GateScanAttempt,
    Guest,
    GuestAttendanceCorrection,
    GuestAttendanceRecord,
    GuestQrCredential,
    ImportBatch,
    ImportBatchRow,
    ImportRowRevision,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Reject offline migrations because connected identity cannot be verified."""
    raise RuntimeError("offline migrations are disabled; use the guarded Alembic command")


def run_migrations_online() -> None:
    """Run migrations using only a connection verified by the guarded CLI."""
    connection = config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("a pre-verified SQLAlchemy connection is required")
    if config.attributes.get("identity_verified") is not True:
        raise RuntimeError("database identity was not verified")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
