"""Guarded Alembic command wrapper."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import Connection, create_engine

from app.core.configuration_errors import (
    print_configuration_validation_error,
    print_operator_refusal,
)
from app.core.settings import Settings
from app.db.guards import (
    DatabaseSafetyError,
    require_schema_mutation_allowed,
    validated_database_url,
    verify_connected_database,
)

MUTATING_COMMANDS = {"upgrade", "downgrade"}


def build_parser() -> argparse.ArgumentParser:
    """Build the deliberately narrow migration command parser."""
    parser = argparse.ArgumentParser(description="Run Alembic with database safety guards")
    parser.add_argument("command", choices=("upgrade", "downgrade", "current", "check"))
    parser.add_argument("revision", nargs="?")
    return parser


def _validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.command in MUTATING_COMMANDS and not args.revision:
        parser.error(f"{args.command} requires an explicit revision")
    if args.command not in MUTATING_COMMANDS and args.revision:
        parser.error(f"{args.command} does not accept a revision")


def _alembic_config(connection: Connection) -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(backend_root / "alembic.ini")
    config.attributes["connection"] = connection
    config.attributes["identity_verified"] = True
    return config


def execute(args: argparse.Namespace, settings: Settings) -> None:
    """Execute one guarded Alembic operation against a verified connection."""
    url, identity = validated_database_url(settings)
    if args.command in MUTATING_COMMANDS:
        require_schema_mutation_allowed(identity)

    print(f"environment: {identity.environment.value}")
    print(f"configured database: {identity.display()}")
    print(f"operation: {args.command}")
    if args.revision:
        print(f"intended target revision: {args.revision}")

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            verify_connected_database(connection, identity.database)
            context = MigrationContext.configure(connection)
            current_revision = context.get_current_revision()
            print(f"current Alembic revision: {current_revision or '<none>'}")
            connection.commit()

            config = _alembic_config(connection)
            if args.command == "upgrade":
                command.upgrade(config, args.revision)
            elif args.command == "downgrade":
                command.downgrade(config, args.revision)
            elif args.command == "current":
                command.current(config, verbose=True)
            else:
                command.check(config)
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse and run a guarded Alembic command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_arguments(args, parser)
    try:
        execute(args, Settings())
    except ValidationError as exc:
        print_configuration_validation_error(exc)
        return 2
    except DatabaseSafetyError as exc:
        print_operator_refusal(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
