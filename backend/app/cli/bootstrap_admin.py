"""Development-only one-time administrator provisioning command."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.identity import normalize_administrator_username
from app.core.security import PasswordManager
from app.core.settings import AppEnvironment, Settings
from app.db.guards import verify_connected_database
from app.db.runtime import DatabaseRuntime
from app.repositories.administrators import AdministratorRepository
from app.services.admin_provisioning import AdministratorProvisioningService

AUTHENTICATION_SCHEMA_REVISIONS = frozenset({"20260814_0002", "20260814_0003"})


class BootstrapSafetyError(RuntimeError):
    """Raised when safe local administrator provisioning cannot be proven."""


class AdministratorBootstrapSettings(BaseSettings):
    """Credentials read only from the explicitly loaded process environment."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=True,
        extra="forbid",
    )

    ADMIN_BOOTSTRAP_USERNAME: str = Field(default="admin", min_length=3, max_length=64)
    ADMIN_BOOTSTRAP_PASSWORD: SecretStr = Field(min_length=14, max_length=1024)

    @field_validator("ADMIN_BOOTSTRAP_USERNAME")
    @classmethod
    def validate_bootstrap_username(cls, value: str) -> str:
        """Canonicalize the indexed login identifier before database access."""
        return normalize_administrator_username(value)

    @field_validator("ADMIN_BOOTSTRAP_PASSWORD")
    @classmethod
    def validate_bootstrap_password(cls, value: SecretStr) -> SecretStr:
        """Reject example and placeholder bootstrap passwords."""
        raw_password = value.get_secret_value()
        if "CHANGE_ME" in raw_password:
            raise ValueError("ADMIN_BOOTSTRAP_PASSWORD must not be a placeholder")
        return value


def execute(settings: Settings, credentials: AdministratorBootstrapSettings) -> None:
    """Provision through one connected, identity-verified development session."""
    if settings.APP_ENV is not AppEnvironment.DEVELOPMENT:
        raise BootstrapSafetyError("administrator bootstrap requires APP_ENV=development")

    runtime = DatabaseRuntime(settings)
    try:
        print(f"environment: {runtime.identity.environment.value}")
        print(f"configured database: {runtime.identity.display()}")
        with runtime.engine.begin() as connection:
            verify_connected_database(connection, runtime.identity.database)
            current_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if current_revision not in AUTHENTICATION_SCHEMA_REVISIONS:
                raise BootstrapSafetyError(
                    "administrator bootstrap requires a compatible authentication schema revision"
                )
            print(f"current Alembic revision: {current_revision}")

            with Session(bind=connection, expire_on_commit=False) as session:
                service = AdministratorProvisioningService(
                    AdministratorRepository(session),
                    PasswordManager(),
                )
                administrator = service.provision(
                    credentials.ADMIN_BOOTSTRAP_USERNAME,
                    credentials.ADMIN_BOOTSTRAP_PASSWORD.get_secret_value(),
                )
        print(
            "administrator provisioned: "
            f"username={administrator.username}, role={administrator.role.value}"
        )
    finally:
        runtime.close()


def main() -> int:
    """Load environment-only settings and provision the local administrator."""
    execute(Settings(), AdministratorBootstrapSettings())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
