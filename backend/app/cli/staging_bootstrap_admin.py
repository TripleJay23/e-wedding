"""One-time no-overwrite SUPER_ADMIN provisioning for canonical local staging."""

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cli.staging_safety import (
    StagingRuntimeSafetyError,
    repository_solo_head,
    require_local_staging_database,
    verify_staging_connection,
)
from app.core.configuration_errors import (
    print_configuration_validation_error,
    print_operator_refusal,
)
from app.core.identity import normalize_administrator_username
from app.core.security import PasswordManager
from app.core.settings import Settings
from app.db.guards import DatabaseSafetyError
from app.db.runtime import DatabaseRuntime
from app.models.administrator import Administrator
from app.repositories.administrators import AdministratorRepository
from app.services.admin_provisioning import (
    AdministratorAlreadyProvisionedError,
    AdministratorProvisioningConflictError,
    AdministratorProvisioningService,
)


class StagingAdministratorBootstrapSettings(BaseSettings):
    """Load staging-only credentials from the explicitly selected process environment."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    STAGING_ADMIN_BOOTSTRAP_USERNAME: str = Field(default="admin", min_length=3, max_length=64)
    STAGING_ADMIN_BOOTSTRAP_PASSWORD: SecretStr

    @field_validator("STAGING_ADMIN_BOOTSTRAP_USERNAME")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_administrator_username(value)

    def password_value(self) -> str:
        """Return a validated password or fail without echoing the submitted secret."""
        raw_password = self.STAGING_ADMIN_BOOTSTRAP_PASSWORD.get_secret_value()
        if not 14 <= len(raw_password) <= 1024 or "CHANGE_ME" in raw_password:
            raise StagingRuntimeSafetyError("staging administrator password is invalid")
        return raw_password


def execute(
    settings: Settings,
    credentials: StagingAdministratorBootstrapSettings,
) -> Administrator:
    """Provision the sole staging SUPER_ADMIN only at the repository's current head."""
    _, expected_identity = require_local_staging_database(settings)
    password = credentials.password_value()
    target_revision = repository_solo_head()
    runtime = DatabaseRuntime(settings)
    try:
        print(f"environment: {expected_identity.environment.value}")
        print(f"configured database: {expected_identity.display()}")
        with runtime.engine.begin() as connection:
            server_version = verify_staging_connection(connection, expected_identity)
            try:
                current_revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            except SQLAlchemyError as exc:
                raise StagingRuntimeSafetyError(
                    "staging administrator provisioning requires a verified current schema"
                ) from exc
            print(f"PostgreSQL version: {server_version}")
            print(f"current Alembic revision: {current_revision}")
            print(f"repository sole head: {target_revision}")
            if current_revision != target_revision:
                raise StagingRuntimeSafetyError(
                    "staging administrator provisioning requires the repository sole head"
                )

            with Session(bind=connection, expire_on_commit=False) as session:
                administrator = AdministratorProvisioningService(
                    AdministratorRepository(session),
                    PasswordManager(),
                ).provision(
                    credentials.STAGING_ADMIN_BOOTSTRAP_USERNAME,
                    password,
                )
        print(
            "staging administrator provisioned: "
            f"username={administrator.username}, role={administrator.role.value}, active=true"
        )
        return administrator
    finally:
        runtime.close()


def main() -> int:
    """Load environment-only staging settings and provision exactly once."""
    try:
        execute(Settings(), StagingAdministratorBootstrapSettings())
    except ValidationError as exc:
        print_configuration_validation_error(exc)
        return 2
    except (
        AdministratorAlreadyProvisionedError,
        AdministratorProvisioningConflictError,
        DatabaseSafetyError,
    ) as exc:
        print_operator_refusal(str(exc))
        return 2
    except SQLAlchemyError:
        print_operator_refusal("database operation failed after identity validation")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
