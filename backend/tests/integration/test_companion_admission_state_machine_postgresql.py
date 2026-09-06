"""Opt-in PostgreSQL C2.3 state-machine and C2.4 scanner API acceptance."""

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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy import event as sqlalchemy_event
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
    EffectiveAttendanceStatus,
)
from app.models.event import (
    DEFAULT_GUEST_FIELD_CONFIGURATION,
    Event,
    EventGuestField,
    EventStatus,
)
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import generate_qr_credential, hash_qr_credential
from app.repositories.administrators import AdministratorRepository
from app.repositories.attendance import AttendanceRepository
from app.repositories.attendance_corrections import AttendanceCorrectionRepository
from app.repositories.events import EventRepository
from app.repositories.guests import GuestRepository
from app.services.admin_provisioning import AdministratorProvisioningService
from app.services.attendance import AttendanceService, GateAdmissionCommand, GateAdmissionResult
from app.services.attendance_corrections import (
    AttendanceCorrectionService,
)
from app.services.events import EventService, M3ConflictError

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


def make_event(administrator_id: UUID) -> Event:
    return Event(
        id=uuid4(),
        event_name=f"Synthetic C2.3 Event {uuid4().hex[:8]}",
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
    raw = generate_qr_credential()
    guest = Guest(
        id=uuid4(),
        event_id=event.id,
        full_name=f"Synthetic C2.3 Guest {uuid4().hex[:8]}",
        phone_number=None,
        email=None,
        invitation_type="Family",
        allowed_companions=2,
        table_number=None,
        category=None,
        status=GuestStatus.ACTIVE,
    )
    credential_id = uuid4()
    credential = GuestQrCredential(
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
    )
    session.add_all([guest, credential])
    return guest, raw


def command(
    *,
    request_id: UUID,
    event_id: UUID,
    raw: str,
    mode: GateArrivalMode,
    declared: int | None,
    entering: int,
    administrator_id: UUID,
) -> GateAdmissionCommand:
    return GateAdmissionCommand(
        request_id=request_id,
        event_id=event_id,
        presented_credential=raw,
        arrival_mode=mode,
        declared_companions=declared,
        companions_entering_now=entering,
        administrator_id=administrator_id,
    )


def admit_once(engine, admission: GateAdmissionCommand) -> GateAdmissionResult:
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        return AttendanceService(AttendanceRepository(session)).admit(admission)


def run_concurrently(engine, commands: list[GateAdmissionCommand]) -> list[GateAdmissionResult]:
    barrier = Barrier(len(commands))

    def worker(admission: GateAdmissionCommand) -> GateAdmissionResult:
        barrier.wait(timeout=10)
        return admit_once(engine, admission)

    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        return list(executor.map(worker, commands))


def observe_admission_sql(
    engine,
    admission: GateAdmissionCommand,
) -> tuple[GateAdmissionResult, int]:
    statements: list[str] = []

    def record_statement(*args) -> None:
        statements.append(str(args[2]))

    sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
    try:
        result = admit_once(engine, admission)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", record_statement)
    return result, len(statements)


def observe_inspection_sql(engine, *, event_id: UUID, raw: str) -> tuple[object, int]:
    statements: list[str] = []

    def record_statement(*args) -> None:
        statements.append(str(args[2]))

    sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with Session(engine, expire_on_commit=False, autoflush=False) as session:
            result = AttendanceService(AttendanceRepository(session)).inspect(
                event_id=event_id,
                presented_credential=raw,
            )
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", record_statement)
    return result, len(statements)


def assert_one_primary_state(engine, event_id: UUID, guest_id: UUID) -> None:
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(GuestAttendanceRecord)
                .where(
                    GuestAttendanceRecord.event_id == event_id,
                    GuestAttendanceRecord.guest_id == guest_id,
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(GuestCompanionEntitlement)
                .where(
                    GuestCompanionEntitlement.event_id == event_id,
                    GuestCompanionEntitlement.guest_id == guest_id,
                )
            )
            == 1
        )


def truncate_synthetic_admission_evidence(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE gate_scan_attempts, guest_companion_entitlements, "
                "guest_attendance_corrections, guest_attendance_records CASCADE"
            )
        )


async def exercise_c24_api(
    settings: Settings,
    *,
    username: str,
    event_id: UUID,
    raw: str,
    stale_primary_raw: str,
    correction_raw: str,
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

        async def inspect(credential: str):
            response = await client.post(
                "/api/v1/attendance/inspect",
                headers=headers,
                json={"event_id": str(event_id), "credential": credential},
            )
            assert response.status_code == 200
            return response.json()

        async def admit(
            credential: str,
            *,
            request_id: UUID,
            mode: str,
            entering: int,
            declared: int | None = None,
        ):
            payload = {
                "request_id": str(request_id),
                "event_id": str(event_id),
                "credential": credential,
                "arrival_mode": mode,
                "companions_entering_now": entering,
            }
            if declared is not None:
                payload["declared_companions"] = declared
            response = await client.post(
                "/api/v1/attendance/admit",
                headers=headers,
                json=payload,
            )
            assert response.status_code == 200
            return response.json()

        primary_request_id = uuid4()
        companion_request_id = uuid4()
        results = {"initial": await inspect(raw)}
        results["primary"] = await admit(
            raw,
            request_id=primary_request_id,
            mode="PRIMARY",
            declared=2,
            entering=0,
        )
        results["primary_replay"] = await admit(
            raw,
            request_id=primary_request_id,
            mode="PRIMARY",
            declared=2,
            entering=0,
        )
        results["after_primary"] = await inspect(raw)
        results["companion"] = await admit(
            raw,
            request_id=companion_request_id,
            mode="COMPANION",
            entering=1,
        )
        results["after_companion"] = await inspect(raw)
        results["replay"] = await admit(
            raw,
            request_id=companion_request_id,
            mode="COMPANION",
            entering=1,
        )
        conflict = await client.post(
            "/api/v1/attendance/admit",
            headers=headers,
            json={
                "request_id": str(companion_request_id),
                "event_id": str(event_id),
                "credential": raw,
                "arrival_mode": "COMPANION",
                "companions_entering_now": 2,
            },
        )
        results["idempotency_conflict"] = {
            "status_code": conflict.status_code,
            "body": conflict.json(),
        }
        results["stale_companion_inspection"] = await inspect(raw)
        results["last_slot"] = await admit(
            raw,
            request_id=uuid4(),
            mode="COMPANION",
            entering=1,
        )
        results["stale_companion_denial"] = await admit(
            raw,
            request_id=uuid4(),
            mode="COMPANION",
            entering=1,
        )
        results["exhausted"] = await inspect(raw)

        results["stale_primary_inspection"] = await inspect(stale_primary_raw)
        results["primary_winner"] = await admit(
            stale_primary_raw,
            request_id=uuid4(),
            mode="PRIMARY",
            declared=1,
            entering=0,
        )
        results["stale_primary_denial"] = await admit(
            stale_primary_raw,
            request_id=uuid4(),
            mode="PRIMARY",
            declared=2,
            entering=0,
        )
        results["correction_inspection"] = await inspect(correction_raw)
        results["correction_companion"] = await admit(
            correction_raw,
            request_id=uuid4(),
            mode="COMPANION",
            entering=1,
        )
        return results


def test_c24_postgresql_two_step_api_and_stale_inspection_safety(
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
        username = f"c24-admin-{uuid4().hex[:8]}"
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session),
                AuthenticationSecurity.from_settings(settings).passwords,
            ).provision(username, "synthetic administrator password")
            selected_event = make_event(administrator.id)
            session.add(selected_event)
            session.flush()
            session.add_all(
                [
                    EventGuestField(
                        id=uuid4(),
                        event_id=selected_event.id,
                        field_key=field_key,
                        enabled=enabled,
                        required=required,
                        sort_order=sort_order,
                    )
                    for sort_order, (field_key, enabled, required) in enumerate(
                        DEFAULT_GUEST_FIELD_CONFIGURATION
                    )
                ]
            )
            guest, raw = add_guest_credential(session, selected_event, administrator.id)
            stale_guest, stale_primary_raw = add_guest_credential(
                session, selected_event, administrator.id
            )
            correction_guest, correction_raw = add_guest_credential(
                session, selected_event, administrator.id
            )
            session.commit()

        with Session(engine, expire_on_commit=False) as session:
            correction = AttendanceCorrectionService(
                AttendanceCorrectionRepository(session)
            ).record_correction(
                event_id=selected_event.id,
                guest_id=correction_guest.id,
                expected_correction_revision=0,
                expected_effective_status=EffectiveAttendanceStatus.NOT_ATTENDED,
                decision=AttendanceCorrectionDecision.SET_ATTENDED,
                reason_code=AttendanceCorrectionReason.MISSED_GATE_SCAN,
                reason_note=None,
                companions_admitted=0,
                administrator_id=administrator.id,
            )
            session.add(
                GuestCompanionEntitlement(
                    id=uuid4(),
                    event_id=selected_event.id,
                    guest_id=correction_guest.id,
                    primary_attendance_id=None,
                    establishing_correction_id=correction.correction.id,
                    declared_companions=2,
                    companions_admitted=0,
                )
            )
            session.commit()

        initial_inspection, inspection_queries = observe_inspection_sql(
            engine,
            event_id=selected_event.id,
            raw=raw,
        )
        assert initial_inspection.inspection_state is GateInspectionState.READY_FOR_PRIMARY
        assert inspection_queries == 4

        results = asyncio.run(
            exercise_c24_api(
                settings,
                username=username,
                event_id=selected_event.id,
                raw=raw,
                stale_primary_raw=stale_primary_raw,
                correction_raw=correction_raw,
            )
        )
        assert results["initial"]["inspection_state"] == GateInspectionState.READY_FOR_PRIMARY
        assert results["primary"]["outcome"] == GateScanOutcome.ADMITTED
        assert results["primary_replay"]["scan_attempt_id"] == results["primary"]["scan_attempt_id"]
        assert results["after_primary"]["remaining_companion_slots"] == 2
        assert results["companion"]["outcome"] == GateScanOutcome.ADMITTED
        assert results["after_companion"]["remaining_companion_slots"] == 1
        assert results["replay"]["scan_attempt_id"] == results["companion"]["scan_attempt_id"]
        assert results["idempotency_conflict"] == {
            "status_code": 409,
            "body": {"detail": {"code": "IDEMPOTENCY_KEY_REUSED"}},
        }
        assert results["last_slot"]["outcome"] == GateScanOutcome.ADMITTED
        assert (
            results["stale_companion_denial"]["outcome"]
            == GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED
        )
        assert results["exhausted"]["inspection_state"] == GateInspectionState.ENTITLEMENT_EXHAUSTED
        assert (
            results["stale_primary_inspection"]["inspection_state"]
            == GateInspectionState.READY_FOR_PRIMARY
        )
        assert results["primary_winner"]["outcome"] == GateScanOutcome.ADMITTED
        assert results["stale_primary_denial"]["outcome"] == GateScanOutcome.ALREADY_CHECKED_IN
        assert results["stale_primary_denial"]["declared_companions"] == 1
        assert (
            results["correction_inspection"]["inspection_state"]
            == GateInspectionState.READY_FOR_COMPANION
        )
        assert results["correction_companion"]["outcome"] == GateScanOutcome.ADMITTED
        assert results["correction_companion"]["primary_attended"] is True

        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(GateScanAttempt)) == 7
            entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.guest_id == guest.id
                )
            )
            stale_entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.guest_id == stale_guest.id
                )
            )
            assert entitlement is not None
            assert entitlement.companions_admitted == 2
            assert stale_entitlement is not None
            assert stale_entitlement.declared_companions == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceRecord)
                    .where(GuestAttendanceRecord.guest_id == correction_guest.id)
                )
                == 0
            )
    finally:
        engine.dispose()


def test_c23_postgresql_both_origins_and_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = configure_test_environment(monkeypatch, tmp_path)
    assert DISPOSABLE_URL is not None
    reset_engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    truncate_synthetic_admission_evidence(reset_engine)
    reset_engine.dispose()
    assert disposable_alembic.main(["downgrade", "base"]) == 0
    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["check"]) == 0

    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    try:
        username = f"c23-admin-{uuid4().hex[:8]}"
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session),
                AuthenticationSecurity.from_settings(settings).passwords,
            ).provision(username, "synthetic administrator password")
            event = make_event(administrator.id)
            session.add(event)
            session.flush()
            session.add_all(
                [
                    EventGuestField(
                        id=uuid4(),
                        event_id=event.id,
                        field_key=field_key,
                        enabled=enabled,
                        required=required,
                        sort_order=sort_order,
                    )
                    for sort_order, (field_key, enabled, required) in enumerate(
                        DEFAULT_GUEST_FIELD_CONFIGURATION
                    )
                ]
            )
            first_guest, first_raw = add_guest_credential(session, event, administrator.id)
            conflict_guest, conflict_raw = add_guest_credential(session, event, administrator.id)
            slot_guest, slot_raw = add_guest_credential(session, event, administrator.id)
            replay_guest, replay_raw = add_guest_credential(session, event, administrator.id)
            correction_guest, correction_raw = add_guest_credential(
                session, event, administrator.id
            )
            _, query_raw = add_guest_credential(session, event, administrator.id)
            freeze_guest, freeze_raw = add_guest_credential(session, event, administrator.id)
            freeze_guest.allowed_companions = 1
            session.commit()

        first_results = run_concurrently(
            engine,
            [
                command(
                    request_id=uuid4(),
                    event_id=event.id,
                    raw=first_raw,
                    mode=GateArrivalMode.PRIMARY,
                    declared=2,
                    entering=0,
                    administrator_id=administrator.id,
                )
                for _ in range(2)
            ],
        )
        assert sorted(result.outcome.value for result in first_results) == [
            GateScanOutcome.ADMITTED.value,
            GateScanOutcome.ALREADY_CHECKED_IN.value,
        ]
        assert_one_primary_state(engine, event.id, first_guest.id)

        with Session(engine, expire_on_commit=False) as session:
            event_service = EventService(EventRepository(session), GuestRepository(session))
            updated = event_service.update_guest(
                event.id,
                freeze_guest.id,
                {"allowed_companions": 2},
            )
            assert updated.allowed_companions == 2
        admit_once(
            engine,
            command(
                request_id=uuid4(),
                event_id=event.id,
                raw=freeze_raw,
                mode=GateArrivalMode.PRIMARY,
                declared=2,
                entering=0,
                administrator_id=administrator.id,
            ),
        )
        with Session(engine, expire_on_commit=False) as session:
            event_service = EventService(EventRepository(session), GuestRepository(session))
            with pytest.raises(M3ConflictError, match="allowance is locked"):
                event_service.update_guest(
                    event.id,
                    freeze_guest.id,
                    {"allowed_companions": 1},
                )
            same = event_service.update_guest(
                event.id,
                freeze_guest.id,
                {"allowed_companions": 2},
            )
            assert same.allowed_companions == 2
            renamed = event_service.update_guest(
                event.id,
                freeze_guest.id,
                {"full_name": "Synthetic Updated Guest"},
            )
            assert renamed.full_name == "Synthetic Updated Guest"

        conflict_results = run_concurrently(
            engine,
            [
                command(
                    request_id=uuid4(),
                    event_id=event.id,
                    raw=conflict_raw,
                    mode=GateArrivalMode.PRIMARY,
                    declared=declared,
                    entering=0,
                    administrator_id=administrator.id,
                )
                for declared in (0, 2)
            ],
        )
        assert sorted(result.outcome.value for result in conflict_results) == [
            GateScanOutcome.ADMITTED.value,
            GateScanOutcome.ALREADY_CHECKED_IN.value,
        ]
        with Session(engine) as session:
            conflict_entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.event_id == event.id,
                    GuestCompanionEntitlement.guest_id == conflict_guest.id,
                )
            )
            assert conflict_entitlement is not None
            assert conflict_entitlement.declared_companions in {0, 2}
            assert conflict_entitlement.companions_admitted == 0

        admit_once(
            engine,
            command(
                request_id=uuid4(),
                event_id=event.id,
                raw=slot_raw,
                mode=GateArrivalMode.PRIMARY,
                declared=2,
                entering=1,
                administrator_id=administrator.id,
            ),
        )
        slot_results = run_concurrently(
            engine,
            [
                command(
                    request_id=uuid4(),
                    event_id=event.id,
                    raw=slot_raw,
                    mode=GateArrivalMode.COMPANION,
                    declared=None,
                    entering=1,
                    administrator_id=administrator.id,
                )
                for _ in range(2)
            ],
        )
        assert sorted(result.outcome.value for result in slot_results) == [
            GateScanOutcome.ADMITTED.value,
            GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED.value,
        ]
        with Session(engine) as session:
            slot_entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.guest_id == slot_guest.id
                )
            )
            assert slot_entitlement is not None
            assert slot_entitlement.companions_admitted == 2

        admit_once(
            engine,
            command(
                request_id=uuid4(),
                event_id=event.id,
                raw=replay_raw,
                mode=GateArrivalMode.PRIMARY,
                declared=2,
                entering=0,
                administrator_id=administrator.id,
            ),
        )
        shared_request_id = uuid4()
        replay_command = command(
            request_id=shared_request_id,
            event_id=event.id,
            raw=replay_raw,
            mode=GateArrivalMode.COMPANION,
            declared=None,
            entering=1,
            administrator_id=administrator.id,
        )
        replay_results = run_concurrently(engine, [replay_command, replay_command])
        assert [result.outcome for result in replay_results] == [
            GateScanOutcome.ADMITTED,
            GateScanOutcome.ADMITTED,
        ]
        assert replay_results[0].scan_attempt_id == replay_results[1].scan_attempt_id
        with Session(engine) as session:
            replay_entitlement = session.scalar(
                select(GuestCompanionEntitlement).where(
                    GuestCompanionEntitlement.guest_id == replay_guest.id
                )
            )
            assert replay_entitlement is not None
            assert replay_entitlement.companions_admitted == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GateScanAttempt)
                    .where(
                        GateScanAttempt.event_id == event.id,
                        GateScanAttempt.request_id == shared_request_id,
                    )
                )
                == 1
            )

        with Session(engine, expire_on_commit=False) as session:
            correction = AttendanceCorrectionService(
                AttendanceCorrectionRepository(session)
            ).record_correction(
                event_id=event.id,
                guest_id=correction_guest.id,
                expected_correction_revision=0,
                expected_effective_status=EffectiveAttendanceStatus.NOT_ATTENDED,
                decision=AttendanceCorrectionDecision.SET_ATTENDED,
                reason_code=AttendanceCorrectionReason.MISSED_GATE_SCAN,
                reason_note=None,
                companions_admitted=0,
                administrator_id=administrator.id,
            )
            session.add(
                GuestCompanionEntitlement(
                    id=uuid4(),
                    event_id=event.id,
                    guest_id=correction_guest.id,
                    primary_attendance_id=None,
                    establishing_correction_id=correction.correction.id,
                    declared_companions=2,
                    companions_admitted=0,
                )
            )
            session.commit()

        correction_result = admit_once(
            engine,
            command(
                request_id=uuid4(),
                event_id=event.id,
                raw=correction_raw,
                mode=GateArrivalMode.COMPANION,
                declared=None,
                entering=1,
                administrator_id=administrator.id,
            ),
        )
        assert correction_result.outcome is GateScanOutcome.ADMITTED
        assert correction_result.primary_attended is True
        assert correction_result.attendance_id is None
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceRecord)
                    .where(GuestAttendanceRecord.guest_id == correction_guest.id)
                )
                == 0
            )

        query_primary_id = uuid4()
        query_primary, primary_sql = observe_admission_sql(
            engine,
            command(
                request_id=query_primary_id,
                event_id=event.id,
                raw=query_raw,
                mode=GateArrivalMode.PRIMARY,
                declared=2,
                entering=0,
                administrator_id=administrator.id,
            ),
        )
        assert query_primary.outcome is GateScanOutcome.ADMITTED
        query_companion_id = uuid4()
        query_companion_command = command(
            request_id=query_companion_id,
            event_id=event.id,
            raw=query_raw,
            mode=GateArrivalMode.COMPANION,
            declared=None,
            entering=1,
            administrator_id=administrator.id,
        )
        query_companion, companion_sql = observe_admission_sql(engine, query_companion_command)
        query_replay, replay_sql = observe_admission_sql(engine, query_companion_command)
        assert query_companion.outcome is GateScanOutcome.ADMITTED
        assert query_replay.scan_attempt_id == query_companion.scan_attempt_id
        assert primary_sql <= 12
        assert companion_sql <= 10
        assert replay_sql <= 8
        print(
            f"c23_query_counts: primary={primary_sql} companion={companion_sql} replay={replay_sql}"
        )
    finally:
        truncate_synthetic_admission_evidence(engine)
        engine.dispose()
        assert disposable_alembic.main(["downgrade", "base"]) == 0
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0
