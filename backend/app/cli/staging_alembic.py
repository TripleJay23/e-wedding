"""Explicitly authorized Alembic workflow for the canonical local staging database."""

import argparse
import secrets
from collections.abc import Sequence

from alembic import command
from alembic.runtime.migration import MigrationContext
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app.cli.staging_safety import (
    StagingRuntimeSafetyError,
    build_alembic_config,
    repository_solo_head,
    require_local_staging_database,
    verify_staging_connection,
)
from app.core.configuration_errors import (
    print_configuration_validation_error,
    print_operator_refusal,
)
from app.core.settings import Settings
from app.db.guards import DatabaseSafetyError

STAGING_MIGRATION_AUTHORIZATION = "APPLY_ACCEPTED_SCHEMA_TO_CANONICAL_STAGING"


class StagingMigrationAuthorizationSettings(BaseSettings):
    """Load the explicit operator acknowledgement without exposing it in output."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    STAGING_MIGRATION_AUTHORIZATION: SecretStr

    def require_valid(self) -> None:
        """Reject an incorrect acknowledgement without echoing its submitted value."""
        if not secrets.compare_digest(
            self.STAGING_MIGRATION_AUTHORIZATION.get_secret_value(),
            STAGING_MIGRATION_AUTHORIZATION,
        ):
            raise StagingRuntimeSafetyError("explicit staging migration authorization is invalid")


def build_parser() -> argparse.ArgumentParser:
    """Build the narrow staging migration parser."""
    parser = argparse.ArgumentParser(description="Operate on canonical local staging schema")
    parser.add_argument("command", choices=("upgrade", "current", "check"))
    parser.add_argument("revision", nargs="?")
    return parser


def _validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.command == "upgrade" and args.revision != "head":
        parser.error("staging upgrade requires the explicit target head")
    if args.command != "upgrade" and args.revision is not None:
        parser.error(f"{args.command} does not accept a revision")


def execute(
    args: argparse.Namespace,
    settings: Settings,
    authorization: StagingMigrationAuthorizationSettings | None,
) -> None:
    """Run one staging schema operation after proving every safety precondition."""
    url, identity = require_local_staging_database(settings)
    if args.command == "upgrade" and authorization is None:
        raise StagingRuntimeSafetyError("staging upgrade requires explicit migration authorization")
    if authorization is not None:
        authorization.require_valid()

    target_revision = repository_solo_head()
    print(f"environment: {identity.environment.value}")
    print(f"configured database: {identity.display()}")
    print(f"operation: {args.command}")
    print(f"repository sole head: {target_revision}")
    if args.command == "upgrade":
        print(f"intended target revision: {target_revision}")

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            server_version = verify_staging_connection(connection, identity)
            context = MigrationContext.configure(connection)
            current_revision = context.get_current_revision()
            print(f"PostgreSQL version: {server_version}")
            print(f"current Alembic revision: {current_revision or '<none>'}")
            connection.commit()

            config = build_alembic_config(connection)
            if args.command == "upgrade":
                command.upgrade(config, "head")
                final_revision = MigrationContext.configure(connection).get_current_revision()
                print(f"final Alembic revision: {final_revision or '<none>'}")
                if final_revision != target_revision:
                    raise StagingRuntimeSafetyError(
                        "staging upgrade did not reach the repository sole head"
                    )
            elif args.command == "current":
                command.current(config, verbose=True)
            else:
                command.check(config)
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Load explicit process settings and run one staging schema operation."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_arguments(args, parser)
    try:
        authorization = (
            StagingMigrationAuthorizationSettings() if args.command == "upgrade" else None
        )
        execute(args, Settings(), authorization)
    except ValidationError as exc:
        print_configuration_validation_error(exc)
        return 2
    except DatabaseSafetyError as exc:
        print_operator_refusal(str(exc))
        return 2
    except SQLAlchemyError:
        print_operator_refusal("database operation failed after identity validation")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
