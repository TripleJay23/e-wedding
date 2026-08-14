"""Opt-in disposable PostgreSQL authentication verification."""

import os

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.guards import validated_database_url, verify_connected_database
from app.models.administrator import Administrator, AdministratorRole
from app.repositories.administrators import AdministratorRepository
from app.services.admin_provisioning import AdministratorProvisioningService
from app.services.authentication import AuthenticationService

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
]


def configure_test_environment(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Set an explicit disposable target and synthetic authentication secrets."""
    assert DISPOSABLE_URL is not None
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv("JWT_SECRET", "synthetic-jwt-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    return Settings()


def test_authentication_schema_and_service_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configure_test_environment(monkeypatch)
    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["check"]) == 0

    url, identity = validated_database_url(settings)
    from sqlalchemy import create_engine

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            verify_connected_database(connection, identity.database)
            inspector = inspect(connection)
            assert {column["name"] for column in inspector.get_columns("users")} == {
                "id",
                "singleton_key",
                "username",
                "password_hash",
                "role",
                "is_active",
                "created_at",
                "updated_at",
            }
            assert {index["name"] for index in inspector.get_indexes("users")} >= {
                "ix_users_username"
            }
            constraint_names = {
                constraint["name"] for constraint in inspector.get_check_constraints("users")
            }
            assert {
                "ck_users_password_hash_argon2id",
                "ck_users_role_super_admin",
                "ck_users_singleton_key",
                "ck_users_username_canonical",
            } <= constraint_names

        security = AuthenticationSecurity.from_settings(settings)
        with Session(engine, expire_on_commit=False) as session:
            repository = AdministratorRepository(session)
            administrator = AdministratorProvisioningService(
                repository,
                security.passwords,
            ).provision("admin", "synthetic administrator password")
            result = AuthenticationService(
                repository,
                security.passwords,
                security.access_tokens,
            ).login("admin", "synthetic administrator password")

            assert result.administrator.id == administrator.id
            assert (
                AuthenticationService(
                    repository,
                    security.passwords,
                    security.access_tokens,
                )
                .current_administrator(result.access_token.value)
                .id
                == administrator.id
            )

            second = Administrator(
                username="other-admin",
                password_hash=security.passwords.hash_password(
                    "second synthetic administrator password"
                ),
                role=AdministratorRole.SUPER_ADMIN,
                is_active=True,
            )
            session.add(second)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            persisted = session.get(Administrator, administrator.id)
            assert persisted is not None
            session.delete(persisted)
            session.commit()
    finally:
        engine.dispose()
