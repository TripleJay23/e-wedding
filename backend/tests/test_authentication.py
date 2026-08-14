"""Administrator authentication API and service tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import httpx
import jwt
import pytest

from app.api.dependencies import get_authentication_service
from app.core.security import AccessTokenManager, AuthenticationSecurity, PasswordManager
from app.core.settings import Settings
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.services.authentication import AuthenticationService

ADMIN_PASSWORD = "synthetic administrator password"


class FakeDatabaseRuntime:
    """Connection-free runtime for authentication API tests."""

    closed = False

    def close(self) -> None:
        self.closed = True


class FakeAdministratorRepository:
    """Count narrow lookups while returning one synthetic administrator."""

    def __init__(self, administrator: Administrator | None) -> None:
        self.administrator = administrator
        self.username_lookups = 0
        self.id_lookups = 0

    def get_by_username(self, username: str) -> Administrator | None:
        self.username_lookups += 1
        if self.administrator is not None and self.administrator.username == username:
            return self.administrator
        return None

    def get_by_id(self, administrator_id: UUID) -> Administrator | None:
        self.id_lookups += 1
        if self.administrator is not None and self.administrator.id == administrator_id:
            return self.administrator
        return None


@pytest.fixture(scope="module")
def password_manager() -> PasswordManager:
    return PasswordManager()


def make_administrator(
    password_manager: PasswordManager,
    *,
    is_active: bool = True,
) -> Administrator:
    return Administrator(
        id=uuid4(),
        username="admin",
        password_hash=password_manager.hash_password(ADMIN_PASSWORD),
        role=AdministratorRole.SUPER_ADMIN,
        is_active=is_active,
    )


def make_token_manager(settings: Settings) -> AccessTokenManager:
    return AccessTokenManager(
        signing_secret=settings.JWT_SECRET.get_secret_value(),
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        ttl_minutes=settings.ACCESS_TOKEN_TTL_MINUTES,
    )


def build_test_app(
    settings: Settings,
    password_manager: PasswordManager,
    repository: FakeAdministratorRepository,
) -> tuple[object, FakeDatabaseRuntime, AuthenticationService, AccessTokenManager]:
    token_manager = make_token_manager(settings)
    security = AuthenticationSecurity(password_manager, token_manager)
    service = AuthenticationService(repository, password_manager, token_manager)
    runtime = FakeDatabaseRuntime()
    app = create_app(
        settings,
        database_runtime=runtime,  # type: ignore[arg-type]
        authentication_security=security,
    )
    app.dependency_overrides[get_authentication_service] = lambda: service
    return app, runtime, service, token_manager


async def request_app(
    app: object, requests: list[tuple[str, str, dict[str, object]]]
) -> list[httpx.Response]:
    responses: list[httpx.Response] = []
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        for method, path, kwargs in requests:
            responses.append(await client.request(method, path, **kwargs))
    return responses


def test_successful_login_and_me_are_minimal_and_lean(
    development_settings: Settings,
    password_manager: PasswordManager,
) -> None:
    administrator = make_administrator(password_manager)
    repository = FakeAdministratorRepository(administrator)
    app, runtime, _, _ = build_test_app(development_settings, password_manager, repository)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with (
            app.router.lifespan_context(app),  # type: ignore[attr-defined]
            httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
        ):
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"username": " ADMIN ", "password": ADMIN_PASSWORD},
            )
            token = login_response.json()["access_token"]
            me_response = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            return login_response, me_response

    login_response, me_response = asyncio.run(exercise())

    assert login_response.status_code == 200
    login_body = login_response.json()
    assert set(login_body) == {"access_token", "token_type", "expires_in"}
    assert login_body["token_type"] == "bearer"
    assert login_body["expires_in"] == 1800
    assert login_response.headers["cache-control"] == "no-store"
    assert login_response.headers["pragma"] == "no-cache"

    claims = jwt.decode(login_body["access_token"], options={"verify_signature": False})
    assert set(claims) == {"sub", "iss", "aud", "iat", "exp"}
    assert claims["sub"] == str(administrator.id)
    assert claims["iss"] == development_settings.JWT_ISSUER
    assert claims["aud"] == development_settings.JWT_AUDIENCE

    assert me_response.status_code == 200
    assert me_response.json() == {
        "id": str(administrator.id),
        "username": "admin",
        "role": "SUPER_ADMIN",
    }
    assert me_response.headers["cache-control"] == "no-store"
    assert repository.username_lookups == 1
    assert repository.id_lookups == 1
    assert runtime.closed is True


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("admin", "wrong password"),
        ("unknown", ADMIN_PASSWORD),
        ("not a valid username", ADMIN_PASSWORD),
    ],
)
def test_bad_credentials_are_rejected_without_sensitive_output(
    development_settings: Settings,
    password_manager: PasswordManager,
    username: str,
    password: str,
) -> None:
    repository = FakeAdministratorRepository(make_administrator(password_manager))
    app, _, _, _ = build_test_app(development_settings, password_manager, repository)

    [response] = asyncio.run(
        request_app(
            app,
            [
                (
                    "POST",
                    "/api/v1/auth/login",
                    {"json": {"username": username, "password": password}},
                )
            ],
        )
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert ADMIN_PASSWORD not in response.text
    assert "$argon2" not in response.text


@pytest.mark.parametrize("account_state", ["inactive", "non_permitted"])
def test_inactive_and_non_permitted_accounts_cannot_login(
    development_settings: Settings,
    password_manager: PasswordManager,
    account_state: str,
) -> None:
    administrator = make_administrator(
        password_manager,
        is_active=account_state != "inactive",
    )
    if account_state == "non_permitted":
        administrator.role = cast(AdministratorRole, "REPORT_VIEWER")
    repository = FakeAdministratorRepository(administrator)
    app, _, _, _ = build_test_app(development_settings, password_manager, repository)

    [response] = asyncio.run(
        request_app(
            app,
            [
                (
                    "POST",
                    "/api/v1/auth/login",
                    {"json": {"username": "admin", "password": ADMIN_PASSWORD}},
                )
            ],
        )
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}


def test_me_rejects_missing_token(
    development_settings: Settings,
    password_manager: PasswordManager,
) -> None:
    repository = FakeAdministratorRepository(make_administrator(password_manager))
    app, _, _, _ = build_test_app(development_settings, password_manager, repository)

    [response] = asyncio.run(request_app(app, [("GET", "/api/v1/auth/me", {})]))

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert repository.id_lookups == 0


@pytest.mark.parametrize("token", ["not-a-jwt", "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0."])
def test_me_rejects_malformed_or_invalid_token(
    development_settings: Settings,
    password_manager: PasswordManager,
    token: str,
) -> None:
    repository = FakeAdministratorRepository(make_administrator(password_manager))
    app, _, _, _ = build_test_app(development_settings, password_manager, repository)

    [response] = asyncio.run(
        request_app(
            app,
            [("GET", "/api/v1/auth/me", {"headers": {"Authorization": f"Bearer {token}"}})],
        )
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert repository.id_lookups == 0


def test_me_rejects_expired_token(
    development_settings: Settings,
    password_manager: PasswordManager,
) -> None:
    administrator = make_administrator(password_manager)
    repository = FakeAdministratorRepository(administrator)
    app, _, _, token_manager = build_test_app(
        development_settings,
        password_manager,
        repository,
    )
    expired = token_manager.issue(
        administrator.id,
        now=datetime.now(UTC) - timedelta(minutes=31),
    ).value

    [response] = asyncio.run(
        request_app(
            app,
            [
                (
                    "GET",
                    "/api/v1/auth/me",
                    {"headers": {"Authorization": f"Bearer {expired}"}},
                )
            ],
        )
    )

    assert response.status_code == 401
    assert repository.id_lookups == 0


def test_me_reloads_and_rejects_an_account_deactivated_after_token_issue(
    development_settings: Settings,
    password_manager: PasswordManager,
) -> None:
    administrator = make_administrator(password_manager)
    repository = FakeAdministratorRepository(administrator)
    app, _, service, _ = build_test_app(development_settings, password_manager, repository)
    token = service.login("admin", ADMIN_PASSWORD).access_token.value
    administrator.is_active = False

    [response] = asyncio.run(
        request_app(
            app,
            [
                (
                    "GET",
                    "/api/v1/auth/me",
                    {"headers": {"Authorization": f"Bearer {token}"}},
                )
            ],
        )
    )

    assert response.status_code == 401
    assert repository.id_lookups == 1
