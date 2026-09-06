"""Opt-in PostgreSQL M7 API, constraints, concurrency, and timing verification."""

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
from sqlalchemy import create_engine, delete, func, inspect, select, text, update
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator
from app.models.attendance import GateScanAttempt, GateScanOutcome, GuestAttendanceRecord
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import generate_qr_credential, hash_qr_credential
from app.repositories.administrators import AdministratorRepository
from app.repositories.attendance import AttendanceRepository
from app.services.admin_provisioning import AdministratorProvisioningService
from app.services.attendance import AttendanceService

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


def make_event(administrator_id: UUID, *, maximum: int = 2) -> Event:
    return Event(
        id=uuid4(),
        event_name=f"Synthetic M7 Event {uuid4().hex[:8]}",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=datetime(2027, 6, 12, 14, 30, tzinfo=UTC),
        venue="Synthetic Hall",
        status=EventStatus.ACTIVE,
        max_companions_per_invitation=maximum,
        created_by=administrator_id,
    )


def add_guest_credential(
    session: Session,
    event: Event,
    administrator_id: UUID,
    *,
    allowed: int,
    qr_status: QrCredentialStatus = QrCredentialStatus.ACTIVE,
    guest_status: GuestStatus = GuestStatus.ACTIVE,
) -> tuple[Guest, GuestQrCredential, str]:
    raw = generate_qr_credential()
    guest = Guest(
        id=uuid4(),
        event_id=event.id,
        full_name=f"Synthetic Gate Guest {uuid4().hex[:8]}",
        phone_number=None,
        email=None,
        invitation_type="Family" if allowed else "Individual",
        allowed_companions=allowed,
        table_number="T7",
        category=None,
        status=guest_status,
    )
    credential_id = uuid4()
    credential = GuestQrCredential(
        id=credential_id,
        event_id=event.id,
        guest_id=guest.id,
        token_hash=hash_qr_credential(raw),
        image_storage_key=f"{event.id.hex}/{guest.id.hex}/{credential_id.hex}.png",
        image_sha256="a" * 64,
        status=qr_status,
        replaces_credential_id=None,
        issued_by=administrator_id,
        issued_at=datetime.now(UTC),
        deactivated_at=datetime.now(UTC) if qr_status is QrCredentialStatus.INACTIVE else None,
        superseded_at=datetime.now(UTC) if qr_status is QrCredentialStatus.SUPERSEDED else None,
    )
    session.add_all([guest, credential])
    return guest, credential, raw


async def exercise_authenticated_api(
    settings: Settings,
    username: str,
    event_id: UUID,
    zero_guest_id: UUID,
    raw_by_case: dict[str, str],
    other_event_id: UUID,
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

        async def scan(case: str, companions: int, selected_event_id: UUID = event_id):
            return await client.post(
                "/api/v1/attendance/scan",
                headers=headers,
                json={
                    "event_id": str(selected_event_id),
                    "credential": raw_by_case[case],
                    "companions_admitted": companions,
                },
            )

        responses = {
            "valid_0": await scan("valid_0", 0),
            "valid_1": await scan("valid_1", 1),
            "valid_2": await scan("valid_2", 2),
            "guest_limit": await scan("guest_limit", 1),
            "event_limit": await scan("event_limit", 1, other_event_id),
            "unknown": await scan("unknown", 0),
            "wrong_event": await scan("wrong_event", 0),
            "inactive": await scan("inactive", 0),
            "superseded": await scan("superseded", 0),
            "duplicate": await scan("valid_1", 0),
        }
        responses["attendance_list"] = await client.get(
            f"/api/v1/events/{event_id}/attendance?limit=2&offset=0",
            headers=headers,
        )
        responses["guest_attendance"] = await client.get(
            f"/api/v1/guests/{zero_guest_id}/attendance",
            headers=headers,
        )
        responses["attempt_list"] = await client.get(
            f"/api/v1/events/{event_id}/scan-attempts?limit=100&offset=0",
            headers=headers,
        )
        responses["missing_auth"] = await client.post(
            "/api/v1/attendance/scan",
            json={"event_id": str(event_id), "credential": raw_by_case["valid_0"]},
        )
        responses["invalid_auth"] = await client.post(
            "/api/v1/attendance/scan",
            headers={"Authorization": "Bearer invalid-token"},
            json={"event_id": str(event_id), "credential": raw_by_case["valid_0"]},
        )
        responses["malformed"] = await client.post(
            "/api/v1/attendance/scan",
            headers=headers,
            json={"event_id": str(event_id), "credential": "invalid"},
        )
        responses["negative"] = await client.post(
            "/api/v1/attendance/scan",
            headers=headers,
            json={
                "event_id": str(event_id),
                "credential": raw_by_case["valid_0"],
                "companions_admitted": -1,
            },
        )
        responses["above_system_max"] = await client.post(
            "/api/v1/attendance/scan",
            headers=headers,
            json={
                "event_id": str(event_id),
                "credential": raw_by_case["valid_0"],
                "companions_admitted": 3,
            },
        )
        responses["extra"] = await client.post(
            "/api/v1/attendance/scan",
            headers=headers,
            json={
                "event_id": str(event_id),
                "credential": raw_by_case["valid_0"],
                "unexpected": "rejected",
            },
        )
    return responses


def scan_once(
    engine,
    *,
    event_id: UUID,
    raw: str,
    companions: int,
    administrator_id: UUID,
) -> GateScanOutcome:
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        result = AttendanceService(AttendanceRepository(session)).scan(
            event_id=event_id,
            presented_credential=raw,
            companions_admitted=companions,
            administrator_id=administrator_id,
        )
        return result.outcome


def observe_scan_sql(
    engine,
    *,
    event_id: UUID,
    raw: str,
    companions: int,
    administrator_id: UUID,
) -> int:
    statements: list[str] = []

    def record_statement(*args) -> None:
        statements.append(str(args[2]))

    sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
    try:
        scan_once(
            engine,
            event_id=event_id,
            raw=raw,
            companions=companions,
            administrator_id=administrator_id,
        )
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


def test_m7_postgresql_api_constraints_concurrency_queries_and_timings(
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
    username = f"m7-admin-{uuid4().hex[:8]}"
    try:
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session),
                AuthenticationSecurity.from_settings(settings).passwords,
            ).provision(username, "synthetic administrator password")
            administrator_id = administrator.id
            event = make_event(administrator.id)
            zero_event = make_event(administrator.id, maximum=0)
            wrong_event = make_event(administrator.id)
            session.add_all([event, zero_event, wrong_event])
            session.flush()

            seeded: dict[str, tuple[Guest, GuestQrCredential, str]] = {
                "valid_0": add_guest_credential(session, event, administrator.id, allowed=0),
                "valid_1": add_guest_credential(session, event, administrator.id, allowed=1),
                "valid_2": add_guest_credential(session, event, administrator.id, allowed=2),
                "guest_limit": add_guest_credential(
                    session,
                    event,
                    administrator.id,
                    allowed=0,
                ),
                "event_limit": add_guest_credential(
                    session,
                    zero_event,
                    administrator.id,
                    allowed=0,
                ),
                "wrong_event": add_guest_credential(
                    session,
                    wrong_event,
                    administrator.id,
                    allowed=0,
                ),
                "inactive": add_guest_credential(
                    session,
                    event,
                    administrator.id,
                    allowed=0,
                    qr_status=QrCredentialStatus.INACTIVE,
                ),
                "superseded": add_guest_credential(
                    session,
                    event,
                    administrator.id,
                    allowed=0,
                    qr_status=QrCredentialStatus.SUPERSEDED,
                ),
            }
            session.commit()
            cleanup_required = True
            event_id = event.id
            zero_event_id = zero_event.id
            zero_guest_id = seeded["valid_0"][0].id
            raw_by_case = {case: values[2] for case, values in seeded.items()}
            raw_by_case["unknown"] = generate_qr_credential()

        responses = asyncio.run(
            exercise_authenticated_api(
                settings,
                username,
                event_id,
                zero_guest_id,
                raw_by_case,
                zero_event_id,
            )
        )
        expected_outcomes = {
            "valid_0": GateScanOutcome.ADMITTED,
            "valid_1": GateScanOutcome.ADMITTED,
            "valid_2": GateScanOutcome.ADMITTED,
            "guest_limit": GateScanOutcome.COMPANION_LIMIT_EXCEEDED,
            "event_limit": GateScanOutcome.COMPANION_LIMIT_EXCEEDED,
            "unknown": GateScanOutcome.UNKNOWN_CREDENTIAL,
            "wrong_event": GateScanOutcome.WRONG_EVENT,
            "inactive": GateScanOutcome.INACTIVE_CREDENTIAL,
            "superseded": GateScanOutcome.SUPERSEDED_CREDENTIAL,
            "duplicate": GateScanOutcome.ALREADY_CHECKED_IN,
        }
        for case, outcome in expected_outcomes.items():
            assert responses[case].status_code == 200, responses[case].text
            assert responses[case].json()["outcome"] == outcome.value
        assert responses["duplicate"].json()["companions_admitted"] == 1
        assert responses["valid_2"].json()["admitted_party_size"] == 3
        assert responses["wrong_event"].json()["guest_id"] is None
        assert responses["attendance_list"].status_code == 200
        assert len(responses["attendance_list"].json()["items"]) == 2
        assert responses["guest_attendance"].status_code == 200
        assert responses["attempt_list"].status_code == 200
        assert responses["missing_auth"].status_code == 401
        assert responses["invalid_auth"].status_code == 401
        assert all(
            responses[case].status_code == 422
            for case in ("malformed", "negative", "above_system_max", "extra")
        )

        raw_values = set(raw_by_case.values())
        for response in responses.values():
            assert all(raw not in response.text for raw in raw_values)
            assert "credential_fingerprint" not in response.text
            assert "token_hash" not in response.text
        assert all(raw not in caplog.text for raw in raw_values)

        inspector = inspect(engine)
        assert {
            "guest_attendance_records",
            "gate_scan_attempts",
        } <= set(inspector.get_table_names())
        assert {
            "uq_guest_attendance_records_event_guest",
            "uq_guest_attendance_records_id_event_guest",
        } <= {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("guest_attendance_records")
        }
        assert "ix_guest_attendance_records_event_checked_id" in {
            index["name"] for index in inspector.get_indexes("guest_attendance_records")
        }
        assert {
            "ix_gate_scan_attempts_event_scanned_id",
            "ix_gate_scan_attempts_fingerprint_scanned_id",
            "uq_gate_scan_attempts_one_primary_admitted_attendance",
        } <= {index["name"] for index in inspector.get_indexes("gate_scan_attempts")}
        assert "credential" not in {
            column["name"] for column in inspector.get_columns("gate_scan_attempts")
        }

        with Session(engine, expire_on_commit=False) as session:
            attendance_rows = list(session.execute(select(GuestAttendanceRecord)).scalars())
            attempt_rows = list(session.execute(select(GateScanAttempt)).scalars())
            assert len(attendance_rows) == 3
            assert len(attempt_rows) == len(expected_outcomes)
            assert all(attempt.credential_fingerprint not in raw_values for attempt in attempt_rows)
            assert all(len(attempt.credential_fingerprint) == 64 for attempt in attempt_rows)
            wrong_attempt = next(
                attempt
                for attempt in attempt_rows
                if attempt.outcome is GateScanOutcome.WRONG_EVENT
            )
            assert wrong_attempt.guest_id is None
            assert wrong_attempt.qr_credential_id is None
            assert wrong_attempt.attendance_id is None

            base = next(row for row in attendance_rows if row.guest_id == zero_guest_id)
            duplicate = GuestAttendanceRecord(
                id=uuid4(),
                event_id=base.event_id,
                guest_id=base.guest_id,
                first_qr_credential_id=base.first_qr_credential_id,
                companions_admitted=0,
                checked_in_by=administrator_id,
            )
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            standalone_event = session.get(Event, event_id)
            assert standalone_event is not None
            standalone_guest, standalone_credential, _ = add_guest_credential(
                session,
                standalone_event,
                administrator_id,
                allowed=0,
            )
            session.commit()
            session.add(
                GuestAttendanceRecord(
                    id=uuid4(),
                    event_id=event_id,
                    guest_id=standalone_guest.id,
                    first_qr_credential_id=standalone_credential.id,
                    companions_admitted=0,
                    checked_in_by=administrator_id,
                    checked_in_at=datetime.now(UTC),
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            supplied_time = datetime(2000, 1, 1, tzinfo=UTC)
            server_timed_attendance = GuestAttendanceRecord(
                id=uuid4(),
                event_id=event_id,
                guest_id=standalone_guest.id,
                first_qr_credential_id=standalone_credential.id,
                companions_admitted=0,
                checked_in_by=administrator_id,
                checked_in_at=supplied_time,
                created_at=supplied_time,
            )
            server_timed_attempt = GateScanAttempt(
                id=uuid4(),
                event_id=event_id,
                credential_fingerprint=standalone_credential.token_hash,
                guest_id=standalone_guest.id,
                qr_credential_id=standalone_credential.id,
                attendance_id=server_timed_attendance.id,
                outcome=GateScanOutcome.ADMITTED,
                requested_companions=0,
                scanned_by=administrator_id,
                scanned_at=supplied_time,
            )
            session.add(server_timed_attendance)
            session.flush()
            session.add(server_timed_attempt)
            session.commit()
            session.refresh(server_timed_attendance)
            session.refresh(server_timed_attempt)
            assert server_timed_attendance.checked_in_at != supplied_time
            assert server_timed_attendance.created_at == server_timed_attendance.checked_in_at
            assert server_timed_attempt.scanned_at == server_timed_attendance.checked_in_at

            zero_policy_guest, zero_policy_credential, _ = seeded["guest_limit"]
            session.add(
                GuestAttendanceRecord(
                    id=uuid4(),
                    event_id=event_id,
                    guest_id=zero_policy_guest.id,
                    first_qr_credential_id=zero_policy_credential.id,
                    companions_admitted=1,
                    checked_in_by=administrator_id,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            wrong_guest, wrong_credential, _ = seeded["wrong_event"]
            session.add(
                GuestAttendanceRecord(
                    id=uuid4(),
                    event_id=event_id,
                    guest_id=wrong_guest.id,
                    first_qr_credential_id=wrong_credential.id,
                    companions_admitted=0,
                    checked_in_by=administrator_id,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            invalid_scanner_guest, invalid_scanner_credential, _ = seeded["guest_limit"]
            session.add(
                GuestAttendanceRecord(
                    id=uuid4(),
                    event_id=event_id,
                    guest_id=invalid_scanner_guest.id,
                    first_qr_credential_id=invalid_scanner_credential.id,
                    companions_admitted=0,
                    checked_in_by=uuid4(),
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            system_event = session.get(Event, event_id)
            assert system_event is not None
            above_system_guest, above_system_credential, _ = add_guest_credential(
                session,
                system_event,
                administrator_id,
                allowed=2,
            )
            session.commit()
            session.add(
                GuestAttendanceRecord(
                    id=uuid4(),
                    event_id=event_id,
                    guest_id=above_system_guest.id,
                    first_qr_credential_id=above_system_credential.id,
                    companions_admitted=3,
                    checked_in_by=administrator_id,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            assert_integrity_failure(
                session,
                update(GuestAttendanceRecord)
                .where(GuestAttendanceRecord.id == base.id)
                .values(companions_admitted=0),
            )
            assert_integrity_failure(
                session,
                delete(GuestAttendanceRecord).where(GuestAttendanceRecord.id == base.id),
            )
            first_attempt = attempt_rows[0]
            assert_integrity_failure(
                session,
                update(GateScanAttempt)
                .where(GateScanAttempt.id == first_attempt.id)
                .values(requested_companions=0),
            )
            assert_integrity_failure(
                session,
                delete(GateScanAttempt).where(GateScanAttempt.id == first_attempt.id),
            )
            session.add(
                GateScanAttempt(
                    id=uuid4(),
                    event_id=event_id,
                    credential_fingerprint="f" * 64,
                    guest_id=None,
                    qr_credential_id=None,
                    attendance_id=base.id,
                    outcome=GateScanOutcome.UNKNOWN_CREDENTIAL,
                    requested_companions=0,
                    scanned_by=administrator_id,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        assert administrator_id is not None
        with Session(engine, expire_on_commit=False) as session:
            same_guest, _, same_raw = add_guest_credential(
                session,
                session.get(Event, event_id),
                administrator_id,
                allowed=2,
            )
            different_a, _, different_a_raw = add_guest_credential(
                session,
                session.get(Event, event_id),
                administrator_id,
                allowed=1,
            )
            different_b, _, different_b_raw = add_guest_credential(
                session,
                session.get(Event, event_id),
                administrator_id,
                allowed=1,
            )
            session.commit()
            same_guest_id = same_guest.id
            different_ids = {different_a.id, different_b.id}

        same_barrier = Barrier(2)

        def concurrent_same() -> GateScanOutcome:
            same_barrier.wait(timeout=10)
            return scan_once(
                engine,
                event_id=event_id,
                raw=same_raw,
                companions=2,
                administrator_id=administrator_id,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            same_outcomes = list(executor.map(lambda _: concurrent_same(), range(2)))
        assert sorted(outcome.value for outcome in same_outcomes) == [
            GateScanOutcome.ADMITTED.value,
            GateScanOutcome.ALREADY_CHECKED_IN.value,
        ]

        different_barrier = Barrier(2)

        def concurrent_different(raw: str) -> GateScanOutcome:
            different_barrier.wait(timeout=10)
            return scan_once(
                engine,
                event_id=event_id,
                raw=raw,
                companions=1,
                administrator_id=administrator_id,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            different_outcomes = list(
                executor.map(concurrent_different, [different_a_raw, different_b_raw])
            )
        assert different_outcomes == [GateScanOutcome.ADMITTED, GateScanOutcome.ADMITTED]

        with Session(engine) as session:
            same_attendance = session.execute(
                select(GuestAttendanceRecord).where(GuestAttendanceRecord.guest_id == same_guest_id)
            ).scalar_one()
            assert same_attendance.companions_admitted == 2
            assert same_attendance.admitted_party_size == 3
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceRecord)
                    .where(GuestAttendanceRecord.guest_id == same_guest_id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GateScanAttempt)
                    .where(GateScanAttempt.guest_id == same_guest_id)
                )
                == 2
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(GuestAttendanceRecord)
                    .where(GuestAttendanceRecord.guest_id.in_(different_ids))
                )
                == 2
            )

        with Session(engine, expire_on_commit=False) as session:
            observed_event = session.get(Event, event_id)
            observed_wrong_event = session.get(Event, wrong_event.id)
            assert observed_event is not None
            assert observed_wrong_event is not None
            valid_query_guest, _, valid_query_raw = add_guest_credential(
                session,
                observed_event,
                administrator_id,
                allowed=0,
            )
            duplicate_query_guest, _, duplicate_query_raw = add_guest_credential(
                session,
                observed_event,
                administrator_id,
                allowed=0,
            )
            wrong_query_guest, _, wrong_query_raw = add_guest_credential(
                session,
                observed_wrong_event,
                administrator_id,
                allowed=0,
            )
            inactive_query_guest, _, inactive_query_raw = add_guest_credential(
                session,
                observed_event,
                administrator_id,
                allowed=0,
                qr_status=QrCredentialStatus.INACTIVE,
            )
            session.commit()
            assert {
                valid_query_guest.id,
                duplicate_query_guest.id,
                wrong_query_guest.id,
                inactive_query_guest.id,
            }
        assert (
            scan_once(
                engine,
                event_id=event_id,
                raw=duplicate_query_raw,
                companions=0,
                administrator_id=administrator_id,
            )
            is GateScanOutcome.ADMITTED
        )
        query_counts = {
            "valid": observe_scan_sql(
                engine,
                event_id=event_id,
                raw=valid_query_raw,
                companions=0,
                administrator_id=administrator_id,
            ),
            "duplicate": observe_scan_sql(
                engine,
                event_id=event_id,
                raw=duplicate_query_raw,
                companions=0,
                administrator_id=administrator_id,
            ),
            "unknown": observe_scan_sql(
                engine,
                event_id=event_id,
                raw=generate_qr_credential(),
                companions=0,
                administrator_id=administrator_id,
            ),
            "wrong_event": observe_scan_sql(
                engine,
                event_id=event_id,
                raw=wrong_query_raw,
                companions=0,
                administrator_id=administrator_id,
            ),
            "inactive": observe_scan_sql(
                engine,
                event_id=event_id,
                raw=inactive_query_raw,
                companions=0,
                administrator_id=administrator_id,
            ),
        }
        assert query_counts["valid"] <= 9
        assert query_counts["duplicate"] <= 8
        assert query_counts["unknown"] <= 4
        assert query_counts["wrong_event"] <= 4
        assert query_counts["inactive"] <= 4

        timing_raws: dict[str, list[str]] = {
            "valid": [],
            "duplicate": [],
            "companion_0": [],
            "companion_1": [],
            "companion_2": [],
        }
        with Session(engine, expire_on_commit=False) as session:
            timing_event = session.get(Event, event_id)
            assert timing_event is not None
            for case, allowed in (
                ("valid", 0),
                ("duplicate", 0),
                ("companion_0", 0),
                ("companion_1", 1),
                ("companion_2", 2),
            ):
                for _ in range(20):
                    _, _, raw = add_guest_credential(
                        session,
                        timing_event,
                        administrator_id,
                        allowed=allowed,
                    )
                    timing_raws[case].append(raw)
            session.commit()
        for raw in timing_raws["duplicate"]:
            assert (
                scan_once(
                    engine,
                    event_id=event_id,
                    raw=raw,
                    companions=0,
                    administrator_id=administrator_id,
                )
                is GateScanOutcome.ADMITTED
            )

        timing_inputs = {
            "valid": [(raw, event_id, 0) for raw in timing_raws["valid"]],
            "duplicate": [(raw, event_id, 0) for raw in timing_raws["duplicate"]],
            "unknown": [(generate_qr_credential(), event_id, 0) for _ in range(20)],
            "wrong_event": [(wrong_query_raw, event_id, 0) for _ in range(20)],
            "inactive": [(inactive_query_raw, event_id, 0) for _ in range(20)],
            "companion_0": [(raw, event_id, 0) for raw in timing_raws["companion_0"]],
            "companion_1": [(raw, event_id, 1) for raw in timing_raws["companion_1"]],
            "companion_2": [(raw, event_id, 2) for raw in timing_raws["companion_2"]],
        }
        observations: dict[str, tuple[float, float]] = {}
        for case, inputs in timing_inputs.items():
            durations: list[float] = []
            for raw, selected_event_id, companions in inputs:
                started = perf_counter()
                scan_once(
                    engine,
                    event_id=selected_event_id,
                    raw=raw,
                    companions=companions,
                    administrator_id=administrator_id,
                )
                durations.append((perf_counter() - started) * 1_000)
            observations[case] = timing_summary(durations)

        print(
            "M7_QUERY_OBSERVATION "
            + " ".join(f"{case}_sql={count}" for case, count in query_counts.items())
        )
        print(
            "M7_TIMING_OBSERVATION "
            + " ".join(
                f"{case}_median_ms={values[0]:.3f} {case}_p95_ms={values[1]:.3f}"
                for case, values in observations.items()
            )
        )
    finally:
        if cleanup_required:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "TRUNCATE TABLE gate_scan_attempts, "
                        "guest_companion_entitlements, guest_attendance_corrections, "
                        "guest_attendance_records CASCADE"
                    )
                )
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
