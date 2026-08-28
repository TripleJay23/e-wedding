"""Opt-in PostgreSQL M8 API, integrity, concurrency, query, and timing checks."""

from __future__ import annotations

import asyncio
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from threading import Barrier
from time import perf_counter
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import (
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy import (
    event as sqlalchemy_event,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator
from app.models.attendance import GateScanAttempt, GateScanOutcome, GuestAttendanceRecord
from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceStatus,
    GuestAttendanceCorrection,
)
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import generate_qr_credential, hash_qr_credential
from app.repositories.administrators import AdministratorRepository
from app.repositories.attendance import AttendanceRepository
from app.repositories.attendance_corrections import AttendanceCorrectionRepository
from app.services.admin_provisioning import AdministratorProvisioningService
from app.services.attendance import AttendanceService
from app.services.attendance_corrections import (
    AttendanceCorrectionConflictError,
    AttendanceCorrectionService,
)

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
]


def configure_test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    assert DISPOSABLE_URL is not None
    private_root = tmp_path / "private"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv("JWT_SECRET", "synthetic-jwt-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(private_root))
    monkeypatch.setenv("IMPORT_EVIDENCE_STORAGE_ROOT", str(private_root / "import-evidence"))
    monkeypatch.setenv("QR_IMAGE_STORAGE_ROOT", str(private_root / "qr-codes"))
    return Settings()


def make_event(administrator_id: UUID, *, status: EventStatus = EventStatus.ACTIVE) -> Event:
    return Event(
        id=uuid4(),
        event_name=f"Synthetic M8 Event {uuid4().hex[:8]}",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=datetime(2027, 7, 12, 14, 30, tzinfo=UTC),
        venue="Synthetic Hall",
        status=status,
        max_companions_per_invitation=2,
        created_by=administrator_id,
    )


def add_guest(
    session: Session,
    event: Event,
    administrator_id: UUID,
    *,
    allowed: int = 2,
    status: GuestStatus = GuestStatus.ACTIVE,
    credential: bool = False,
) -> tuple[Guest, str | None]:
    guest = Guest(
        id=uuid4(),
        event_id=event.id,
        full_name=f"Synthetic M8 Guest {uuid4().hex[:8]}",
        phone_number=None,
        email=None,
        invitation_type="Family" if allowed else "Individual",
        allowed_companions=allowed,
        table_number=None,
        category=None,
        status=status,
    )
    session.add(guest)
    if not credential:
        return guest, None
    raw = generate_qr_credential()
    credential_id = uuid4()
    session.add(
        GuestQrCredential(
            id=credential_id,
            event_id=event.id,
            guest_id=guest.id,
            token_hash=hash_qr_credential(raw),
            image_storage_key=f"{event.id.hex}/{guest.id.hex}/{credential_id.hex}.png",
            image_sha256="a" * 64,
            status=QrCredentialStatus.ACTIVE,
            replaces_credential_id=None,
            issued_by=administrator_id,
            issued_at=datetime.now(UTC),
            deactivated_at=None,
            superseded_at=None,
        )
    )
    return guest, raw


def record_once(
    engine,
    *,
    event_id: UUID,
    guest_id: UUID,
    administrator_id: UUID,
    revision: int,
    expected_status: EffectiveAttendanceStatus,
    decision: AttendanceCorrectionDecision,
    companions: int | None = None,
):
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        return AttendanceCorrectionService(
            AttendanceCorrectionRepository(session)
        ).record_correction(
            event_id=event_id,
            guest_id=guest_id,
            expected_correction_revision=revision,
            expected_effective_status=expected_status,
            decision=decision,
            reason_code=AttendanceCorrectionReason.OPERATOR_ERROR,
            reason_note=None,
            companions_admitted=companions,
            administrator_id=administrator_id,
        )


def effective_once(engine, *, event_id: UUID, guest_id: UUID):
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        return AttendanceCorrectionService(
            AttendanceCorrectionRepository(session)
        ).get_effective_state(event_id, guest_id)


def list_effective_once(engine, *, event_id: UUID, limit: int):
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        return AttendanceCorrectionService(
            AttendanceCorrectionRepository(session)
        ).list_effective_states(event_id, limit=limit, offset=0)


def list_history_once(engine, *, event_id: UUID, guest_id: UUID):
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        return AttendanceCorrectionService(
            AttendanceCorrectionRepository(session)
        ).list_corrections(event_id, guest_id, limit=100, offset=0)


def observe_sql(engine, operation) -> int:
    statements: list[str] = []

    def record_statement(*args) -> None:
        statements.append(str(args[2]))

    sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
    try:
        operation()
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", record_statement)
    return len(statements)


def timing_summary(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return median(ordered), ordered[p95_index]


def assert_integrity_failure(session: Session, statement) -> None:
    def execute_and_commit() -> None:
        session.execute(statement)
        session.commit()

    with pytest.raises(IntegrityError):
        execute_and_commit()
    session.rollback()


async def exercise_authenticated_api(
    settings: Settings,
    username: str,
    *,
    event_id: UUID,
    no_gate_guest_id: UUID,
    gate_guest_id: UUID,
    gate_attendance_id: UUID,
    later_guest_id: UUID,
    later_raw: str,
    inactive_guest_id: UUID,
    completed_event_id: UUID,
    completed_guest_id: UUID,
    draft_event_id: UUID,
    draft_guest_id: UUID,
    cancelled_event_id: UUID,
    cancelled_guest_id: UUID,
    other_event_id: UUID,
    other_guest_id: UUID,
    sensitive_note: str,
) -> dict[str, httpx.Response]:
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
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "synthetic administrator password"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        def path(selected_event_id: UUID, guest_id: UUID) -> str:
            return f"/api/v1/events/{selected_event_id}/attendance/guests/{guest_id}/corrections"

        def body(
            revision: int,
            expected_status: str,
            decision: str,
            *,
            companions: int | None = None,
            note: str | None = None,
        ) -> dict[str, object]:
            payload: dict[str, object] = {
                "expected_correction_revision": revision,
                "expected_effective_status": expected_status,
                "decision": decision,
                "reason_code": "OPERATOR_ERROR",
            }
            if companions is not None:
                payload["companions_admitted"] = companions
            if note is not None:
                payload["reason_note"] = note
            return payload

        responses: dict[str, httpx.Response] = {}
        responses["none_initial"] = await client.get(
            f"/api/v1/events/{event_id}/attendance/guests/{no_gate_guest_id}/effective",
            headers=headers,
        )
        responses["gate_initial"] = await client.get(
            f"/api/v1/events/{event_id}/attendance/guests/{gate_guest_id}/effective",
            headers=headers,
        )
        responses["set_attended"] = await client.post(
            path(event_id, no_gate_guest_id),
            headers=headers,
            json=body(0, "NOT_ATTENDED", "SET_ATTENDED", companions=1, note=sensitive_note),
        )
        responses["raw_absent_after_manual"] = await client.get(
            f"/api/v1/guests/{no_gate_guest_id}/attendance",
            headers=headers,
        )
        responses["set_not"] = await client.post(
            path(event_id, no_gate_guest_id),
            headers=headers,
            json=body(1, "ATTENDED", "SET_NOT_ATTENDED"),
        )
        responses["restore_none"] = await client.post(
            path(event_id, no_gate_guest_id),
            headers=headers,
            json=body(2, "NOT_ATTENDED", "RESTORE_GATE_DERIVED"),
        )
        responses["gate_set_not"] = await client.post(
            path(event_id, gate_guest_id),
            headers=headers,
            json=body(0, "ATTENDED", "SET_NOT_ATTENDED"),
        )
        responses["gate_restore"] = await client.post(
            path(event_id, gate_guest_id),
            headers=headers,
            json=body(1, "NOT_ATTENDED", "RESTORE_GATE_DERIVED"),
        )
        responses["stale_revision"] = await client.post(
            path(event_id, no_gate_guest_id),
            headers=headers,
            json=body(2, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
        )
        responses["stale_status"] = await client.post(
            path(event_id, no_gate_guest_id),
            headers=headers,
            json=body(3, "ATTENDED", "SET_ATTENDED", companions=0),
        )
        responses["restore_without_manual"] = await client.post(
            path(event_id, no_gate_guest_id),
            headers=headers,
            json=body(3, "NOT_ATTENDED", "RESTORE_GATE_DERIVED"),
        )
        responses["inactive"] = await client.post(
            path(event_id, inactive_guest_id),
            headers=headers,
            json=body(0, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
        )
        responses["completed"] = await client.post(
            path(completed_event_id, completed_guest_id),
            headers=headers,
            json=body(0, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
        )
        responses["draft"] = await client.post(
            path(draft_event_id, draft_guest_id),
            headers=headers,
            json=body(0, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
        )
        responses["cancelled"] = await client.post(
            path(cancelled_event_id, cancelled_guest_id),
            headers=headers,
            json=body(0, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
        )
        responses["cross_event_guest"] = await client.post(
            path(event_id, other_guest_id),
            headers=headers,
            json=body(0, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
        )

        correction_id = responses["set_attended"].json()["correction"]["id"]
        responses["history"] = await client.get(
            path(event_id, no_gate_guest_id) + "?limit=100&offset=0",
            headers=headers,
        )
        responses["detail"] = await client.get(
            f"/api/v1/events/{event_id}/attendance/corrections/{correction_id}",
            headers=headers,
        )
        responses["audit"] = await client.get(
            f"/api/v1/events/{event_id}/attendance/corrections/{correction_id}/audit-detail",
            headers=headers,
        )
        responses["cross_event_correction"] = await client.get(
            f"/api/v1/events/{other_event_id}/attendance/corrections/{correction_id}",
            headers=headers,
        )

        responses["later_manual"] = await client.post(
            path(event_id, later_guest_id),
            headers=headers,
            json=body(0, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
        )
        responses["later_first_scan"] = await client.post(
            "/api/v1/attendance/scan",
            headers=headers,
            json={
                "event_id": str(event_id),
                "credential": later_raw,
                "companions_admitted": 0,
            },
        )
        responses["later_duplicate_scan"] = await client.post(
            "/api/v1/attendance/scan",
            headers=headers,
            json={
                "event_id": str(event_id),
                "credential": later_raw,
                "companions_admitted": 0,
            },
        )
        responses["later_effective"] = await client.get(
            f"/api/v1/events/{event_id}/attendance/guests/{later_guest_id}/effective",
            headers=headers,
        )
        responses["raw_gate_guest"] = await client.get(
            f"/api/v1/guests/{gate_guest_id}/attendance",
            headers=headers,
        )
        responses["raw_event"] = await client.get(
            f"/api/v1/events/{event_id}/attendance?limit=100&offset=0",
            headers=headers,
        )
        responses["scan_history"] = await client.get(
            f"/api/v1/events/{event_id}/scan-attempts?limit=100&offset=0",
            headers=headers,
        )
        responses["missing_auth"] = await client.get(
            f"/api/v1/events/{event_id}/attendance/effective"
        )
        responses["invalid_auth"] = await client.get(
            f"/api/v1/events/{event_id}/attendance/effective",
            headers={"Authorization": "Bearer invalid-token"},
        )
        responses["strict_422"] = await client.post(
            path(event_id, no_gate_guest_id),
            headers=headers,
            json={
                **body(3, "NOT_ATTENDED", "SET_ATTENDED", companions=0),
                "raw_qr_credential": sensitive_note,
            },
        )
        assert responses["gate_initial"].json()["gate_attendance_record_id"] == str(
            gate_attendance_id
        )
    return responses


def test_m8_postgresql_api_integrity_concurrency_queries_and_timings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = configure_test_environment(monkeypatch, tmp_path)
    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["check"]) == 0

    assert DISPOSABLE_URL is not None
    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    administrator_id: UUID | None = None
    cleanup_required = False
    username = f"m8-admin-{uuid4().hex[:8]}"
    sensitive_note = "Sahihisho nyeti la majaribio"
    try:
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session),
                AuthenticationSecurity.from_settings(settings).passwords,
            ).provision(username, "synthetic administrator password")
            administrator_id = administrator.id
            event = make_event(administrator.id)
            completed_event = make_event(administrator.id, status=EventStatus.COMPLETED)
            draft_event = make_event(administrator.id, status=EventStatus.DRAFT)
            cancelled_event = make_event(administrator.id, status=EventStatus.CANCELLED)
            other_event = make_event(administrator.id)
            small_event = make_event(administrator.id)
            large_event = make_event(administrator.id)
            session.add_all(
                [
                    event,
                    completed_event,
                    draft_event,
                    cancelled_event,
                    other_event,
                    small_event,
                    large_event,
                ]
            )
            session.flush()

            no_gate_guest, _ = add_guest(session, event, administrator.id)
            gate_guest, gate_raw = add_guest(
                session, event, administrator.id, allowed=2, credential=True
            )
            later_guest, later_raw = add_guest(
                session, event, administrator.id, allowed=0, credential=True
            )
            inactive_guest, _ = add_guest(
                session, event, administrator.id, status=GuestStatus.INACTIVE
            )
            completed_guest, _ = add_guest(session, completed_event, administrator.id)
            draft_guest, _ = add_guest(session, draft_event, administrator.id)
            cancelled_guest, _ = add_guest(session, cancelled_event, administrator.id)
            other_guest, _ = add_guest(session, other_event, administrator.id)
            same_guest, _ = add_guest(session, event, administrator.id, allowed=0)
            different_a, _ = add_guest(session, event, administrator.id, allowed=0)
            different_b, _ = add_guest(session, event, administrator.id, allowed=0)
            query_command_guest, _ = add_guest(session, event, administrator.id, allowed=0)
            direct_timestamp_guest, _ = add_guest(session, event, administrator.id, allowed=0)
            direct_actor_guest, _ = add_guest(session, event, administrator.id, allowed=0)
            direct_transition_guest, _ = add_guest(session, event, administrator.id, allowed=0)
            direct_note_guest, _ = add_guest(session, event, administrator.id, allowed=0)
            direct_companion_guest, _ = add_guest(session, event, administrator.id, allowed=0)
            for _ in range(5):
                add_guest(session, small_event, administrator.id, allowed=0)
            for _ in range(120):
                add_guest(session, large_event, administrator.id, allowed=0)
            timing_guests = [
                add_guest(session, event, administrator.id, allowed=0)[0] for _ in range(20)
            ]
            session.commit()
            cleanup_required = True

        assert gate_raw is not None
        assert later_raw is not None
        with Session(engine, expire_on_commit=False, autoflush=False) as session:
            admitted = AttendanceService(AttendanceRepository(session)).scan(
                event_id=event.id,
                presented_credential=gate_raw,
                companions_admitted=2,
                administrator_id=administrator.id,
            )
            assert admitted.outcome is GateScanOutcome.ADMITTED
            assert admitted.attendance_id is not None
            gate_attendance_id = admitted.attendance_id

        with Session(engine) as session:
            gate_before = session.get(GuestAttendanceRecord, gate_attendance_id)
            assert gate_before is not None
            gate_evidence_before = (
                gate_before.id,
                gate_before.event_id,
                gate_before.guest_id,
                gate_before.first_qr_credential_id,
                gate_before.companions_admitted,
                gate_before.checked_in_by,
                gate_before.checked_in_at,
            )
            attempts_before = list(
                session.execute(
                    select(GateScanAttempt)
                    .where(GateScanAttempt.guest_id == gate_guest.id)
                    .order_by(GateScanAttempt.scanned_at, GateScanAttempt.id)
                ).scalars()
            )
            attempt_evidence_before = [
                (item.id, item.outcome, item.requested_companions, item.scanned_at)
                for item in attempts_before
            ]

        responses = asyncio.run(
            exercise_authenticated_api(
                settings,
                username,
                event_id=event.id,
                no_gate_guest_id=no_gate_guest.id,
                gate_guest_id=gate_guest.id,
                gate_attendance_id=gate_attendance_id,
                later_guest_id=later_guest.id,
                later_raw=later_raw,
                inactive_guest_id=inactive_guest.id,
                completed_event_id=completed_event.id,
                completed_guest_id=completed_guest.id,
                draft_event_id=draft_event.id,
                draft_guest_id=draft_guest.id,
                cancelled_event_id=cancelled_event.id,
                cancelled_guest_id=cancelled_guest.id,
                other_event_id=other_event.id,
                other_guest_id=other_guest.id,
                sensitive_note=sensitive_note,
            )
        )

        assert responses["none_initial"].status_code == 200
        assert {
            "effective_status": "NOT_ATTENDED",
            "effective_source": "NONE",
            "correction_revision": 0,
        }.items() <= responses["none_initial"].json().items()
        assert responses["gate_initial"].json()["effective_source"] == "GATE"
        assert responses["gate_initial"].json()["effective_companions_admitted"] == 2
        assert responses["set_attended"].status_code == 201
        assert responses["set_attended"].json()["effective_state"]["effective_party_size"] == 2
        assert responses["raw_absent_after_manual"].status_code == 404
        assert responses["set_not"].status_code == 201
        assert responses["restore_none"].status_code == 201
        assert responses["restore_none"].json()["effective_state"]["effective_source"] == "NONE"
        assert responses["gate_set_not"].status_code == 201
        assert responses["gate_restore"].status_code == 201
        assert {
            "effective_status": "ATTENDED",
            "effective_source": "GATE",
            "effective_companions_admitted": 2,
        }.items() <= responses["gate_restore"].json()["effective_state"].items()
        assert all(
            responses[name].status_code == 409
            for name in (
                "stale_revision",
                "stale_status",
                "restore_without_manual",
                "inactive",
                "draft",
                "cancelled",
            )
        )
        assert responses["completed"].status_code == 201
        assert responses["cross_event_guest"].status_code == 404
        assert responses["cross_event_correction"].status_code == 404
        assert responses["history"].status_code == 200
        assert [item["revision_number"] for item in responses["history"].json()["items"]] == [
            1,
            2,
            3,
        ]
        assert responses["detail"].status_code == 200
        assert sensitive_note not in responses["detail"].text
        assert sensitive_note not in responses["history"].text
        assert responses["audit"].json()["reason_note"] == sensitive_note
        assert responses["later_manual"].status_code == 201
        assert responses["later_first_scan"].json()["outcome"] == "ADMITTED"
        assert responses["later_duplicate_scan"].json()["outcome"] == "ALREADY_CHECKED_IN"
        assert responses["later_effective"].json()["effective_source"] == "MANUAL"
        assert responses["raw_gate_guest"].json()["companions_admitted"] == 2
        assert responses["raw_event"].status_code == 200
        assert responses["scan_history"].status_code == 200
        assert responses["missing_auth"].status_code == 401
        assert responses["invalid_auth"].status_code == 401
        assert responses["strict_422"].status_code == 422
        assert sensitive_note not in responses["strict_422"].text
        assert sensitive_note not in caplog.text

        with Session(engine) as session:
            gate_after = session.get(GuestAttendanceRecord, gate_attendance_id)
            assert gate_after is not None
            assert (
                gate_after.id,
                gate_after.event_id,
                gate_after.guest_id,
                gate_after.first_qr_credential_id,
                gate_after.companions_admitted,
                gate_after.checked_in_by,
                gate_after.checked_in_at,
            ) == gate_evidence_before
            attempts_after = list(
                session.execute(
                    select(GateScanAttempt)
                    .where(GateScanAttempt.guest_id == gate_guest.id)
                    .order_by(GateScanAttempt.scanned_at, GateScanAttempt.id)
                ).scalars()
            )
            assert [
                (item.id, item.outcome, item.requested_companions, item.scanned_at)
                for item in attempts_after
            ] == attempt_evidence_before
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceCorrection)
                    .where(GuestAttendanceCorrection.guest_id == no_gate_guest.id)
                )
                == 3
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceRecord)
                    .where(GuestAttendanceRecord.guest_id == later_guest.id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GateScanAttempt)
                    .where(GateScanAttempt.guest_id == later_guest.id)
                )
                == 2
            )

        same_barrier = Barrier(2)

        def concurrent_same() -> str:
            same_barrier.wait(timeout=10)
            try:
                record_once(
                    engine,
                    event_id=event.id,
                    guest_id=same_guest.id,
                    administrator_id=administrator.id,
                    revision=0,
                    expected_status=EffectiveAttendanceStatus.NOT_ATTENDED,
                    decision=AttendanceCorrectionDecision.SET_ATTENDED,
                    companions=0,
                )
            except AttendanceCorrectionConflictError:
                return "CONFLICT"
            return "CREATED"

        with ThreadPoolExecutor(max_workers=2) as executor:
            same_outcomes = list(executor.map(lambda _: concurrent_same(), range(2)))
        assert sorted(same_outcomes) == ["CONFLICT", "CREATED"]

        different_barrier = Barrier(2)

        def concurrent_different(guest_id: UUID) -> str:
            different_barrier.wait(timeout=10)
            record_once(
                engine,
                event_id=event.id,
                guest_id=guest_id,
                administrator_id=administrator.id,
                revision=0,
                expected_status=EffectiveAttendanceStatus.NOT_ATTENDED,
                decision=AttendanceCorrectionDecision.SET_ATTENDED,
                companions=0,
            )
            return "CREATED"

        with ThreadPoolExecutor(max_workers=2) as executor:
            different_outcomes = list(
                executor.map(concurrent_different, [different_a.id, different_b.id])
            )
        assert different_outcomes == ["CREATED", "CREATED"]

        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceCorrection)
                    .where(GuestAttendanceCorrection.guest_id == same_guest.id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceCorrection)
                    .where(GuestAttendanceCorrection.guest_id.in_([different_a.id, different_b.id]))
                )
                == 2
            )

        query_counts = {
            "single_effective": observe_sql(
                engine,
                lambda: effective_once(
                    engine,
                    event_id=event.id,
                    guest_id=no_gate_guest.id,
                ),
            ),
            "small_event_list": observe_sql(
                engine,
                lambda: list_effective_once(engine, event_id=small_event.id, limit=100),
            ),
            "large_event_list": observe_sql(
                engine,
                lambda: list_effective_once(engine, event_id=large_event.id, limit=100),
            ),
            "history": observe_sql(
                engine,
                lambda: list_history_once(
                    engine,
                    event_id=event.id,
                    guest_id=no_gate_guest.id,
                ),
            ),
            "command": observe_sql(
                engine,
                lambda: record_once(
                    engine,
                    event_id=event.id,
                    guest_id=query_command_guest.id,
                    administrator_id=administrator.id,
                    revision=0,
                    expected_status=EffectiveAttendanceStatus.NOT_ATTENDED,
                    decision=AttendanceCorrectionDecision.SET_ATTENDED,
                    companions=0,
                ),
            ),
        }
        assert query_counts["single_effective"] == 1
        assert query_counts["small_event_list"] == query_counts["large_event_list"] == 2
        assert query_counts["history"] == 2
        assert query_counts["command"] <= 4
        assert len(list_effective_once(engine, event_id=large_event.id, limit=100)) == 100

        old_time = datetime(2000, 1, 1, tzinfo=UTC)
        first_direct = record_once(
            engine,
            event_id=event.id,
            guest_id=direct_timestamp_guest.id,
            administrator_id=administrator.id,
            revision=0,
            expected_status=EffectiveAttendanceStatus.NOT_ATTENDED,
            decision=AttendanceCorrectionDecision.SET_ATTENDED,
            companions=0,
        )
        table = GuestAttendanceCorrection.__table__
        with Session(engine) as session:
            second_id = uuid4()
            session.execute(
                insert(table).values(
                    id=second_id,
                    event_id=event.id,
                    guest_id=direct_timestamp_guest.id,
                    revision_number=2,
                    previous_correction_id=first_direct.correction.id,
                    decision="SET_NOT_ATTENDED",
                    reason_code="OPERATOR_ERROR",
                    reason_note=None,
                    companions_admitted=None,
                    gate_attendance_record_id=None,
                    previous_effective_status="ATTENDED",
                    resulting_effective_status="NOT_ATTENDED",
                    resulting_effective_source="MANUAL",
                    corrected_by_user_id=administrator.id,
                    created_at=old_time,
                )
            )
            session.commit()
            server_timed = session.get(GuestAttendanceCorrection, second_id)
            assert server_timed is not None
            assert server_timed.created_at != old_time

            base_values = {
                "id": uuid4(),
                "event_id": event.id,
                "guest_id": direct_actor_guest.id,
                "revision_number": 1,
                "previous_correction_id": None,
                "decision": "SET_ATTENDED",
                "reason_code": "OPERATOR_ERROR",
                "reason_note": None,
                "companions_admitted": 0,
                "gate_attendance_record_id": None,
                "previous_effective_status": "NOT_ATTENDED",
                "resulting_effective_status": "ATTENDED",
                "resulting_effective_source": "MANUAL",
                "corrected_by_user_id": uuid4(),
            }
            assert_integrity_failure(session, insert(table).values(**base_values))
            assert_integrity_failure(
                session,
                insert(table).values(
                    **{
                        **base_values,
                        "id": uuid4(),
                        "event_id": other_event.id,
                        "corrected_by_user_id": administrator.id,
                    }
                ),
            )
            assert_integrity_failure(
                session,
                insert(table).values(
                    **{
                        **base_values,
                        "id": uuid4(),
                        "guest_id": direct_transition_guest.id,
                        "revision_number": 2,
                        "previous_correction_id": first_direct.correction.id,
                        "corrected_by_user_id": administrator.id,
                    }
                ),
            )
            assert_integrity_failure(
                session,
                insert(table).values(
                    **{
                        **base_values,
                        "id": uuid4(),
                        "guest_id": direct_note_guest.id,
                        "reason_code": "OTHER",
                        "reason_note": "unsafe\ncontrol",
                        "corrected_by_user_id": administrator.id,
                    }
                ),
            )
            assert_integrity_failure(
                session,
                insert(table).values(
                    **{
                        **base_values,
                        "id": uuid4(),
                        "guest_id": direct_companion_guest.id,
                        "companions_admitted": 1,
                        "corrected_by_user_id": administrator.id,
                    }
                ),
            )
            assert_integrity_failure(
                session,
                update(GuestAttendanceCorrection)
                .where(GuestAttendanceCorrection.id == second_id)
                .values(reason_code="POST_EVENT_RECONCILIATION"),
            )
            assert_integrity_failure(
                session,
                delete(GuestAttendanceCorrection).where(GuestAttendanceCorrection.id == second_id),
            )

        observations: dict[str, tuple[float, float]] = {}
        durations: list[float] = []
        for _ in range(20):
            started = perf_counter()
            effective_once(engine, event_id=event.id, guest_id=no_gate_guest.id)
            durations.append((perf_counter() - started) * 1_000)
        observations["single_effective"] = timing_summary(durations)

        durations = []
        for guest in timing_guests:
            started = perf_counter()
            record_once(
                engine,
                event_id=event.id,
                guest_id=guest.id,
                administrator_id=administrator.id,
                revision=0,
                expected_status=EffectiveAttendanceStatus.NOT_ATTENDED,
                decision=AttendanceCorrectionDecision.SET_ATTENDED,
                companions=0,
            )
            durations.append((perf_counter() - started) * 1_000)
        observations["command"] = timing_summary(durations)

        durations = []
        for _ in range(20):
            started = perf_counter()
            list_history_once(engine, event_id=event.id, guest_id=no_gate_guest.id)
            durations.append((perf_counter() - started) * 1_000)
        observations["history"] = timing_summary(durations)

        durations = []
        for _ in range(20):
            started = perf_counter()
            list_effective_once(engine, event_id=large_event.id, limit=100)
            durations.append((perf_counter() - started) * 1_000)
        observations["event_list"] = timing_summary(durations)

        print(
            "M8_QUERY_OBSERVATION "
            + " ".join(f"{case}_sql={count}" for case, count in query_counts.items())
        )
        print(
            "M8_TIMING_OBSERVATION "
            + " ".join(
                f"{case}_median_ms={values[0]:.3f} {case}_p95_ms={values[1]:.3f}"
                for case, values in observations.items()
            )
        )
    finally:
        engine.dispose()
        if cleanup_required:
            assert disposable_alembic.main(["downgrade", "20260820_0006"]) == 0
            cleanup_engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
            try:
                if administrator_id is not None:
                    with Session(cleanup_engine) as session:
                        session.execute(delete(Event).where(Event.created_by == administrator_id))
                        administrator = session.get(Administrator, administrator_id)
                        if administrator is not None:
                            session.delete(administrator)
                        session.commit()
            finally:
                cleanup_engine.dispose()
            assert disposable_alembic.main(["upgrade", "head"]) == 0
