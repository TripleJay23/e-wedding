"""Authenticated M8 API contracts, route ordering, and privacy tests."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import (
    get_attendance_correction_service,
    get_authentication_service,
    get_current_administrator,
)
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
)
from app.repositories.attendance_corrections import CorrectionAuditRecord, CorrectionRecord
from app.services.attendance_corrections import (
    AttendanceCorrectionConflictError,
    AttendanceCorrectionPersistenceError,
    AttendanceCorrectionResourceNotFoundError,
    AttendanceCorrectionResult,
    EffectiveAttendanceState,
)
from app.services.authentication import AuthenticationRejectedError

NOW = datetime(2026, 8, 21, 12, 30, tzinfo=UTC)
SENSITIVE_NOTE = "Sensitive reconciliation detail"


class FakeDatabaseRuntime:
    def close(self) -> None:
        pass


class RejectAuthenticationService:
    def current_administrator(self, token: str) -> Administrator:
        raise AuthenticationRejectedError("synthetic rejection")


class FakeCorrectionService:
    def __init__(self) -> None:
        self.event_id = uuid4()
        self.guest_id = uuid4()
        self.correction_id = uuid4()
        self.actor_id = uuid4()
        self.calls: list[tuple[str, Any]] = []
        self.next_error: Exception | None = None

    def _raise_if_requested(self) -> None:
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error

    def state(self) -> EffectiveAttendanceState:
        return EffectiveAttendanceState(
            event_id=self.event_id,
            guest_id=self.guest_id,
            effective_status=EffectiveAttendanceStatus.ATTENDED,
            effective_source=EffectiveAttendanceSource.MANUAL,
            effective_companions_admitted=1,
            effective_party_size=2,
            gate_attendance_record_id=None,
            current_correction_id=self.correction_id,
            correction_revision=1,
            effective_at=NOW,
            allowed_companions=2,
            declared_companions=2,
            companions_admitted=1,
            remaining_companion_slots=1,
        )

    def correction(self) -> CorrectionRecord:
        return CorrectionRecord(
            id=self.correction_id,
            event_id=self.event_id,
            guest_id=self.guest_id,
            revision_number=1,
            previous_correction_id=None,
            decision=AttendanceCorrectionDecision.SET_ATTENDED,
            reason_code=AttendanceCorrectionReason.MISSED_GATE_SCAN,
            companions_admitted=1,
            gate_attendance_record_id=None,
            previous_effective_status=EffectiveAttendanceStatus.NOT_ATTENDED,
            resulting_effective_status=EffectiveAttendanceStatus.ATTENDED,
            resulting_effective_source=EffectiveAttendanceSource.MANUAL,
            created_at=NOW,
        )

    def record_correction(self, **kwargs) -> AttendanceCorrectionResult:
        self._raise_if_requested()
        self.calls.append(("record", kwargs))
        return AttendanceCorrectionResult(self.correction(), self.state())

    def get_effective_state(self, event_id, guest_id):
        self._raise_if_requested()
        self.calls.append(("get_effective", (event_id, guest_id)))
        return self.state()

    def list_effective_states(self, event_id, *, limit, offset):
        self._raise_if_requested()
        self.calls.append(("list_effective", (event_id, limit, offset)))
        return [self.state()]

    def list_corrections(self, event_id, guest_id, *, limit, offset):
        self._raise_if_requested()
        self.calls.append(("list_corrections", (event_id, guest_id, limit, offset)))
        return [self.correction()]

    def get_correction(self, event_id, correction_id):
        self._raise_if_requested()
        self.calls.append(("get_correction", (event_id, correction_id)))
        return self.correction()

    def get_correction_audit_detail(self, event_id, correction_id):
        self._raise_if_requested()
        self.calls.append(("get_audit", (event_id, correction_id)))
        safe = self.correction()
        return CorrectionAuditRecord(
            id=safe.id,
            event_id=safe.event_id,
            guest_id=safe.guest_id,
            revision_number=safe.revision_number,
            previous_correction_id=safe.previous_correction_id,
            decision=safe.decision,
            reason_code=safe.reason_code,
            companions_admitted=safe.companions_admitted,
            gate_attendance_record_id=safe.gate_attendance_record_id,
            previous_effective_status=safe.previous_effective_status,
            resulting_effective_status=safe.resulting_effective_status,
            resulting_effective_source=safe.resulting_effective_source,
            created_at=safe.created_at,
            reason_note=SENSITIVE_NOTE,
        )


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
    service = FakeCorrectionService()
    app = create_app(settings, database_runtime=FakeDatabaseRuntime())  # type: ignore[arg-type]
    app.dependency_overrides[get_attendance_correction_service] = lambda: service
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


def valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "expected_correction_revision": 0,
        "expected_effective_status": "NOT_ATTENDED",
        "decision": "SET_ATTENDED",
        "reason_code": "MISSED_GATE_SCAN",
        "reason_note": SENSITIVE_NOTE,
        "companions_admitted": 1,
    }
    payload.update(overrides)
    return payload


def test_record_route_is_strict_private_and_returns_no_sensitive_note(development_settings) -> None:
    app, service, administrator = build_app(development_settings)
    response = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{service.event_id}/attendance/guests/{service.guest_id}/corrections",
            json=valid_payload(),
        )
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["effective_state"]["effective_status"] == "ATTENDED"
    assert body["effective_state"]["effective_companions_admitted"] == 1
    assert SENSITIVE_NOTE not in response.text
    assert "reason_note" not in body["correction"]
    assert "corrected_by_user_id" not in body["correction"]
    call = service.calls[0][1]
    assert call["administrator_id"] == administrator.id
    assert call["reason_note"] == SENSITIVE_NOTE


def test_set_companion_state_accepts_only_desired_state_and_returns_current_state(
    development_settings,
) -> None:
    app, service, _ = build_app(development_settings)
    response = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{service.event_id}/attendance/guests/{service.guest_id}/corrections",
            json={
                "expected_correction_revision": 0,
                "decision": "SET_COMPANION_STATE",
                "reason_code": "OPERATOR_ERROR",
                "declared_companions": 2,
                "companions_admitted": 1,
            },
        )
    )

    assert response.status_code == 201
    state = response.json()["effective_state"]
    assert {
        "primary_attended": True,
        "allowed_companions": 2,
        "declared_companions": 2,
        "companions_admitted": 1,
        "remaining_companion_slots": 1,
    }.items() <= state.items()
    call = service.calls[0][1]
    assert call["expected_effective_status"] is None
    assert call["declared_companions"] == 2
    assert call["companions_admitted"] == 1
    assert "previous_declared_companions" not in call
    assert "previous_companions_admitted" not in call

    request_schema = app.openapi()["components"]["schemas"]["AttendanceCorrectionRequest"]
    properties = request_schema["properties"]
    assert (
        "optional for SET_COMPANION_STATE" in properties["expected_effective_status"]["description"]
    )
    assert "Required for SET_COMPANION_STATE" in properties["declared_companions"]["description"]
    assert (
        "SET_ATTENDED and SET_COMPANION_STATE" in properties["companions_admitted"]["description"]
    )


def test_all_read_routes_are_bounded_and_only_audit_detail_returns_note(
    development_settings,
) -> None:
    app, service, _ = build_app(development_settings)
    paths = {
        "event": f"/api/v1/events/{service.event_id}/attendance/effective?limit=10&offset=2",
        "guest": (
            f"/api/v1/events/{service.event_id}/attendance/guests/{service.guest_id}/effective"
        ),
        "history": (
            f"/api/v1/events/{service.event_id}/attendance/guests/"
            f"{service.guest_id}/corrections?limit=20&offset=1"
        ),
        "detail": (
            f"/api/v1/events/{service.event_id}/attendance/corrections/{service.correction_id}"
        ),
        "audit": (
            f"/api/v1/events/{service.event_id}/attendance/corrections/"
            f"{service.correction_id}/audit-detail"
        ),
    }
    responses = {name: asyncio.run(request(app, "GET", path)) for name, path in paths.items()}

    assert all(response.status_code == 200 for response in responses.values())
    assert responses["event"].json()["limit"] == 10
    assert responses["history"].json()["limit"] == 20
    assert SENSITIVE_NOTE in responses["audit"].text
    assert all(
        SENSITIVE_NOTE not in responses[name].text
        for name in ("event", "guest", "history", "detail")
    )
    assert all(
        forbidden not in response.text.casefold()
        for name, response in responses.items()
        if name != "audit"
        for forbidden in ("reason_note", "corrected_by", "phone", "email", "credential", "hash")
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        valid_payload(expected_correction_revision=False),
        valid_payload(expected_correction_revision="0"),
        valid_payload(expected_correction_revision=-1),
        valid_payload(expected_effective_status="UNKNOWN"),
        valid_payload(decision="UNKNOWN"),
        valid_payload(reason_code="UNBOUNDED"),
        valid_payload(companions_admitted=3),
        valid_payload(decision="SET_ATTENDED", companions_admitted=None),
        valid_payload(decision="SET_ATTENDED", declared_companions=1),
        valid_payload(decision="SET_NOT_ATTENDED", companions_admitted=0),
        {
            "expected_correction_revision": 0,
            "decision": "SET_NOT_ATTENDED",
            "reason_code": "OPERATOR_ERROR",
        },
        {
            "expected_correction_revision": 0,
            "decision": "SET_COMPANION_STATE",
            "reason_code": "OPERATOR_ERROR",
            "companions_admitted": 0,
        },
        {
            "expected_correction_revision": 0,
            "decision": "SET_COMPANION_STATE",
            "reason_code": "OPERATOR_ERROR",
            "declared_companions": 2,
        },
        {
            "expected_correction_revision": 0,
            "decision": "SET_COMPANION_STATE",
            "reason_code": "OPERATOR_ERROR",
            "declared_companions": 0,
            "companions_admitted": 1,
        },
        valid_payload(
            decision="RESTORE_GATE_DERIVED",
            companions_admitted=None,
            declared_companions=0,
        ),
        valid_payload(reason_code="OTHER", reason_note=None),
        valid_payload(reason_note="x" * 2001),
        valid_payload(resulting_effective_status="ATTENDED"),
        valid_payload(corrected_by_user_id=str(uuid4())),
        valid_payload(raw_qr_credential="A" * 32),
    ],
)
def test_request_validation_rejects_stale_shape_and_caller_controlled_fields(
    development_settings,
    payload,
) -> None:
    app, service, _ = build_app(development_settings)
    response = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{service.event_id}/attendance/guests/{service.guest_id}/corrections",
            json=payload,
        )
    )
    assert response.status_code == 422
    assert service.calls == []
    assert "raw_qr_credential" not in response.text
    assert "A" * 32 not in response.text
    assert "x" * 100 not in response.text


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_all_correction_operations_require_current_persisted_admin(
    development_settings,
    authorization,
) -> None:
    app, service, _ = build_app(development_settings, authenticated=False)
    headers = {} if authorization is None else {"Authorization": authorization}
    response = asyncio.run(
        request(
            app,
            "GET",
            f"/api/v1/events/{service.event_id}/attendance/effective",
            headers=headers,
        )
    )
    assert response.status_code == 401
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "expected_status", "detail"),
    [
        (
            AttendanceCorrectionResourceNotFoundError("attendance correction resource not found"),
            404,
            "attendance correction resource not found",
        ),
        (
            AttendanceCorrectionConflictError("attendance correction conflicts with current state"),
            409,
            "attendance correction conflicts with current state",
        ),
        (
            AttendanceCorrectionPersistenceError("postgresql://private"),
            503,
            "attendance correction service is unavailable",
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
            f"/api/v1/events/{service.event_id}/attendance/guests/{service.guest_id}/corrections",
            json=valid_payload(),
        )
    )
    assert response.status_code == expected_status
    assert response.json() == {"detail": detail}
    assert "postgresql" not in response.text.casefold()


def test_static_effective_and_audit_paths_cannot_be_swallowed_by_uuid_routes(
    development_settings,
) -> None:
    app, service, _ = build_app(development_settings)
    effective = asyncio.run(
        request(
            app,
            "GET",
            f"/api/v1/events/{service.event_id}/attendance/effective",
        )
    )
    malformed = asyncio.run(
        request(
            app,
            "GET",
            f"/api/v1/events/{service.event_id}/attendance/corrections/not-a-uuid/audit-detail",
        )
    )
    assert effective.status_code == 200
    assert malformed.status_code == 422
    assert service.calls == [("list_effective", (service.event_id, 50, 0))]
