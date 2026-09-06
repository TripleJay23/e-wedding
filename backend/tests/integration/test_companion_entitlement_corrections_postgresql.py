"""Opt-in PostgreSQL C2.5 companion-correction integration acceptance."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.attendance import (
    GateArrivalMode,
    GateInspectionState,
    GateScanAttempt,
    GateScanOutcome,
    GuestAttendanceRecord,
    GuestCompanionEntitlement,
)
from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
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
from app.services.attendance import AttendanceService, GateAdmissionCommand
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
    monkeypatch.setenv("IMPORT_EVIDENCE_STORAGE_ROOT", str(private_root / "imports"))
    monkeypatch.setenv("QR_IMAGE_STORAGE_ROOT", str(private_root / "qr"))
    return Settings()


def make_event(administrator_id: UUID) -> Event:
    return Event(
        id=uuid4(),
        event_name=f"Synthetic C2.5 Event {uuid4().hex[:8]}",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=datetime(2027, 9, 12, 14, 30, tzinfo=UTC),
        venue="Synthetic Hall",
        status=EventStatus.ACTIVE,
        max_companions_per_invitation=2,
        created_by=administrator_id,
    )


def add_guest_credential(
    session: Session,
    event: Event,
    administrator_id: UUID,
) -> tuple[Guest, str]:
    guest = Guest(
        id=uuid4(),
        event_id=event.id,
        full_name=f"Synthetic C2.5 Guest {uuid4().hex[:8]}",
        phone_number=None,
        email=None,
        invitation_type="Family",
        allowed_companions=2,
        table_number=None,
        category=None,
        status=GuestStatus.ACTIVE,
    )
    raw = generate_qr_credential()
    credential_id = uuid4()
    session.add_all(
        [
            guest,
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
            ),
        ]
    )
    return guest, raw


def correction_once(
    engine,
    *,
    event_id: UUID,
    guest_id: UUID,
    administrator_id: UUID,
    revision: int,
    decision: AttendanceCorrectionDecision,
    expected_status: EffectiveAttendanceStatus | None = None,
    declared: int | None = None,
    admitted: int | None = None,
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
            declared_companions=declared,
            companions_admitted=admitted,
            administrator_id=administrator_id,
        )


def admit_once(
    engine,
    *,
    event_id: UUID,
    raw: str,
    administrator_id: UUID,
    mode: GateArrivalMode,
    entering: int,
    declared: int | None = None,
):
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        return AttendanceService(AttendanceRepository(session)).admit(
            GateAdmissionCommand(
                request_id=uuid4(),
                event_id=event_id,
                presented_credential=raw,
                arrival_mode=mode,
                declared_companions=declared,
                companions_entering_now=entering,
                administrator_id=administrator_id,
            )
        )


def inspect_once(engine, *, event_id: UUID, raw: str):
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        return AttendanceService(AttendanceRepository(session)).inspect(
            event_id=event_id,
            presented_credential=raw,
        )


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


def execute_and_commit(session: Session, statement) -> None:
    session.execute(statement)
    session.commit()


async def exercise_correction_and_scanner_api(
    settings: Settings,
    *,
    username: str,
    event_id: UUID,
    guest_id: UUID,
    raw: str,
) -> dict[str, dict]:
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
        correction_path = f"/api/v1/events/{event_id}/attendance/guests/{guest_id}/corrections"

        established = await client.post(
            correction_path,
            headers=headers,
            json={
                "expected_correction_revision": 0,
                "expected_effective_status": "NOT_ATTENDED",
                "decision": "SET_ATTENDED",
                "reason_code": "MISSED_GATE_SCAN",
                "companions_admitted": 0,
            },
        )
        assert established.status_code == 201
        corrected = await client.post(
            correction_path,
            headers=headers,
            json={
                "expected_correction_revision": 1,
                "decision": "SET_COMPANION_STATE",
                "reason_code": "OPERATOR_ERROR",
                "declared_companions": 2,
                "companions_admitted": 0,
            },
        )
        assert corrected.status_code == 201
        inspected = await client.post(
            "/api/v1/attendance/inspect",
            headers=headers,
            json={"event_id": str(event_id), "credential": raw},
        )
        admitted = await client.post(
            "/api/v1/attendance/admit",
            headers=headers,
            json={
                "request_id": str(uuid4()),
                "event_id": str(event_id),
                "credential": raw,
                "arrival_mode": "COMPANION",
                "companions_entering_now": 1,
            },
        )
        after = await client.post(
            "/api/v1/attendance/inspect",
            headers=headers,
            json={"event_id": str(event_id), "credential": raw},
        )
        effective_after = await client.get(
            f"/api/v1/events/{event_id}/attendance/guests/{guest_id}/effective",
            headers=headers,
        )
        assert (
            inspected.status_code
            == admitted.status_code
            == after.status_code
            == effective_after.status_code
            == 200
        )
        return {
            "established": established.json(),
            "corrected": corrected.json(),
            "inspected": inspected.json(),
            "admitted": admitted.json(),
            "after": after.json(),
            "effective_after": effective_after.json(),
        }


def test_c25_postgresql_corrections_evidence_scanner_and_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = configure_test_environment(monkeypatch, tmp_path)
    assert DISPOSABLE_URL is not None
    assert disposable_alembic.main(["downgrade", "base"]) == 0
    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["check"]) == 0

    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    try:
        username = f"c25-admin-{uuid4().hex[:8]}"
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session),
                AuthenticationSecurity.from_settings(settings).passwords,
            ).provision(username, "synthetic administrator password")
            event = make_event(administrator.id)
            session.add(event)
            session.flush()
            api_guest, api_raw = add_guest_credential(session, event, administrator.id)
            gate_guest, gate_raw = add_guest_credential(session, event, administrator.id)
            bounded_guest, bounded_raw = add_guest_credential(session, event, administrator.id)
            missing_guest, _ = add_guest_credential(session, event, administrator.id)
            race_guest, race_raw = add_guest_credential(session, event, administrator.id)
            session.commit()

        api = asyncio.run(
            exercise_correction_and_scanner_api(
                settings,
                username=username,
                event_id=event.id,
                guest_id=api_guest.id,
                raw=api_raw,
            )
        )
        assert {
            "previous_declared_companions": 0,
            "previous_companions_admitted": 0,
            "resulting_declared_companions": 2,
            "resulting_companions_admitted": 0,
        }.items() <= api["corrected"]["correction"].items()
        assert {
            "correction_revision": 2,
            "primary_attended": True,
            "allowed_companions": 2,
            "declared_companions": 2,
            "companions_admitted": 0,
            "remaining_companion_slots": 2,
        }.items() <= api["corrected"]["effective_state"].items()
        assert api["inspected"]["inspection_state"] == "READY_FOR_COMPANION"
        assert api["inspected"]["remaining_companion_slots"] == 2
        assert api["admitted"]["outcome"] == "ADMITTED"
        assert api["after"]["remaining_companion_slots"] == 1
        assert api["effective_after"]["companions_admitted"] == 1
        assert api["effective_after"]["effective_companions_admitted"] == 1

        reduced = correction_once(
            engine,
            event_id=event.id,
            guest_id=api_guest.id,
            administrator_id=administrator.id,
            revision=2,
            decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
            declared=2,
            admitted=0,
        )
        assert reduced.correction.previous_declared_companions == 2
        assert reduced.correction.previous_companions_admitted == 1
        restored = correction_once(
            engine,
            event_id=event.id,
            guest_id=api_guest.id,
            administrator_id=administrator.id,
            revision=3,
            decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
            declared=2,
            admitted=1,
        )
        assert restored.effective_state.companions_admitted == 1
        with pytest.raises(AttendanceCorrectionConflictError):
            correction_once(
                engine,
                event_id=event.id,
                guest_id=api_guest.id,
                administrator_id=administrator.id,
                revision=4,
                decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
                declared=2,
                admitted=1,
            )

        suppressed = correction_once(
            engine,
            event_id=event.id,
            guest_id=api_guest.id,
            administrator_id=administrator.id,
            revision=4,
            expected_status=EffectiveAttendanceStatus.ATTENDED,
            decision=AttendanceCorrectionDecision.SET_NOT_ATTENDED,
        )
        assert suppressed.effective_state.primary_attended is False
        assert (
            inspect_once(engine, event_id=event.id, raw=api_raw).inspection_state
            is GateInspectionState.ADMISSION_LOCKED
        )
        restored_none = correction_once(
            engine,
            event_id=event.id,
            guest_id=api_guest.id,
            administrator_id=administrator.id,
            revision=5,
            expected_status=EffectiveAttendanceStatus.NOT_ATTENDED,
            decision=AttendanceCorrectionDecision.RESTORE_GATE_DERIVED,
        )
        assert restored_none.effective_state.effective_source is EffectiveAttendanceSource.NONE
        assert restored_none.effective_state.declared_companions == 2
        assert restored_none.effective_state.companions_admitted == 1

        gate_primary = admit_once(
            engine,
            event_id=event.id,
            raw=gate_raw,
            administrator_id=administrator.id,
            mode=GateArrivalMode.PRIMARY,
            declared=2,
            entering=2,
        )
        assert gate_primary.outcome is GateScanOutcome.ADMITTED
        assert gate_primary.attendance_id is not None
        with Session(engine) as session:
            attendance = session.get(GuestAttendanceRecord, gate_primary.attendance_id)
            entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.guest_id == gate_guest.id
                )
            )
            assert attendance is not None
            assert entitlement is not None
            attendance_before = (
                attendance.id,
                attendance.event_id,
                attendance.guest_id,
                attendance.companions_admitted,
                attendance.checked_in_at,
            )
            provenance_before = (
                entitlement.primary_attendance_id,
                entitlement.establishing_correction_id,
                entitlement.event_id,
                entitlement.guest_id,
            )
            admitted_attempts_before = list(
                session.execute(
                    select(
                        GateScanAttempt.id,
                        GateScanAttempt.arrival_mode,
                        GateScanAttempt.companions_admitted_this_scan,
                        GateScanAttempt.attendance_id,
                    ).where(
                        GateScanAttempt.guest_id == gate_guest.id,
                        GateScanAttempt.outcome == GateScanOutcome.ADMITTED,
                    )
                )
            )

        combined = correction_once(
            engine,
            event_id=event.id,
            guest_id=gate_guest.id,
            administrator_id=administrator.id,
            revision=0,
            decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
            declared=1,
            admitted=1,
        )
        assert (
            combined.correction.previous_declared_companions,
            combined.correction.previous_companions_admitted,
            combined.correction.resulting_declared_companions,
            combined.correction.resulting_companions_admitted,
        ) == (2, 2, 1, 1)
        assert (
            inspect_once(engine, event_id=event.id, raw=gate_raw).inspection_state
            is GateInspectionState.ENTITLEMENT_EXHAUSTED
        )
        denial = admit_once(
            engine,
            event_id=event.id,
            raw=gate_raw,
            administrator_id=administrator.id,
            mode=GateArrivalMode.COMPANION,
            entering=1,
        )
        assert denial.outcome is GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED
        correction_once(
            engine,
            event_id=event.id,
            guest_id=gate_guest.id,
            administrator_id=administrator.id,
            revision=1,
            decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
            declared=2,
            admitted=2,
        )
        correction_once(
            engine,
            event_id=event.id,
            guest_id=gate_guest.id,
            administrator_id=administrator.id,
            revision=2,
            expected_status=EffectiveAttendanceStatus.ATTENDED,
            decision=AttendanceCorrectionDecision.SET_NOT_ATTENDED,
        )
        with pytest.raises(AttendanceCorrectionConflictError):
            correction_once(
                engine,
                event_id=event.id,
                guest_id=gate_guest.id,
                administrator_id=administrator.id,
                revision=2,
                decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
                declared=1,
                admitted=1,
            )
        correction_once(
            engine,
            event_id=event.id,
            guest_id=gate_guest.id,
            administrator_id=administrator.id,
            revision=3,
            expected_status=EffectiveAttendanceStatus.NOT_ATTENDED,
            decision=AttendanceCorrectionDecision.RESTORE_GATE_DERIVED,
        )
        final_gate = correction_once(
            engine,
            event_id=event.id,
            guest_id=gate_guest.id,
            administrator_id=administrator.id,
            revision=4,
            decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
            declared=1,
            admitted=1,
        )
        assert final_gate.effective_state.primary_attended is True

        admit_once(
            engine,
            event_id=event.id,
            raw=bounded_raw,
            administrator_id=administrator.id,
            mode=GateArrivalMode.PRIMARY,
            declared=2,
            entering=1,
        )
        for declared, admitted in ((0, 1), (2, 2)):
            with pytest.raises(AttendanceCorrectionConflictError):
                correction_once(
                    engine,
                    event_id=event.id,
                    guest_id=bounded_guest.id,
                    administrator_id=administrator.id,
                    revision=0,
                    decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
                    declared=declared,
                    admitted=admitted,
                )
        with pytest.raises(AttendanceCorrectionConflictError):
            correction_once(
                engine,
                event_id=event.id,
                guest_id=missing_guest.id,
                administrator_id=administrator.id,
                revision=0,
                decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
                declared=2,
                admitted=0,
            )
        command_queries = observe_sql(
            engine,
            lambda: correction_once(
                engine,
                event_id=event.id,
                guest_id=bounded_guest.id,
                administrator_id=administrator.id,
                revision=0,
                decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
                declared=1,
                admitted=1,
            ),
        )
        assert command_queries <= 8

        admit_once(
            engine,
            event_id=event.id,
            raw=race_raw,
            administrator_id=administrator.id,
            mode=GateArrivalMode.PRIMARY,
            declared=2,
            entering=1,
        )
        barrier = Barrier(2)

        def race_correction():
            barrier.wait(timeout=10)
            return correction_once(
                engine,
                event_id=event.id,
                guest_id=race_guest.id,
                administrator_id=administrator.id,
                revision=0,
                decision=AttendanceCorrectionDecision.SET_COMPANION_STATE,
                declared=1,
                admitted=1,
            )

        def race_admission():
            barrier.wait(timeout=10)
            return admit_once(
                engine,
                event_id=event.id,
                raw=race_raw,
                administrator_id=administrator.id,
                mode=GateArrivalMode.COMPANION,
                entering=1,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            correction_future = executor.submit(race_correction)
            admission_future = executor.submit(race_admission)
            race_correction_result = correction_future.result(timeout=20)
            race_admission_result = admission_future.result(timeout=20)
        assert race_admission_result.outcome in {
            GateScanOutcome.ADMITTED,
            GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED,
        }
        expected_previous = (
            (2, 2) if race_admission_result.outcome is GateScanOutcome.ADMITTED else (2, 1)
        )
        assert (
            race_correction_result.correction.previous_declared_companions,
            race_correction_result.correction.previous_companions_admitted,
        ) == expected_previous

        with Session(engine) as session:
            race_entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.guest_id == race_guest.id
                )
            )
            assert race_entitlement is not None
            assert (
                race_entitlement.declared_companions,
                race_entitlement.companions_admitted,
            ) == (1, 1)

            gate_attendance = session.get(GuestAttendanceRecord, gate_primary.attendance_id)
            gate_entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.guest_id == gate_guest.id
                )
            )
            assert gate_attendance is not None
            assert gate_entitlement is not None
            assert (
                gate_attendance.id,
                gate_attendance.event_id,
                gate_attendance.guest_id,
                gate_attendance.companions_admitted,
                gate_attendance.checked_in_at,
            ) == attendance_before
            assert (
                gate_entitlement.primary_attendance_id,
                gate_entitlement.establishing_correction_id,
                gate_entitlement.event_id,
                gate_entitlement.guest_id,
            ) == provenance_before
            admitted_attempts_after = list(
                session.execute(
                    select(
                        GateScanAttempt.id,
                        GateScanAttempt.arrival_mode,
                        GateScanAttempt.companions_admitted_this_scan,
                        GateScanAttempt.attendance_id,
                    ).where(
                        GateScanAttempt.guest_id == gate_guest.id,
                        GateScanAttempt.outcome == GateScanOutcome.ADMITTED,
                    )
                )
            )
            assert admitted_attempts_after == admitted_attempts_before

            companion_correction_id = combined.correction.id
            with pytest.raises(IntegrityError):
                execute_and_commit(
                    session,
                    update(GuestAttendanceCorrection)
                    .where(GuestAttendanceCorrection.id == companion_correction_id)
                    .values(resulting_declared_companions=0),
                )
            session.rollback()
            with pytest.raises(IntegrityError):
                execute_and_commit(
                    session,
                    delete(GuestAttendanceCorrection).where(
                        GuestAttendanceCorrection.id == companion_correction_id
                    ),
                )
            session.rollback()
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceCorrection)
                    .where(GuestAttendanceCorrection.guest_id == bounded_guest.id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceRecord)
                    .where(GuestAttendanceRecord.guest_id == api_guest.id)
                )
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users CASCADE"))
        engine.dispose()
        assert disposable_alembic.main(["check"]) == 0
