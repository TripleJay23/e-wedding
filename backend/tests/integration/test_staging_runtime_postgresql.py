"""Opt-in PostgreSQL 17 acceptance for the guarded local staging operator workflow."""

import argparse
import asyncio
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.cli import staging_alembic, staging_bootstrap_admin
from app.cli.staging_alembic import (
    STAGING_MIGRATION_AUTHORIZATION,
    StagingMigrationAuthorizationSettings,
)
from app.cli.staging_bootstrap_admin import StagingAdministratorBootstrapSettings
from app.core.security import AuthenticationSecurity
from app.core.settings import AppEnvironment, Settings
from app.db.guards import validated_database_url
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.services.admin_provisioning import AdministratorAlreadyProvisionedError

STAGING_URL = os.getenv("WEDDING_STAGING_BOOTSTRAP_DATABASE_URL")
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
        JWT_SECRET="synthetic-staging-jwt-signing-secret-at-least-32-bytes",
        JWT_ISSUER="synthetic-staging-issuer",
        JWT_AUDIENCE="synthetic-staging-audience",
        PRIVATE_STORAGE_ROOT=tmp_path / "private",
        IMPORT_EVIDENCE_STORAGE_ROOT=tmp_path / "private" / "import-evidence",
    )


async def verify_staging_app_startup(settings: Settings) -> None:
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
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_fresh_staging_migration_admin_no_overwrite_and_runtime(
    tmp_path: Path,
) -> None:
    settings = staging_settings(tmp_path)
    authorization = StagingMigrationAuthorizationSettings(
        STAGING_MIGRATION_AUTHORIZATION=STAGING_MIGRATION_AUTHORIZATION
    )
    migration_args = argparse.Namespace(command="upgrade", revision="head")

    staging_alembic.execute(migration_args, settings, authorization)
    staging_alembic.execute(migration_args, settings, authorization)
    staging_alembic.execute(
        argparse.Namespace(command="current", revision=None),
        settings,
        None,
    )
    staging_alembic.execute(
        argparse.Namespace(command="check", revision=None),
        settings,
        None,
    )

    credentials = StagingAdministratorBootstrapSettings(
        STAGING_ADMIN_BOOTSTRAP_USERNAME="admin",
        STAGING_ADMIN_BOOTSTRAP_PASSWORD="synthetic staging administrator password",
    )
    administrator = staging_bootstrap_admin.execute(settings, credentials)
    assert administrator.role is AdministratorRole.SUPER_ADMIN
    assert administrator.is_active is True

    url, _ = validated_database_url(settings)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine, expire_on_commit=False) as session:
            persisted = session.execute(select(Administrator)).scalar_one()
            fingerprint = (
                persisted.id,
                persisted.username,
                persisted.password_hash,
                persisted.role,
                persisted.is_active,
                persisted.created_at,
                persisted.updated_at,
            )
            assert persisted.password_hash.startswith("$argon2id$")
            assert "synthetic staging administrator password" not in persisted.password_hash
            assert session.scalar(select(func.count()).select_from(Administrator)) == 1

        with pytest.raises(AdministratorAlreadyProvisionedError):
            staging_bootstrap_admin.execute(
                settings,
                StagingAdministratorBootstrapSettings(
                    STAGING_ADMIN_BOOTSTRAP_USERNAME="replacement-admin",
                    STAGING_ADMIN_BOOTSTRAP_PASSWORD=(
                        "replacement synthetic staging administrator password"
                    ),
                ),
            )

        with Session(engine, expire_on_commit=False) as session:
            unchanged = session.execute(select(Administrator)).scalar_one()
            assert (
                unchanged.id,
                unchanged.username,
                unchanged.password_hash,
                unchanged.role,
                unchanged.is_active,
                unchanged.created_at,
                unchanged.updated_at,
            ) == fingerprint
            assert session.execute(text("SELECT count(*) FROM events")).scalar_one() == 0
            session.delete(unchanged)
            session.commit()

        asyncio.run(verify_staging_app_startup(settings))
    finally:
        engine.dispose()
