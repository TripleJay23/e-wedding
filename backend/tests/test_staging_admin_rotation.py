"""Focused safety tests for staging existing-administrator password rotation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.cli import staging_rotate_admin_password
from app.cli.staging_rotate_admin_password import (
    StagingAdministratorPasswordRotationSettings,
)
from app.cli.staging_safety import StagingRuntimeSafetyError
from app.core.security import PasswordManager
from app.core.settings import AppEnvironment, Settings
from app.models.administrator import Administrator, AdministratorRole
from app.services.admin_password_rotation import (
    AdministratorPasswordRotationError,
    AdministratorPasswordRotationService,
)

OLD_PASSWORD = "synthetic original administrator password"
NEW_PASSWORD = "synthetic replacement administrator password"
SYNTHETIC_AUTH_SETTINGS = {
    "JWT_SECRET": "synthetic-jwt-signing-secret-at-least-32-bytes",
    "JWT_ISSUER": "synthetic-issuer",
    "JWT_AUDIENCE": "synthetic-audience",
}


def runtime_settings(
    tmp_path: Path,
    *,
    environment: AppEnvironment = AppEnvironment.STAGING,
    database_url: str = (
        "postgresql+psycopg://wedding_staging_app:synthetic@127.0.0.1:55432/wedding_ecard_staging"
    ),
) -> Settings:
    private_root = tmp_path / "private"
    return Settings(
        APP_ENV=environment,
        DATABASE_URL=database_url,
        PRIVATE_STORAGE_ROOT=private_root,
        IMPORT_EVIDENCE_STORAGE_ROOT=private_root / "import-evidence",
        **SYNTHETIC_AUTH_SETTINGS,
    )


def administrator(
    password_manager: PasswordManager,
    *,
    administrator_id: UUID | None = None,
    password: str = OLD_PASSWORD,
    active: bool = True,
) -> Administrator:
    now = datetime.now(UTC)
    return Administrator(
        id=administrator_id or uuid4(),
        singleton_key=1,
        username="admin",
        password_hash=password_manager.hash_password(password),
        role=AdministratorRole.SUPER_ADMIN,
        is_active=active,
        created_at=now,
        updated_at=now,
    )


@dataclass
class FakeRotationStore:
    administrators: list[Administrator]
    fail_commit: bool = False
    committed: bool = False
    rolled_back: bool = False
    original_hashes: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.original_hashes = [item.password_hash for item in self.administrators]

    def list_for_update(self) -> list[Administrator]:
        return self.administrators

    def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True
        for item, password_hash in zip(self.administrators, self.original_hashes, strict=True):
            item.password_hash = password_hash


def test_rotation_changes_only_password_state() -> None:
    password_manager = PasswordManager()
    persisted = administrator(password_manager)
    original = (
        persisted.id,
        persisted.singleton_key,
        persisted.username,
        persisted.role,
        persisted.is_active,
        persisted.created_at,
    )
    original_hash = persisted.password_hash
    store = FakeRotationStore([persisted])

    rotated = AdministratorPasswordRotationService(store, password_manager).rotate(
        persisted.id,
        NEW_PASSWORD,
    )

    assert rotated is persisted
    assert store.committed is True
    assert store.rolled_back is False
    assert persisted.password_hash != original_hash
    assert persisted.password_hash.startswith("$argon2id$")
    assert password_manager.verify_password(NEW_PASSWORD, persisted.password_hash) is True
    assert password_manager.verify_password(OLD_PASSWORD, persisted.password_hash) is False
    assert (
        persisted.id,
        persisted.singleton_key,
        persisted.username,
        persisted.role,
        persisted.is_active,
        persisted.created_at,
    ) == original


@pytest.mark.parametrize(
    ("administrators", "expected_id", "message"),
    [
        ([], uuid4(), "does not exist"),
        (
            [
                administrator(PasswordManager()),
                administrator(PasswordManager()),
            ],
            uuid4(),
            "singleton state is invalid",
        ),
    ],
)
def test_rotation_refuses_missing_or_multiple_administrators(
    administrators: list[Administrator],
    expected_id: UUID,
    message: str,
) -> None:
    store = FakeRotationStore(administrators)

    with pytest.raises(AdministratorPasswordRotationError, match=message):
        AdministratorPasswordRotationService(store, PasswordManager()).rotate(
            expected_id,
            NEW_PASSWORD,
        )

    assert store.committed is False


def test_rotation_refuses_unexpected_administrator_uuid() -> None:
    password_manager = PasswordManager()
    persisted = administrator(password_manager)
    store = FakeRotationStore([persisted])

    with pytest.raises(AdministratorPasswordRotationError, match="expected UUID"):
        AdministratorPasswordRotationService(store, password_manager).rotate(
            uuid4(),
            NEW_PASSWORD,
        )

    assert persisted.password_hash == store.original_hashes[0]
    assert store.committed is False


@pytest.mark.parametrize("invalid_state", ["singleton", "role", "inactive"])
def test_rotation_refuses_invalid_administrator_state(invalid_state: str) -> None:
    password_manager = PasswordManager()
    persisted = administrator(password_manager)
    if invalid_state == "singleton":
        persisted.singleton_key = 2
    elif invalid_state == "role":
        persisted.role = "OTHER"  # type: ignore[assignment]
    else:
        persisted.is_active = False
    store = FakeRotationStore([persisted])

    with pytest.raises(AdministratorPasswordRotationError, match="state is invalid"):
        AdministratorPasswordRotationService(store, password_manager).rotate(
            persisted.id,
            NEW_PASSWORD,
        )

    assert persisted.password_hash == store.original_hashes[0]
    assert store.committed is False


def test_rotation_refuses_same_password_without_mutation() -> None:
    password_manager = PasswordManager()
    persisted = administrator(password_manager)
    original_hash = persisted.password_hash
    store = FakeRotationStore([persisted])

    with pytest.raises(AdministratorPasswordRotationError, match="must differ"):
        AdministratorPasswordRotationService(store, password_manager).rotate(
            persisted.id,
            OLD_PASSWORD,
        )

    assert persisted.password_hash == original_hash
    assert store.committed is False


def test_rotation_rolls_back_commit_failure() -> None:
    password_manager = PasswordManager()
    persisted = administrator(password_manager)
    original_hash = persisted.password_hash
    store = FakeRotationStore([persisted], fail_commit=True)

    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        AdministratorPasswordRotationService(store, password_manager).rotate(
            persisted.id,
            NEW_PASSWORD,
        )

    assert store.rolled_back is True
    assert persisted.password_hash == original_hash


def test_rotation_settings_mask_and_validate_replacement_secret() -> None:
    expected_id = uuid4()
    settings = StagingAdministratorPasswordRotationSettings(
        STAGING_ADMIN_ROTATION_EXPECTED_UUID=expected_id,
        STAGING_ADMIN_ROTATION_PASSWORD=NEW_PASSWORD,
    )

    assert expected_id == settings.STAGING_ADMIN_ROTATION_EXPECTED_UUID
    assert settings.password_value() == NEW_PASSWORD
    assert NEW_PASSWORD not in repr(settings)

    invalid_password = "CHANGE_ME_do-not-print-this-replacement"
    with pytest.raises(ValidationError) as error:
        StagingAdministratorPasswordRotationSettings(
            STAGING_ADMIN_ROTATION_EXPECTED_UUID=expected_id,
            STAGING_ADMIN_ROTATION_PASSWORD=invalid_password,
        )
    assert invalid_password not in str(error.value)


@pytest.mark.parametrize(
    ("environment", "database_url"),
    [
        (
            AppEnvironment.DEVELOPMENT,
            "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev",
        ),
        (
            AppEnvironment.TEST,
            "postgresql+psycopg://wedding_app:synthetic@localhost/"
            "wedding_ecard_disposable_rotation_guard",
        ),
        (
            AppEnvironment.PRODUCTION,
            "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_prod",
        ),
    ],
)
def test_rotation_refuses_wrong_environment_before_runtime_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: AppEnvironment,
    database_url: str,
) -> None:
    runtime_called = False

    def fail_runtime(_: Settings) -> None:
        nonlocal runtime_called
        runtime_called = True

    monkeypatch.setattr(staging_rotate_admin_password, "DatabaseRuntime", fail_runtime)
    rotation = StagingAdministratorPasswordRotationSettings(
        STAGING_ADMIN_ROTATION_EXPECTED_UUID=uuid4(),
        STAGING_ADMIN_ROTATION_PASSWORD=NEW_PASSWORD,
    )

    with pytest.raises(StagingRuntimeSafetyError, match="APP_ENV=staging"):
        staging_rotate_admin_password.execute(
            runtime_settings(
                tmp_path,
                environment=environment,
                database_url=database_url,
            ),
            rotation,
        )
    assert runtime_called is False


def test_rotation_requires_database_at_repository_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(
        execute=lambda statement: SimpleNamespace(scalar_one=lambda: "20260821_0007")
    )

    class TransactionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        def __init__(self, settings: Settings) -> None:
            self.engine = SimpleNamespace(begin=lambda: TransactionContext())

        def close(self) -> None:
            return None

    monkeypatch.setattr(staging_rotate_admin_password, "DatabaseRuntime", FakeRuntime)
    monkeypatch.setattr(
        staging_rotate_admin_password,
        "verify_staging_connection",
        lambda *args: "17.99",
    )
    monkeypatch.setattr(
        staging_rotate_admin_password,
        "repository_solo_head",
        lambda: "20260821_0008",
    )
    rotation = StagingAdministratorPasswordRotationSettings(
        STAGING_ADMIN_ROTATION_EXPECTED_UUID=uuid4(),
        STAGING_ADMIN_ROTATION_PASSWORD=NEW_PASSWORD,
    )

    with pytest.raises(StagingRuntimeSafetyError, match="repository sole head"):
        staging_rotate_admin_password.execute(runtime_settings(tmp_path), rotation)


def test_rotation_refuses_incompatible_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_schema_query(statement: object) -> object:
        raise SQLAlchemyError("synthetic incompatible schema")

    connection = SimpleNamespace(execute=fail_schema_query)

    class TransactionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *args: object) -> None:
            return None

    class FakeRuntime:
        def __init__(self, settings: Settings) -> None:
            self.engine = SimpleNamespace(begin=lambda: TransactionContext())

        def close(self) -> None:
            return None

    monkeypatch.setattr(staging_rotate_admin_password, "DatabaseRuntime", FakeRuntime)
    monkeypatch.setattr(
        staging_rotate_admin_password,
        "verify_staging_connection",
        lambda *args: "17.99",
    )
    monkeypatch.setattr(
        staging_rotate_admin_password,
        "repository_solo_head",
        lambda: "20260821_0008",
    )
    rotation = StagingAdministratorPasswordRotationSettings(
        STAGING_ADMIN_ROTATION_EXPECTED_UUID=uuid4(),
        STAGING_ADMIN_ROTATION_PASSWORD=NEW_PASSWORD,
    )

    with pytest.raises(StagingRuntimeSafetyError, match="verified current schema"):
        staging_rotate_admin_password.execute(runtime_settings(tmp_path), rotation)
