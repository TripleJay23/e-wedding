"""Authenticated M5 QR metadata routes and privacy boundaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.dependencies import (
    get_authentication_service,
    get_current_administrator,
    get_qr_credential_service,
)
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.services.authentication import AuthenticationRejectedError
from app.services.qr import (
    QrConflictError,
    QrPersistenceError,
    QrResourceNotFoundError,
    QrStorageError,
)


class FakeDatabaseRuntime:
    def close(self) -> None:
        pass


class RejectAuthenticationService:
    def current_administrator(self, token: str) -> Administrator:
        raise AuthenticationRejectedError("synthetic rejection")


def make_administrator() -> Administrator:
    now = datetime.now(UTC)
    return Administrator(
        id=uuid4(),
        username="admin",
        password_hash="synthetic",
        role=AdministratorRole.SUPER_ADMIN,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_credential(event_id: UUID, guest_id: UUID) -> GuestQrCredential:
    now = datetime.now(UTC)
    credential_id = uuid4()
    return GuestQrCredential(
        id=credential_id,
        event_id=event_id,
        guest_id=guest_id,
        token_hash="a" * 64,
        image_storage_key=(f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png"),
        image_sha256="b" * 64,
        status=QrCredentialStatus.ACTIVE,
        replaces_credential_id=None,
        issued_by=uuid4(),
        issued_at=now,
        deactivated_at=None,
        superseded_at=None,
        created_at=now,
        updated_at=now,
    )


class FakeQrService:
    def __init__(self, event_id: UUID, guest_id: UUID) -> None:
        self.credential = make_credential(event_id, guest_id)
        self.calls: list[tuple[str, Any]] = []
        self.next_error: Exception | None = None

    def _raise_if_requested(self) -> None:
        if self.next_error is not None:
            requested = self.next_error
            self.next_error = None
            raise requested

    def issue(self, guest_id: UUID, *, administrator_id: UUID):
        self._raise_if_requested()
        self.calls.append(("issue", (guest_id, administrator_id)))
        return self.credential

    def get(self, guest_id: UUID):
        self._raise_if_requested()
        self.calls.append(("get", guest_id))
        return self.credential

    def deactivate(self, guest_id: UUID):
        self._raise_if_requested()
        self.calls.append(("deactivate", guest_id))
        self.credential.status = QrCredentialStatus.INACTIVE
        self.credential.deactivated_at = datetime.now(UTC)
        return self.credential

    def activate(self, guest_id: UUID):
        self._raise_if_requested()
        self.calls.append(("activate", guest_id))
        self.credential.status = QrCredentialStatus.ACTIVE
        self.credential.deactivated_at = None
        return self.credential

    def regenerate(self, guest_id: UUID, *, administrator_id: UUID):
        self._raise_if_requested()
        self.calls.append(("regenerate", (guest_id, administrator_id)))
        previous_id = self.credential.id
        self.credential = make_credential(self.credential.event_id, guest_id)
        self.credential.replaces_credential_id = previous_id
        return self.credential

    def list_for_event(self, event_id: UUID, *, limit: int, offset: int):
        self._raise_if_requested()
        self.calls.append(("list", (event_id, limit, offset)))
        return [self.credential]


def build_app(
    settings,
    *,
    authenticated: bool = True,
) -> tuple[object, FakeQrService, Administrator, UUID, UUID]:
    event_id = uuid4()
    guest_id = uuid4()
    administrator = make_administrator()
    service = FakeQrService(event_id, guest_id)
    app = create_app(settings, database_runtime=FakeDatabaseRuntime())  # type: ignore[arg-type]
    app.dependency_overrides[get_qr_credential_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_administrator] = lambda: administrator
    else:
        app.dependency_overrides[get_authentication_service] = lambda: RejectAuthenticationService()
    return app, service, administrator, event_id, guest_id


async def request(app: object, method: str, path: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        return await client.request(method, path, **kwargs)


def test_issue_returns_metadata_only_without_hash_path_token_or_guest_pii(
    development_settings,
) -> None:
    app, service, administrator, event_id, guest_id = build_app(development_settings)
    response = asyncio.run(request(app, "POST", f"/api/v1/guests/{guest_id}/qr-code"))

    assert response.status_code == 201
    assert response.json() == {
        "id": str(service.credential.id),
        "event_id": str(event_id),
        "guest_id": str(guest_id),
        "status": "ACTIVE",
        "replaces_credential_id": None,
        "issued_at": service.credential.issued_at.isoformat().replace("+00:00", "Z"),
        "deactivated_at": None,
        "superseded_at": None,
        "created_at": service.credential.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": service.credential.updated_at.isoformat().replace("+00:00", "Z"),
    }
    assert service.calls == [("issue", (guest_id, administrator.id))]
    forbidden = (
        "token",
        "hash",
        "storage",
        "path",
        "synthetic guest",
        "example.test",
        "+255",
    )
    assert all(value not in response.text.casefold() for value in forbidden)


def test_get_lifecycle_regenerate_and_event_list_routes_are_explicit_and_bounded(
    development_settings,
) -> None:
    app, service, administrator, event_id, guest_id = build_app(development_settings)
    calls = [
        ("GET", f"/api/v1/guests/{guest_id}/qr-code"),
        ("POST", f"/api/v1/guests/{guest_id}/qr-code/deactivate"),
        ("POST", f"/api/v1/guests/{guest_id}/qr-code/activate"),
        ("POST", f"/api/v1/guests/{guest_id}/qr-code/regenerate"),
        ("GET", f"/api/v1/events/{event_id}/qr-codes?limit=10&offset=2"),
    ]
    responses = [asyncio.run(request(app, method, path)) for method, path in calls]

    assert [response.status_code for response in responses] == [200, 200, 200, 201, 200]
    assert responses[1].json()["status"] == "INACTIVE"
    assert responses[2].json()["status"] == "ACTIVE"
    assert responses[3].json()["replaces_credential_id"] is not None
    assert responses[4].json()["limit"] == 10
    assert ("regenerate", (guest_id, administrator.id)) in service.calls
    assert ("list", (event_id, 10, 2)) in service.calls

    invalid_page = asyncio.run(request(app, "GET", f"/api/v1/events/{event_id}/qr-codes?limit=101"))
    assert invalid_page.status_code == 422


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_missing_or_invalid_authentication_rejects_before_qr_service(
    development_settings,
    authorization: str | None,
) -> None:
    app, service, _, _, guest_id = build_app(development_settings, authenticated=False)
    headers = {} if authorization is None else {"Authorization": authorization}
    response = asyncio.run(
        request(app, "GET", f"/api/v1/guests/{guest_id}/qr-code", headers=headers)
    )

    assert response.status_code == 401
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (QrResourceNotFoundError("guest not found"), 404, "guest not found"),
        (QrConflictError("QR credential already exists"), 409, "already exists"),
        (QrStorageError("C:/private/secret.png"), 503, "storage is unavailable"),
        (QrPersistenceError("postgresql://secret"), 503, "service is unavailable"),
    ],
)
def test_domain_errors_are_bounded_without_internal_details(
    development_settings,
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    app, service, _, _, guest_id = build_app(development_settings)
    service.next_error = error
    response = asyncio.run(request(app, "POST", f"/api/v1/guests/{guest_id}/qr-code"))

    assert response.status_code == expected_status
    assert expected_detail in response.json()["detail"]
    assert "C:/private" not in response.text
    assert "postgresql://" not in response.text
