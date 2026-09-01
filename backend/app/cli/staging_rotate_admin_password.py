"""Staging-only password rotation for the existing singleton SUPER_ADMIN."""

from uuid import UUID

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
from app.core.security import PasswordManager
from app.core.settings import Settings
from app.db.guards import DatabaseSafetyError
from app.db.runtime import DatabaseRuntime
from app.models.administrator import Administrator
from app.repositories.administrators import AdministratorRepository
from app.services.admin_password_rotation import (
    AdministratorPasswordRotationError,
    AdministratorPasswordRotationService,
)


class StagingAdministratorPasswordRotationSettings(BaseSettings):
    """Load the expected identity and replacement secret from process environment only."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    STAGING_ADMIN_ROTATION_EXPECTED_UUID: UUID
    STAGING_ADMIN_ROTATION_PASSWORD: SecretStr = Field(min_length=14, max_length=1024)

    @field_validator("STAGING_ADMIN_ROTATION_PASSWORD")
    @classmethod
    def validate_replacement_password(cls, value: SecretStr) -> SecretStr:
        """Reject placeholders without exposing the submitted replacement."""
        if "CHANGE_ME" in value.get_secret_value():
            raise ValueError("staging administrator replacement password is invalid")
        return value

    def password_value(self) -> str:
        """Return the already validated replacement at the mutation boundary."""
        return self.STAGING_ADMIN_ROTATION_PASSWORD.get_secret_value()


def execute(
    settings: Settings,
    rotation: StagingAdministratorPasswordRotationSettings,
) -> Administrator:
    """Rotate one expected staging administrator after every runtime guard passes."""
    _, expected_identity = require_local_staging_database(settings)
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
                    "staging administrator rotation requires a verified current schema"
                ) from exc
            print(f"PostgreSQL version: {server_version}")
            print(f"current Alembic revision: {current_revision}")
            print(f"repository sole head: {target_revision}")
            if current_revision != target_revision:
                raise StagingRuntimeSafetyError(
                    "staging administrator rotation requires the repository sole head"
                )

            with Session(bind=connection, expire_on_commit=False, autoflush=False) as session:
                administrator = AdministratorPasswordRotationService(
                    AdministratorRepository(session),
                    PasswordManager(),
                ).rotate(
                    rotation.STAGING_ADMIN_ROTATION_EXPECTED_UUID,
                    rotation.password_value(),
                )

        print(
            "staging administrator password rotation: SUCCESS; "
            f"id={administrator.id}, username={administrator.username}, "
            f"role={administrator.role.value}, active={str(administrator.is_active).lower()}"
        )
        return administrator
    finally:
        runtime.close()


def main() -> int:
    """Load environment-only settings and rotate the expected staging administrator."""
    try:
        execute(Settings(), StagingAdministratorPasswordRotationSettings())
    except ValidationError as exc:
        print_configuration_validation_error(exc)
        return 2
    except (AdministratorPasswordRotationError, DatabaseSafetyError) as exc:
        print_operator_refusal(str(exc))
        return 2
    except SQLAlchemyError:
        print_operator_refusal("database operation failed after identity validation")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
