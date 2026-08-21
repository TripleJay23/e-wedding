"""Focused M7 gate-validation service and immutable-model tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.models.attendance import (
    AttendancePersistenceError as ModelPersistenceError,
)
from app.models.attendance import (
    GateScanAttempt,
    GateScanOutcome,
    GuestAttendanceRecord,
    _reject_attempt_update,
    _reject_attendance_update,
)
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import generate_qr_credential, hash_qr_credential
from app.services.attendance import AttendanceRequestError, AttendanceService

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def make_event(*, maximum: int = 2) -> Event:
    return Event(
        id=uuid4(),
        event_name="Synthetic M7 Wedding",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=NOW,
        venue="Synthetic Hall",
        status=EventStatus.ACTIVE,
        max_companions_per_invitation=maximum,
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


def make_guest(event_id: UUID, *, allowed: int = 2, status=GuestStatus.ACTIVE) -> Guest:
    return Guest(
        id=uuid4(),
        event_id=event_id,
        full_name="Synthetic Gate Guest",
        phone_number=None,
        email=None,
        invitation_type="Family",
        allowed_companions=allowed,
        table_number="T7",
        category=None,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def make_credential(
    event_id: UUID,
    guest_id: UUID,
    raw: str,
    *,
    status=QrCredentialStatus.ACTIVE,
) -> GuestQrCredential:
    credential_id = uuid4()
    return GuestQrCredential(
        id=credential_id,
        event_id=event_id,
        guest_id=guest_id,
        token_hash=hash_qr_credential(raw),
        image_storage_key=f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png",
        image_sha256="a" * 64,
        status=status,
        replaces_credential_id=None,
        issued_by=uuid4(),
        issued_at=NOW,
        deactivated_at=NOW if status is QrCredentialStatus.INACTIVE else None,
        superseded_at=NOW if status is QrCredentialStatus.SUPERSEDED else None,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeAttendanceRepository:
    def __init__(self, events: list[Event]) -> None:
        self.events = {event.id: event for event in events}
        self.credentials: dict[str, tuple[GuestQrCredential, Guest]] = {}
        self.attendance: dict[tuple[UUID, UUID], GuestAttendanceRecord] = {}
        self.attempts: list[GateScanAttempt] = []
        self.pending_attendance: GuestAttendanceRecord | None = None
        self.commits = 0
        self.rollbacks = 0

    def resolve_scan_context_for_update(self, event_id, fingerprint):
        resolved = self.credentials.get(fingerprint)
        selected_event = self.events.get(event_id)
        if resolved is None or selected_event is None:
            return None
        return resolved[0], resolved[1], selected_event

    def get_event(self, event_id):
        return self.events.get(event_id)

    def get_attendance(self, event_id, guest_id):
        return self.attendance.get((event_id, guest_id))

    def get_guest_attendance(self, guest_id):
        for (_, persisted_guest_id), attendance in self.attendance.items():
            if persisted_guest_id == guest_id:
                guest = next(
                    item[1] for item in self.credentials.values() if item[1].id == guest_id
                )
                return guest, attendance
        return None

    def list_event_attendance(self, event_id, *, limit, offset):
        rows = []
        for (persisted_event_id, guest_id), attendance in self.attendance.items():
            if persisted_event_id == event_id:
                guest = next(
                    item[1] for item in self.credentials.values() if item[1].id == guest_id
                )
                rows.append((attendance, guest))
        return rows[offset : offset + limit]

    def list_event_scan_attempts(self, event_id, *, limit, offset):
        rows = [attempt for attempt in reversed(self.attempts) if attempt.event_id == event_id]
        return rows[offset : offset + limit]

    def add_attendance(self, attendance):
        self.pending_attendance = attendance

    def add_attempt(self, attempt):
        if getattr(attempt, "scanned_at", None) is None:
            attempt.scanned_at = NOW
        self.attempts.append(attempt)

    def flush(self):
        assert self.pending_attendance is not None
        attendance = self.pending_attendance
        if getattr(attendance, "checked_in_at", None) is None:
            attendance.checked_in_at = NOW
        if getattr(attendance, "created_at", None) is None:
            attendance.created_at = attendance.checked_in_at
        self.attendance[(attendance.event_id, attendance.guest_id)] = attendance

    def commit(self):
        self.commits += 1
        self.pending_attendance = None

    def rollback(self):
        self.rollbacks += 1
        self.pending_attendance = None


def build_service(
    *,
    credential_status=QrCredentialStatus.ACTIVE,
    guest_status=GuestStatus.ACTIVE,
    guest_allowance: int = 2,
    event_maximum: int = 2,
) -> tuple[AttendanceService, FakeAttendanceRepository, Event, Guest, GuestQrCredential, str]:
    event = make_event(maximum=event_maximum)
    guest = make_guest(event.id, allowed=guest_allowance, status=guest_status)
    raw = generate_qr_credential()
    credential = make_credential(event.id, guest.id, raw, status=credential_status)
    repository = FakeAttendanceRepository([event])
    repository.credentials[credential.token_hash] = (credential, guest)
    return AttendanceService(repository), repository, event, guest, credential, raw  # type: ignore[arg-type]


@pytest.mark.parametrize("companions", [0, 1, 2])
def test_valid_scan_records_one_attendance_and_immutable_attempt(companions: int) -> None:
    service, repository, event, guest, credential, raw = build_service()

    result = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=companions,
        administrator_id=event.created_by,
    )

    attendance = repository.attendance[(event.id, guest.id)]
    assert result.outcome is GateScanOutcome.ADMITTED
    assert result.companions_admitted == companions
    assert result.admitted_party_size == companions + 1
    assert attendance.first_qr_credential_id == credential.id
    assert repository.attempts[-1].attendance_id == attendance.id
    assert repository.attempts[-1].credential_fingerprint == hash_qr_credential(raw)
    assert repository.commits == 1


def test_duplicate_scan_returns_original_attendance_and_never_replaces_companions() -> None:
    service, repository, event, _, _, raw = build_service()
    first = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=2,
        administrator_id=event.created_by,
    )
    duplicate = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=0,
        administrator_id=event.created_by,
    )

    assert first.outcome is GateScanOutcome.ADMITTED
    assert duplicate.outcome is GateScanOutcome.ALREADY_CHECKED_IN
    assert duplicate.attendance_id == first.attendance_id
    assert duplicate.companions_admitted == 2
    assert len(repository.attendance) == 1
    assert [attempt.outcome for attempt in repository.attempts] == [
        GateScanOutcome.ADMITTED,
        GateScanOutcome.ALREADY_CHECKED_IN,
    ]


@pytest.mark.parametrize(
    ("credential_status", "guest_status", "expected"),
    [
        (QrCredentialStatus.INACTIVE, GuestStatus.ACTIVE, GateScanOutcome.INACTIVE_CREDENTIAL),
        (
            QrCredentialStatus.SUPERSEDED,
            GuestStatus.ACTIVE,
            GateScanOutcome.SUPERSEDED_CREDENTIAL,
        ),
        (QrCredentialStatus.ACTIVE, GuestStatus.INACTIVE, GateScanOutcome.INELIGIBLE_GUEST),
    ],
)
def test_lifecycle_rejections_are_distinct_and_create_no_attendance(
    credential_status,
    guest_status,
    expected,
) -> None:
    service, repository, event, guest, credential, raw = build_service(
        credential_status=credential_status,
        guest_status=guest_status,
    )

    result = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=0,
        administrator_id=event.created_by,
    )

    assert result.outcome is expected
    assert result.attendance_id is None
    assert repository.attendance == {}
    assert repository.attempts[-1].guest_id == guest.id
    assert repository.attempts[-1].qr_credential_id == credential.id


def test_unknown_and_wrong_event_attempts_do_not_disclose_cross_event_identity() -> None:
    service, repository, event, guest, credential, raw = build_service()
    other_event = make_event()
    repository.events[other_event.id] = other_event

    unknown = service.scan(
        event_id=event.id,
        presented_credential=generate_qr_credential(),
        companions_admitted=0,
        administrator_id=event.created_by,
    )
    wrong = service.scan(
        event_id=other_event.id,
        presented_credential=raw,
        companions_admitted=0,
        administrator_id=event.created_by,
    )

    assert unknown.outcome is GateScanOutcome.UNKNOWN_CREDENTIAL
    assert wrong.outcome is GateScanOutcome.WRONG_EVENT
    assert wrong.guest_id is None
    wrong_attempt = repository.attempts[-1]
    assert wrong_attempt.guest_id is None
    assert wrong_attempt.qr_credential_id is None
    assert wrong_attempt.credential_fingerprint == credential.token_hash
    assert guest.id not in {attempt.guest_id for attempt in repository.attempts}


@pytest.mark.parametrize(
    ("guest_allowance", "event_maximum", "requested"),
    [(0, 2, 1), (1, 2, 2), (2, 1, 2)],
)
def test_companion_policy_rejection_is_auditable_and_creates_no_attendance(
    guest_allowance: int,
    event_maximum: int,
    requested: int,
) -> None:
    service, repository, event, _, _, raw = build_service(
        guest_allowance=guest_allowance,
        event_maximum=event_maximum,
    )

    result = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=requested,
        administrator_id=event.created_by,
    )

    assert result.outcome is GateScanOutcome.COMPANION_LIMIT_EXCEEDED
    assert repository.attendance == {}
    assert repository.attempts[-1].requested_companions == requested


@pytest.mark.parametrize(
    ("credential", "companions"),
    [("", 0), ("A" * 31, 0), ("A" * 33, 0), ("!" * 32, 0), ("A" * 32, -1), ("A" * 32, 3)],
)
def test_service_boundary_rejects_malformed_inputs_before_repository_work(
    credential: str,
    companions: int,
) -> None:
    service, repository, event, _, _, _ = build_service()

    with pytest.raises(AttendanceRequestError):
        service.scan(
            event_id=event.id,
            presented_credential=credential,
            companions_admitted=companions,
            administrator_id=event.created_by,
        )

    assert repository.attempts == []
    assert repository.commits == 0


def test_orm_rejects_updates_and_deletes_to_m7_evidence() -> None:
    service, repository, event, guest, _, raw = build_service()
    service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=1,
        administrator_id=event.created_by,
    )
    attendance = repository.attendance[(event.id, guest.id)]
    attempt = repository.attempts[-1]

    attendance.companions_admitted = 0
    with pytest.raises(ModelPersistenceError):
        _reject_attendance_update(None, None, attendance)

    attempt.requested_companions = 0
    with pytest.raises(ModelPersistenceError):
        _reject_attempt_update(None, None, attempt)
