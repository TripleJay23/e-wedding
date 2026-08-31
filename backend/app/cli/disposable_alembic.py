"""Strict wrapper for disposable PostgreSQL migration verification."""

from collections.abc import Sequence

from pydantic import ValidationError

from app.cli.safe_alembic import build_parser, execute
from app.core.configuration_errors import (
    print_configuration_validation_error,
    print_operator_refusal,
)
from app.core.settings import Settings
from app.db.guards import (
    DatabaseSafetyError,
    require_disposable_database,
    validated_database_url,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run Alembic only against a verified local disposable test database."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"upgrade", "downgrade"} and not args.revision:
        parser.error(f"{args.command} requires an explicit revision")
    if args.command not in {"upgrade", "downgrade"} and args.revision:
        parser.error(f"{args.command} does not accept a revision")

    try:
        settings = Settings()
        _, identity = validated_database_url(settings)
        require_disposable_database(identity)
        execute(args, settings)
    except ValidationError as exc:
        print_configuration_validation_error(exc)
        return 2
    except DatabaseSafetyError as exc:
        print_operator_refusal(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
