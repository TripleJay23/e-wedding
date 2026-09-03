"""Synthetic query-result coverage for repository paths normally hit by PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
)
from app.models.event import EventStatus
from app.repositories.administrators import AdministratorRepository
from app.repositories.attendance import AttendanceRepository
from app.repositories.attendance_corrections import AttendanceCorrectionRepository
from app.repositories.qr import QrCredentialRepository
from app.repositories.reports import ReportRepository


class FakeResult:
    def __init__(
        self,
        *,
        scalar: Any = None,
        one: Any = None,
        rows: list[Any] | None = None,
        scalars: list[Any] | None = None,
    ) -> None:
        self.scalar = scalar
        self.one = one
        self.rows = rows or []
        self.scalar_rows = scalars or []

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def one_or_none(self) -> Any:
        return self.one

    def scalars(self) -> list[Any]:
        return self.scalar_rows

    def mappings(self) -> FakeResult:
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeSession:
    def __init__(self) -> None:
        self.result = FakeResult()
        self.get_result: Any = None
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return self.result

    def get(self, _model: type[Any], _identity: Any) -> Any:
        return self.get_result

    def add(self, value: Any) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_administrator_and_qr_repositories_delegate_all_operations() -> None:
    session = FakeSession()
    administrator_repository = AdministratorRepository(session)  # type: ignore[arg-type]
    qr_repository = QrCredentialRepository(session)  # type: ignore[arg-type]
    administrator = SimpleNamespace(id=uuid4(), username="synthetic-admin")
    guest = SimpleNamespace(id=uuid4())
    event = SimpleNamespace(id=uuid4())
    credential = SimpleNamespace(id=uuid4())

    session.result = FakeResult(scalar=administrator, scalars=[administrator])
    session.get_result = administrator
    assert administrator_repository.get_by_username("synthetic-admin") is administrator
    assert administrator_repository.get_by_id(administrator.id) is administrator
    assert administrator_repository.get_singleton() is administrator
    assert administrator_repository.list_for_update() == [administrator]
    administrator_repository.add(administrator)
    administrator_repository.commit()
    administrator_repository.rollback()

    session.result = FakeResult(one=(guest, event))
    assert qr_repository.get_guest_context_for_update(guest.id) == (guest, event)
    session.result = FakeResult(one=None)
    assert qr_repository.get_guest_context_for_update(guest.id) is None
    session.result = FakeResult(scalar=credential, scalars=[credential])
    session.get_result = event
    assert qr_repository.get_latest_for_guest(guest.id) is credential
    assert qr_repository.get_active_by_hash("a" * 64) is credential
    assert qr_repository.get_event(event.id) is event
    assert qr_repository.list_for_event(event.id, limit=10, offset=0) == [credential]
    qr_repository.add(credential)
    qr_repository.flush()
    qr_repository.commit()
    qr_repository.rollback()

    assert session.added == [administrator, credential]
    assert (session.flushes, session.commits, session.rollbacks) == (1, 2, 2)


def test_attendance_repository_projects_rows_and_delegates_transactions() -> None:
    session = FakeSession()
    repository = AttendanceRepository(session)  # type: ignore[arg-type]
    event = SimpleNamespace(id=uuid4())
    guest = SimpleNamespace(id=uuid4())
    credential = SimpleNamespace(id=uuid4())
    attendance = SimpleNamespace(id=uuid4())
    attempt = SimpleNamespace(id=uuid4())

    session.result = FakeResult(one=(credential, guest, event))
    assert repository.resolve_scan_context_for_update(event.id, "b" * 64) == (
        credential,
        guest,
        event,
    )
    session.result = FakeResult(one=None)
    assert repository.resolve_scan_context_for_update(event.id, "b" * 64) is None
    session.get_result = event
    assert repository.get_event(event.id) is event
    session.result = FakeResult(scalar=attendance)
    assert repository.get_attendance(event.id, guest.id) is attendance
    session.result = FakeResult(one=(guest, attendance))
    assert repository.get_guest_attendance(guest.id) == (guest, attendance)
    session.result = FakeResult(one=None)
    assert repository.get_guest_attendance(guest.id) is None
    session.result = FakeResult(rows=[(attendance, guest)])
    assert repository.list_event_attendance(event.id, limit=10, offset=0) == [(attendance, guest)]
    session.result = FakeResult(scalars=[attempt])
    assert repository.list_event_scan_attempts(event.id, limit=10, offset=0) == [attempt]
    repository.add_attendance(attendance)
    repository.add_attempt(attempt)
    repository.flush()
    repository.commit()
    repository.rollback()
    assert session.added == [attendance, attempt]
    assert (session.flushes, session.commits, session.rollbacks) == (1, 1, 1)


def _correction_row(*, include_evidence: bool = True) -> dict[str, Any]:
    now = datetime(2027, 6, 12, 14, 30, tzinfo=UTC)
    return {
        "id": uuid4(),
        "event_id": uuid4(),
        "guest_id": uuid4(),
        "revision_number": 1,
        "previous_correction_id": None,
        "decision": AttendanceCorrectionDecision.SET_ATTENDED,
        "reason_code": AttendanceCorrectionReason.MISSED_GATE_SCAN,
        "companions_admitted": 1,
        "gate_attendance_record_id": uuid4(),
        "previous_effective_status": EffectiveAttendanceStatus.NOT_ATTENDED,
        "resulting_effective_status": EffectiveAttendanceStatus.ATTENDED,
        "resulting_effective_source": EffectiveAttendanceSource.MANUAL,
        "created_at": now,
        "reason_note": "synthetic audit note",
        "attendance_id": uuid4() if include_evidence else None,
        "gate_companions_admitted": 1,
        "gate_checked_in_at": now,
        "correction_id": uuid4() if include_evidence else None,
        "correction_revision": 1,
        "correction_decision": AttendanceCorrectionDecision.SET_ATTENDED,
        "manual_companions_admitted": 1,
        "correction_created_at": now,
    }


def test_attendance_correction_repository_projects_safe_records() -> None:
    session = FakeSession()
    repository = AttendanceCorrectionRepository(session)  # type: ignore[arg-type]
    event_id = uuid4()
    guest_id = uuid4()
    now = datetime(2027, 6, 12, tzinfo=UTC)

    session.result = FakeResult(
        one=SimpleNamespace(
            id=event_id,
            status=EventStatus.ACTIVE,
            max_companions_per_invitation=2,
        )
    )
    assert repository.get_event_for_share(event_id).id == event_id  # type: ignore[union-attr]
    session.result = FakeResult(one=None)
    assert repository.get_event_for_share(event_id) is None
    session.result = FakeResult(
        one=SimpleNamespace(id=guest_id, status="ACTIVE", allowed_companions=2)
    )
    assert repository.get_guest_for_update(event_id, guest_id).id == guest_id  # type: ignore[union-attr]
    session.result = FakeResult(one=None)
    assert repository.get_guest_for_update(event_id, guest_id) is None

    session.result = FakeResult(scalar=event_id)
    assert repository.event_exists(event_id)
    assert repository.guest_exists(event_id, guest_id)
    session.result = FakeResult(scalar=None)
    assert not repository.event_exists(event_id)
    assert not repository.guest_exists(event_id, guest_id)

    row = _correction_row()
    empty_row = {**_correction_row(include_evidence=False), "guest_id": uuid4()}
    session.result = FakeResult(one=row)
    snapshot = repository.get_state_snapshot(event_id, guest_id)
    assert snapshot is not None
    assert snapshot.attendance is not None
    session.result = FakeResult(one=None)
    assert repository.get_state_snapshot(event_id, guest_id) is None
    session.result = FakeResult(rows=[row, empty_row])
    snapshots = repository.list_event_state_snapshots(event_id, limit=10, offset=0)
    assert snapshots[0].latest_correction is not None
    assert snapshots[1].attendance is None

    session.result = FakeResult(rows=[row], one=row)
    assert repository.list_corrections(event_id, guest_id, limit=10, offset=0)[0].id == row["id"]
    assert repository.get_correction(event_id, row["id"]).id == row["id"]  # type: ignore[union-attr]
    assert repository.get_correction_audit_detail(event_id, row["id"]).reason_note == (  # type: ignore[union-attr]
        "synthetic audit note"
    )
    session.result = FakeResult(one=None)
    assert repository.get_correction(event_id, uuid4()) is None
    assert repository.get_correction_audit_detail(event_id, uuid4()) is None

    correction = SimpleNamespace(id=uuid4(), created_at=now)
    repository.add(correction)  # type: ignore[arg-type]
    repository.flush()
    repository.commit()
    repository.rollback()
    assert session.added == [correction]
    assert (session.flushes, session.commits, session.rollbacks) == (1, 1, 1)


def test_report_repository_builds_dataset_and_handles_empty_event() -> None:
    session = FakeSession()
    repository = ReportRepository(session)  # type: ignore[arg-type]
    row = _correction_row()
    row.update(
        {
            "event_id": uuid4(),
            "event_name": "Synthetic Wedding",
            "bride_name": "Example One",
            "groom_name": "Example Two",
            "event_datetime": datetime(2027, 6, 12, tzinfo=UTC),
            "venue": "Synthetic Hall",
            "event_status": EventStatus.ACTIVE,
            "full_name": "Synthetic Guest",
            "invitation_type": "Family",
            "category": None,
            "table_number": None,
            "allowed_companions": 2,
            "real_gate_admissions": 1,
            "duplicate_scan_attempts": 0,
            "unknown_credential_attempts": 0,
            "wrong_event_attempts": 0,
            "inactive_credential_attempts": 0,
            "superseded_credential_attempts": 0,
            "ineligible_guest_attempts": 0,
            "companion_limit_exceeded_attempts": 0,
            "actual_companions_admitted_at_gate": 1,
        }
    )
    no_guest = {**row, "guest_id": None}
    session.result = FakeResult(rows=[row, no_guest])
    dataset = repository.fetch_event_dataset(row["event_id"], maximum_rows=100)
    assert dataset is not None
    assert dataset.event.event_name == "Synthetic Wedding"
    assert len(dataset.guests) == 1
    assert dataset.guests[0].raw_gate_attended

    session.result = FakeResult(rows=[])
    assert repository.fetch_event_dataset(row["event_id"], maximum_rows=100) is None
    repository.end_read()
    assert session.rollbacks == 1
