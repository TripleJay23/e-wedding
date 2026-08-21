"""Authenticated M7 scan/read API contracts and privacy boundaries."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import (
    get_attendance_service,
    get_authentication_service,
    get_current_administrator,
)
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.models.attendance import GateScanOutcome
from app.services.attendance import (
    AttendancePersistenceError,
    AttendanceRecordView,
    AttendanceResourceNotFoundError,
    GateScanAttemptView,
    GateScanResult,
)
from app.services.authentication import AuthenticationRejectedError

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


class FakeDatabaseRuntime:
    def close(self) -> None:
        pass


class RejectAuthenticationService:
    def current_administrator(self, token: str) -> Administrator:
        raise AuthenticationRejectedError("synthetic rejection")


class FakeAttendanceService:
    def __init__(self) -> None:
        self.event_id = uuid4()
        self.guest_id = uuid4()
        self.attendance_id = uuid4()
        self.scan_id = uuid4()
        self.calls: list[tuple[str, Any]] = []
        self.next_error: Exception | None = None

    def _raise_if_requested(self) -> None:
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error

    def attendance_view(self) -> AttendanceRecordView:
        return AttendanceRecordView(
            id=self.attendance_id,
            event_id=self.event_id,
            guest_id=self.guest_id,
            guest_display_name="Synthetic Gate Guest",
            invitation_type="Family",
            table_number="T7",
            allowed_companions=2,
            companions_admitted=1,
            admitted_party_size=2,
            checked_in_at=NOW,
        )

    def scan(
        self,
        *,
        event_id,
        presented_credential,
        companions_admitted,
        administrator_id,
    ) -> GateScanResult:
        self._raise_if_requested()
        self.calls.append(
            (
                "scan",
                (event_id, presented_credential, companions_admitted, administrator_id),
            )
        )
        return GateScanResult(
            scan_attempt_id=self.scan_id,
            event_id=event_id,
            outcome=GateScanOutcome.ADMITTED,
            admitted=True,
            already_checked_in=False,
            guest_id=self.guest_id,
            attendance_id=self.attendance_id,
            guest_display_name="Synthetic Gate Guest",
            invitation_type="Family",
            table_number="T7",
            allowed_companions=2,
            companions_admitted=companions_admitted,
            admitted_party_size=companions_admitted + 1,
            checked_in_at=NOW,
            scanned_at=NOW,
        )

    def list_event_attendance(self, event_id, *, limit, offset):
        self._raise_if_requested()
        self.calls.append(("list_attendance", (event_id, limit, offset)))
        return [self.attendance_view()]

    def get_guest_attendance(self, guest_id):
        self._raise_if_requested()
        self.calls.append(("get_attendance", guest_id))
        return self.attendance_view()

    def list_event_scan_attempts(self, event_id, *, limit, offset):
        self._raise_if_requested()
        self.calls.append(("list_attempts", (event_id, limit, offset)))
        return [
            GateScanAttemptView(
                id=self.scan_id,
                event_id=event_id,
                guest_id=self.guest_id,
                attendance_id=self.attendance_id,
                outcome=GateScanOutcome.ADMITTED,
                requested_companions=1,
                scanned_at=NOW,
            )
        ]


def make_administrator() -> Administrator:
    return Administrator(
        id=uuid4(),
        username="admin",
        password_hash="$argon2id$synthetic",
        role=AdministratorRole.SUPER_ADMIN,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def build_app(settings, *, authenticated: bool = True):
    administrator = make_administrator()
    service = FakeAttendanceService()
    app = create_app(settings, database_runtime=FakeDatabaseRuntime())  # type: ignore[arg-type]
    app.dependency_overrides[get_attendance_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_administrator] = lambda: administrator
    else:
        app.dependency_overrides[get_authentication_service] = lambda: RejectAuthenticationService()
    return app, service, administrator


async def request(app: object, method: str, path: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        return await client.request(method, path, **kwargs)


def test_scan_route_returns_only_mobile_gate_fields(development_settings) -> None:
    app, service, administrator = build_app(development_settings)
    raw = "A" * 32
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/scan",
            json={
                "event_id": str(service.event_id),
                "credential": raw,
                "companions_admitted": 1,
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "ADMITTED"
    assert body["admitted"] is True
    assert body["guest_display_name"] == "Synthetic Gate Guest"
    assert body["companions_admitted"] == 1
    assert set(body) == {
        "scan_attempt_id",
        "event_id",
        "outcome",
        "admitted",
        "already_checked_in",
        "guest_id",
        "attendance_id",
        "guest_display_name",
        "invitation_type",
        "table_number",
        "allowed_companions",
        "companions_admitted",
        "admitted_party_size",
        "checked_in_at",
        "scanned_at",
    }
    assert service.calls == [("scan", (service.event_id, raw, 1, administrator.id))]
    assert all(
        forbidden not in response.text.casefold()
        for forbidden in (
            raw.casefold(),
            "credential",
            "fingerprint",
            "hash",
            "storage",
            "phone",
            "email",
        )
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event_id": str(uuid4()), "credential": ""},
        {"event_id": str(uuid4()), "credential": "A" * 31},
        {"event_id": str(uuid4()), "credential": "A" * 33},
        {"event_id": str(uuid4()), "credential": "!" * 32},
        {"event_id": str(uuid4()), "credential": "A" * 32, "companions_admitted": -1},
        {"event_id": str(uuid4()), "credential": "A" * 32, "companions_admitted": 3},
        {"event_id": str(uuid4()), "credential": "A" * 32, "unexpected": True},
    ],
)
def test_scan_schema_rejects_malformed_oversized_and_extra_input(
    development_settings,
    payload,
) -> None:
    app, service, _ = build_app(development_settings)
    response = asyncio.run(request(app, "POST", "/api/v1/attendance/scan", json=payload))

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_scan_and_read_routes_require_current_authentication(
    development_settings,
    authorization,
) -> None:
    app, service, _ = build_app(development_settings, authenticated=False)
    headers = {} if authorization is None else {"Authorization": authorization}
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/scan",
            headers=headers,
            json={"event_id": str(service.event_id), "credential": "A" * 32},
        )
    )

    assert response.status_code == 401
    assert service.calls == []


def test_m7_read_routes_are_bounded_and_do_not_expose_internal_evidence(
    development_settings,
) -> None:
    app, service, _ = build_app(development_settings)
    responses = [
        asyncio.run(
            request(
                app,
                "GET",
                f"/api/v1/events/{service.event_id}/attendance?limit=10&offset=2",
            )
        ),
        asyncio.run(request(app, "GET", f"/api/v1/guests/{service.guest_id}/attendance")),
        asyncio.run(
            request(
                app,
                "GET",
                f"/api/v1/events/{service.event_id}/scan-attempts?limit=20&offset=1",
            )
        ),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert responses[0].json()["limit"] == 10
    assert responses[2].json()["limit"] == 20
    assert set(responses[2].json()["items"][0]) == {
        "id",
        "event_id",
        "guest_id",
        "attendance_id",
        "outcome",
        "requested_companions",
        "scanned_at",
    }
    assert all(
        forbidden not in responses[2].text.casefold()
        for forbidden in ("credential", "fingerprint", "hash", "scanned_by")
    )

    too_large = asyncio.run(
        request(
            app,
            "GET",
            f"/api/v1/events/{service.event_id}/scan-attempts?limit=101",
        )
    )
    assert too_large.status_code == 422


@pytest.mark.parametrize(
    ("error", "expected_status", "detail"),
    [
        (AttendanceResourceNotFoundError("event not found"), 404, "event not found"),
        (
            AttendancePersistenceError("postgresql://private"),
            503,
            "attendance service is unavailable",
        ),
    ],
)
def test_domain_errors_are_bounded(development_settings, error, expected_status, detail) -> None:
    app, service, _ = build_app(development_settings)
    service.next_error = error
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/scan",
            json={"event_id": str(service.event_id), "credential": "A" * 32},
        )
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": detail}
    assert "postgresql" not in response.text.casefold()
