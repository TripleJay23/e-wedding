"""Administrator provisioning and bootstrap safety tests."""

from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.cli.bootstrap_admin import (
    AUTHENTICATION_SCHEMA_REVISIONS,
    AdministratorBootstrapSettings,
    BootstrapSafetyError,
    execute,
)
from app.core.security import PasswordManager
from app.core.settings import AppEnvironment, Settings
from app.models.administrator import Administrator, AdministratorRole
from app.services.admin_provisioning import (
    AdministratorAlreadyProvisionedError,
    AdministratorProvisioningConflictError,
    AdministratorProvisioningService,
)

SYNTHETIC_AUTH_SETTINGS = {
    "JWT_SECRET": "synthetic-jwt-signing-secret-at-least-32-bytes",
    "JWT_ISSUER": "synthetic-issuer",
    "JWT_AUDIENCE": "synthetic-audience",
}


class FakeProvisioningRepository:
    """In-memory provisioning store with transaction counters."""

    def __init__(self, administrator: Administrator | None = None) -> None:
        self.administrator = administrator
        self.commits = 0
        self.rollbacks = 0
        self.commit_error: IntegrityError | None = None

    def get_singleton(self) -> Administrator | None:
        return self.administrator

    def add(self, administrator: Administrator) -> None:
        self.administrator = administrator

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(scope="module")
def password_manager() -> PasswordManager:
    return PasswordManager()


def test_provisioning_creates_one_active_super_admin_with_argon2id(
    password_manager: PasswordManager,
) -> None:
    repository = FakeProvisioningRepository()
    service = AdministratorProvisioningService(repository, password_manager)

    administrator = service.provision(" ADMIN ", "synthetic administrator password")

    assert administrator.username == "admin"
    assert administrator.role is AdministratorRole.SUPER_ADMIN
    assert administrator.is_active is True
    assert administrator.password_hash.startswith("$argon2id$")
    assert repository.commits == 1
    assert repository.rollbacks == 0


def test_provisioning_never_overwrites_existing_administrator(
    password_manager: PasswordManager,
) -> None:
    existing = Administrator(
        username="admin",
        password_hash=password_manager.hash_password("existing synthetic password"),
        role=AdministratorRole.SUPER_ADMIN,
        is_active=True,
    )
    repository = FakeProvisioningRepository(existing)
    service = AdministratorProvisioningService(repository, password_manager)

    with pytest.raises(AdministratorAlreadyProvisionedError):
        service.provision("admin", "replacement synthetic password")

    assert repository.administrator is existing
    assert repository.commits == 0


def test_provisioning_rolls_back_singleton_race(password_manager: PasswordManager) -> None:
    repository = FakeProvisioningRepository()
    repository.commit_error = IntegrityError("INSERT", {}, Exception("synthetic conflict"))
    service = AdministratorProvisioningService(repository, password_manager)

    with pytest.raises(AdministratorProvisioningConflictError):
        service.provision("admin", "synthetic administrator password")

    assert repository.rollbacks == 1


@pytest.mark.parametrize(
    "password",
    ["short", "CHANGE_ME_TO_A_LONG_ADMIN_PASSWORD"],
)
def test_bootstrap_password_rejects_weak_or_placeholder_values(password: str) -> None:
    with pytest.raises(ValidationError):
        AdministratorBootstrapSettings(ADMIN_BOOTSTRAP_PASSWORD=password)


def test_bootstrap_settings_mask_password() -> None:
    credentials = AdministratorBootstrapSettings(
        ADMIN_BOOTSTRAP_USERNAME=" ADMIN ",
        ADMIN_BOOTSTRAP_PASSWORD="synthetic administrator password",
    )

    assert "synthetic administrator password" not in repr(credentials)
    assert credentials.ADMIN_BOOTSTRAP_USERNAME == "admin"


def test_bootstrap_rejects_invalid_username_before_database_access() -> None:
    with pytest.raises(ValidationError, match="ADMIN_BOOTSTRAP_USERNAME"):
        AdministratorBootstrapSettings(
            ADMIN_BOOTSTRAP_USERNAME="not a permitted login",
            ADMIN_BOOTSTRAP_PASSWORD="synthetic administrator password",
        )


def test_bootstrap_refuses_non_development_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        APP_ENV=AppEnvironment.STAGING,
        DATABASE_URL="postgresql+psycopg://wedding_app:synthetic@db/wedding_ecard_staging",
        PRIVATE_STORAGE_ROOT="C:/private",
        IMPORT_EVIDENCE_STORAGE_ROOT="C:/private/evidence",
        **SYNTHETIC_AUTH_SETTINGS,
    )
    credentials = AdministratorBootstrapSettings(
        ADMIN_BOOTSTRAP_PASSWORD="synthetic administrator password"
    )
    runtime_called = False

    def fail_runtime(_: Settings) -> None:
        nonlocal runtime_called
        runtime_called = True

    monkeypatch.setattr("app.cli.bootstrap_admin.DatabaseRuntime", fail_runtime)

    with pytest.raises(BootstrapSafetyError, match="APP_ENV=development"):
        execute(settings, credentials)
    assert runtime_called is False


def test_non_permitted_role_is_not_a_model_role() -> None:
    assert list(AdministratorRole) == [AdministratorRole.SUPER_ADMIN]
    assert cast(str, AdministratorRole.SUPER_ADMIN) == "SUPER_ADMIN"


def test_bootstrap_accepts_only_known_authentication_compatible_revisions() -> None:
    assert (
        frozenset(
            {
                "20260814_0002",
                "20260814_0003",
                "20260820_0004",
                "20260820_0005",
                "20260820_0006",
                "20260821_0007",
            }
        )
        == AUTHENTICATION_SCHEMA_REVISIONS
    )
