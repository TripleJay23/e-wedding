"""PostgreSQL 17 acceptance for staging existing-admin password rotation."""

import argparse
import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cli import staging_alembic, staging_bootstrap_admin, staging_rotate_admin_password
from app.cli.staging_alembic import (
    STAGING_MIGRATION_AUTHORIZATION,
    StagingMigrationAuthorizationSettings,
)
from app.cli.staging_bootstrap_admin import StagingAdministratorBootstrapSettings
from app.cli.staging_rotate_admin_password import (
    StagingAdministratorPasswordRotationSettings,
)
from app.core.security import AuthenticationSecurity, PasswordManager
from app.core.settings import AppEnvironment, Settings
from app.db.guards import validated_database_url
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator
from app.repositories.administrators import AdministratorRepository
from app.services.admin_password_rotation import AdministratorPasswordRotationError

STAGING_URL = os.getenv("WEDDING_STAGING_BOOTSTRAP_DATABASE_URL")
OLD_PASSWORD = f"synthetic-original-{secrets.token_urlsafe(24)}"
FIRST_PASSWORD = f"synthetic-first-rotation-{secrets.token_urlsafe(24)}"
SECOND_PASSWORD = f"synthetic-second-rotation-{secrets.token_urlsafe(24)}"
CONCURRENT_PASSWORD = f"synthetic-concurrent-rotation-{secrets.token_urlsafe(24)}"
JWT_SECRET = f"synthetic-staging-jwt-{secrets.token_urlsafe(32)}"

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        STAGING_URL is None,
        reason="WEDDING_STAGING_BOOTSTRAP_DATABASE_URL is not explicitly configured",
    ),
]


def staging_settings(tmp_path: Path) -> Settings:
    assert STAGING_URL is not None
    return Settings(
        APP_ENV=AppEnvironment.STAGING,
        DATABASE_URL=STAGING_URL,
        JWT_SECRET=JWT_SECRET,
        JWT_ISSUER="synthetic-staging-issuer",
        JWT_AUDIENCE="synthetic-staging-audience",
        PRIVATE_STORAGE_ROOT=tmp_path / "private",
        IMPORT_EVIDENCE_STORAGE_ROOT=tmp_path / "private" / "import-evidence",
    )


def ensure_schema_head(settings: Settings) -> None:
    staging_alembic.execute(
        argparse.Namespace(command="upgrade", revision="head"),
        settings,
        StagingMigrationAuthorizationSettings(
            STAGING_MIGRATION_AUTHORIZATION=STAGING_MIGRATION_AUTHORIZATION
        ),
    )


def delete_administrators(settings: Settings) -> None:
    url, _ = validated_database_url(settings)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            for administrator in session.execute(select(Administrator)).scalars():
                session.delete(administrator)
            session.commit()
    finally:
        engine.dispose()


def persisted_administrator(settings: Settings) -> Administrator:
    url, _ = validated_database_url(settings)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine, expire_on_commit=False) as session:
            return session.execute(select(Administrator)).scalar_one()
    finally:
        engine.dispose()


async def authenticate(
    settings: Settings,
    password: str,
) -> tuple[int, dict[str, object] | None]:
    runtime = DatabaseRuntime(settings)
    app = create_app(
        settings,
        database_runtime=runtime,
        authentication_security=AuthenticationSecurity.from_settings(settings),
    )
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": password},
        )
        if response.status_code != 200:
            return response.status_code, None
        token = response.json()["access_token"]
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me.status_code == 200
        return response.status_code, me.json()


def rotation_settings(
    administrator_id: UUID, password: str
) -> StagingAdministratorPasswordRotationSettings:
    return StagingAdministratorPasswordRotationSettings(
        STAGING_ADMIN_ROTATION_EXPECTED_UUID=administrator_id,
        STAGING_ADMIN_ROTATION_PASSWORD=password,
    )


def assert_output_is_secret_free(output: str) -> None:
    assert STAGING_URL is not None
    database_password = make_url(STAGING_URL).password
    assert database_password is not None
    for secret in (
        database_password,
        OLD_PASSWORD,
        FIRST_PASSWORD,
        SECOND_PASSWORD,
        CONCURRENT_PASSWORD,
        JWT_SECRET,
        STAGING_URL,
    ):
        assert secret not in output


def test_rotation_preserves_identity_updates_auth_and_supports_repeated_rotation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = staging_settings(tmp_path)
    ensure_schema_head(settings)
    delete_administrators(settings)
    password_manager = PasswordManager()
    try:
        provisioned = staging_bootstrap_admin.execute(
            settings,
            StagingAdministratorBootstrapSettings(
                STAGING_ADMIN_BOOTSTRAP_USERNAME="admin",
                STAGING_ADMIN_BOOTSTRAP_PASSWORD=OLD_PASSWORD,
            ),
        )
        before = persisted_administrator(settings)
        identity = (
            before.id,
            before.singleton_key,
            before.username,
            before.role,
            before.is_active,
            before.created_at,
        )
        original_hash = before.password_hash
        assert asyncio.run(authenticate(settings, OLD_PASSWORD))[0] == 200

        with pytest.raises(AdministratorPasswordRotationError, match="expected UUID"):
            staging_rotate_admin_password.execute(
                settings,
                rotation_settings(uuid4(), FIRST_PASSWORD),
            )
        assert persisted_administrator(settings).password_hash == original_hash

        with pytest.raises(AdministratorPasswordRotationError, match="must differ"):
            staging_rotate_admin_password.execute(
                settings,
                rotation_settings(provisioned.id, OLD_PASSWORD),
            )
        assert persisted_administrator(settings).password_hash == original_hash

        first = staging_rotate_admin_password.execute(
            settings,
            rotation_settings(provisioned.id, FIRST_PASSWORD),
        )
        after_first = persisted_administrator(settings)
        assert first.id == provisioned.id
        assert (
            after_first.id,
            after_first.singleton_key,
            after_first.username,
            after_first.role,
            after_first.is_active,
            after_first.created_at,
        ) == identity
        assert after_first.password_hash != original_hash
        assert after_first.updated_at >= before.updated_at
        assert after_first.password_hash.startswith("$argon2id$")
        assert password_manager.verify_password(FIRST_PASSWORD, after_first.password_hash) is True
        assert password_manager.verify_password(OLD_PASSWORD, after_first.password_hash) is False
        assert asyncio.run(authenticate(settings, OLD_PASSWORD))[0] == 401
        first_login, first_me = asyncio.run(authenticate(settings, FIRST_PASSWORD))
        assert first_login == 200
        assert first_me == {
            "id": str(provisioned.id),
            "username": "admin",
            "role": "SUPER_ADMIN",
        }

        staging_rotate_admin_password.execute(
            settings,
            rotation_settings(provisioned.id, SECOND_PASSWORD),
        )
        after_second = persisted_administrator(settings)
        assert after_second.id == provisioned.id
        assert after_second.password_hash != after_first.password_hash
        assert password_manager.verify_password(SECOND_PASSWORD, after_second.password_hash) is True
        assert password_manager.verify_password(FIRST_PASSWORD, after_second.password_hash) is False
        assert asyncio.run(authenticate(settings, FIRST_PASSWORD))[0] == 401
        assert asyncio.run(authenticate(settings, SECOND_PASSWORD))[0] == 200

        url, _ = validated_database_url(settings)
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with Session(engine) as session:
                assert session.scalar(select(func.count()).select_from(Administrator)) == 1
        finally:
            engine.dispose()
    finally:
        delete_administrators(settings)

    captured = capsys.readouterr()
    assert_output_is_secret_free(captured.out + captured.err)


def test_concurrent_same_rotation_serializes_to_one_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = staging_settings(tmp_path)
    ensure_schema_head(settings)
    delete_administrators(settings)
    password_manager = PasswordManager()
    try:
        provisioned = staging_bootstrap_admin.execute(
            settings,
            StagingAdministratorBootstrapSettings(
                STAGING_ADMIN_BOOTSTRAP_PASSWORD=OLD_PASSWORD,
            ),
        )

        def rotate_once() -> str:
            try:
                staging_rotate_admin_password.execute(
                    settings,
                    rotation_settings(provisioned.id, CONCURRENT_PASSWORD),
                )
            except AdministratorPasswordRotationError:
                return "refused"
            return "rotated"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(lambda _: rotate_once(), range(2)))

        assert outcomes == ["refused", "rotated"]
        persisted = persisted_administrator(settings)
        assert persisted.id == provisioned.id
        assert (
            password_manager.verify_password(CONCURRENT_PASSWORD, persisted.password_hash) is True
        )
    finally:
        delete_administrators(settings)

    captured = capsys.readouterr()
    assert_output_is_secret_free(captured.out + captured.err)


def test_rotation_persistence_failure_rolls_back_original_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = staging_settings(tmp_path)
    ensure_schema_head(settings)
    delete_administrators(settings)
    try:
        provisioned = staging_bootstrap_admin.execute(
            settings,
            StagingAdministratorBootstrapSettings(
                STAGING_ADMIN_BOOTSTRAP_PASSWORD=OLD_PASSWORD,
            ),
        )
        original_hash = persisted_administrator(settings).password_hash

        def fail_commit(repository: AdministratorRepository) -> None:
            repository._session.flush()
            raise SQLAlchemyError("synthetic post-flush rotation failure")

        monkeypatch.setattr(AdministratorRepository, "commit", fail_commit)
        with pytest.raises(SQLAlchemyError, match="synthetic post-flush"):
            staging_rotate_admin_password.execute(
                settings,
                rotation_settings(provisioned.id, FIRST_PASSWORD),
            )

        assert persisted_administrator(settings).password_hash == original_hash
    finally:
        delete_administrators(settings)

    captured = capsys.readouterr()
    assert_output_is_secret_free(captured.out + captured.err)
