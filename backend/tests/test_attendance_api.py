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
from app.models.attendance import (
    GateArrivalMode,
    GateInspectionState,
    GatePermittedAction,
    GateScanOutcome,
)
from app.services.attendance import (
    AttendanceIdempotencyConflictError,
    AttendancePersistenceError,
    AttendanceRecordView,
    AttendanceResourceNotFoundError,
    GateAdmissionResult,
    GateInspectionResult,
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
        self.admit_outcome = GateScanOutcome.ADMITTED

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

    def inspect(self, *, event_id, presented_credential) -> GateInspectionResult:
        self._raise_if_requested()
        self.calls.append(("inspect", (event_id, presented_credential)))
        return GateInspectionResult(
            event_id=event_id,
            inspection_state=GateInspectionState.READY_FOR_PRIMARY,
            permitted_actions=[GatePermittedAction.PRIMARY],
            guest_id=self.guest_id,
            guest_display_name="Synthetic Gate Guest",
            invitation_type="Family",
            table_number="T7",
            primary_attended=False,
            primary_admission_locked=False,
            allowed_companions=2,
            declared_companions=None,
            companions_admitted=None,
            remaining_companion_slots=None,
        )

    def admit(self, command) -> GateAdmissionResult:
        self._raise_if_requested()
        self.calls.append(("admit", command))
        return GateAdmissionResult(
            request_id=command.request_id,
            scan_attempt_id=self.scan_id,
            event_id=command.event_id,
            guest_id=self.guest_id,
            outcome=self.admit_outcome,
            arrival_mode=command.arrival_mode,
            primary_attended=True,
            primary_admitted_this_scan=(
                self.admit_outcome is GateScanOutcome.ADMITTED
                and command.arrival_mode is GateArrivalMode.PRIMARY
            ),
            allowed_companions=2,
            declared_companions=command.declared_companions,
            companions_admitted=command.companions_entering_now,
            companions_admitted_this_scan=(
                command.companions_entering_now
                if self.admit_outcome is GateScanOutcome.ADMITTED
                else 0
            ),
            remaining_companion_slots=(
                None
                if command.declared_companions is None
                else command.declared_companions - command.companions_entering_now
            ),
            people_admitted_this_scan=(
                command.companions_entering_now
                + (1 if command.arrival_mode is GateArrivalMode.PRIMARY else 0)
                if self.admit_outcome is GateScanOutcome.ADMITTED
                else 0
            ),
            attendance_id=self.attendance_id,
            scanned_at=NOW,
            guest_display_name="Synthetic Gate Guest",
            invitation_type="Family",
            table_number="T7",
            checked_in_at=NOW,
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


def test_c24_inspect_route_returns_only_authoritative_gate_fields(development_settings) -> None:
    app, service, _ = build_app(development_settings)
    raw = "A" * 32
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/inspect",
            json={"event_id": str(service.event_id), "credential": raw},
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inspection_state"] == "READY_FOR_PRIMARY"
    assert body["permitted_actions"] == ["PRIMARY"]
    assert body["declared_companions"] is None
    assert body["companions_admitted"] is None
    assert body["remaining_companion_slots"] is None
    assert set(body) == {
        "event_id",
        "inspection_state",
        "permitted_actions",
        "guest_id",
        "guest_display_name",
        "invitation_type",
        "table_number",
        "primary_attended",
        "primary_admission_locked",
        "allowed_companions",
        "declared_companions",
        "companions_admitted",
        "remaining_companion_slots",
    }
    assert service.calls == [("inspect", (service.event_id, raw))]
    assert all(
        forbidden not in response.text.casefold()
        for forbidden in (raw.casefold(), "credential", "phone", "email", "correction")
    )


def test_c24_admit_route_delegates_explicit_command_and_omits_internal_ids(
    development_settings,
) -> None:
    app, service, administrator = build_app(development_settings)
    request_id = uuid4()
    raw = "A" * 32
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/admit",
            json={
                "request_id": str(request_id),
                "event_id": str(service.event_id),
                "credential": raw,
                "arrival_mode": "PRIMARY",
                "declared_companions": 2,
                "companions_entering_now": 1,
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == str(request_id)
    assert body["outcome"] == "ADMITTED"
    assert body["admitted"] is True
    assert body["people_admitted_this_scan"] == 2
    assert "attendance_id" not in body
    assert "checked_in_at" not in body
    _, command = service.calls[0]
    assert command.request_id == request_id
    assert command.administrator_id == administrator.id
    assert command.arrival_mode is GateArrivalMode.PRIMARY
    assert command.declared_companions == 2
    assert command.companions_entering_now == 1
    assert raw.casefold() not in response.text.casefold()


@pytest.mark.parametrize(
    ("allowed", "declared", "entering"),
    [
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (1, 1, 1),
        (2, 0, 0),
        (2, 1, 0),
        (2, 1, 1),
        (2, 2, 0),
        (2, 2, 1),
        (2, 2, 2),
    ],
)
def test_c24_public_primary_request_matrix(
    development_settings,
    allowed,
    declared,
    entering,
) -> None:
    app, service, _ = build_app(development_settings)
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/admit",
            json={
                "request_id": str(uuid4()),
                "event_id": str(service.event_id),
                "credential": "A" * 32,
                "arrival_mode": "PRIMARY",
                "declared_companions": declared,
                "companions_entering_now": entering,
            },
        )
    )
    assert response.status_code == 200
    _, command = service.calls[0]
    assert command.declared_companions == declared
    assert command.companions_entering_now == entering
    assert allowed in {0, 1, 2}


@pytest.mark.parametrize("entering", [1, 2])
def test_c24_public_companion_request_has_no_declaration(development_settings, entering) -> None:
    app, service, _ = build_app(development_settings)
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/admit",
            json={
                "request_id": str(uuid4()),
                "event_id": str(service.event_id),
                "credential": "A" * 32,
                "arrival_mode": "COMPANION",
                "companions_entering_now": entering,
            },
        )
    )
    assert response.status_code == 200
    _, command = service.calls[0]
    assert command.arrival_mode is GateArrivalMode.COMPANION
    assert command.declared_companions is None


@pytest.mark.parametrize(
    "payload_update",
    [
        {},
        {"arrival_mode": "PRIMARY", "declared_companions": None},
        {"arrival_mode": "COMPANION", "declared_companions": 1},
        {
            "arrival_mode": "COMPANION",
            "declared_companions": None,
            "companions_entering_now": 0,
        },
        {"companions_entering_now": -1},
        {"companions_entering_now": 3},
    ],
)
def test_c24_admit_schema_rejects_invalid_conditional_commands(
    development_settings,
    payload_update,
) -> None:
    app, service, _ = build_app(development_settings)
    payload = {
        "request_id": str(uuid4()),
        "event_id": str(service.event_id),
        "credential": "A" * 32,
        "arrival_mode": "PRIMARY",
        "declared_companions": 1,
        "companions_entering_now": 0,
    }
    if payload_update:
        payload.update(payload_update)
    else:
        payload.pop("request_id")
    response = asyncio.run(request(app, "POST", "/api/v1/attendance/admit", json=payload))
    assert response.status_code == 422
    assert service.calls == []


def test_c24_idempotency_conflict_is_safe_machine_readable_409(development_settings) -> None:
    app, service, _ = build_app(development_settings)
    service.next_error = AttendanceIdempotencyConflictError("IDEMPOTENCY_KEY_REUSED")
    raw = "A" * 32
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/admit",
            json={
                "request_id": str(uuid4()),
                "event_id": str(service.event_id),
                "credential": raw,
                "arrival_mode": "COMPANION",
                "companions_entering_now": 1,
            },
        )
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "IDEMPOTENCY_KEY_REUSED"}}
    assert raw.casefold() not in response.text.casefold()


@pytest.mark.parametrize(
    "outcome",
    [
        GateScanOutcome.ADMITTED,
        GateScanOutcome.ALREADY_CHECKED_IN,
        GateScanOutcome.PRIMARY_REQUIRED,
        GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED,
        GateScanOutcome.INSUFFICIENT_COMPANION_SLOTS,
        GateScanOutcome.EVENT_NOT_ACTIVE,
        GateScanOutcome.UNKNOWN_CREDENTIAL,
        GateScanOutcome.WRONG_EVENT,
        GateScanOutcome.INACTIVE_CREDENTIAL,
        GateScanOutcome.SUPERSEDED_CREDENTIAL,
        GateScanOutcome.INELIGIBLE_GUEST,
    ],
)
def test_c24_business_outcomes_are_structured_http_200(development_settings, outcome) -> None:
    app, service, _ = build_app(development_settings)
    service.admit_outcome = outcome
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/attendance/admit",
            json={
                "request_id": str(uuid4()),
                "event_id": str(service.event_id),
                "credential": "A" * 32,
                "arrival_mode": "PRIMARY",
                "declared_companions": 0,
                "companions_entering_now": 0,
            },
        )
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == outcome.value
    assert response.json()["people_admitted_this_scan"] == (
        1 if outcome is GateScanOutcome.ADMITTED else 0
    )


def test_c24_openapi_declares_two_step_contract_and_legacy_deprecation(
    development_settings,
) -> None:
    app, _, _ = build_app(development_settings)
    schema = app.openapi()
    paths = schema["paths"]
    assert "deprecated" not in paths["/api/v1/attendance/inspect"]["post"]
    assert "deprecated" not in paths["/api/v1/attendance/admit"]["post"]
    assert paths["/api/v1/attendance/scan"]["post"]["deprecated"] is True
    components = schema["components"]["schemas"]
    assert set(components["GateInspectionState"]["enum"]) == {
        state.value for state in GateInspectionState
    }
    assert set(components["GateArrivalMode"]["enum"]) == {mode.value for mode in GateArrivalMode}
    assert "request_id" in components["GateAdmitRequest"]["required"]
    assert "declared_companions" not in components["GateAdmitRequest"]["required"]


@pytest.mark.parametrize("path", ["/api/v1/attendance/inspect", "/api/v1/attendance/admit"])
def test_c24_two_step_routes_require_authentication(development_settings, path) -> None:
    app, service, _ = build_app(development_settings, authenticated=False)
    payload = {"event_id": str(service.event_id), "credential": "A" * 32}
    if path.endswith("admit"):
        payload.update(
            request_id=str(uuid4()),
            arrival_mode="PRIMARY",
            declared_companions=0,
            companions_entering_now=0,
        )
    response = asyncio.run(request(app, "POST", path, json=payload))
    assert response.status_code == 401
    assert service.calls == []
