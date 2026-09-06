"""Focused M7 gate-validation service and immutable-model tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.db.base import Base
from app.models.attendance import (
    AttendancePersistenceError as ModelPersistenceError,
)
from app.models.attendance import (
    GateArrivalMode,
    GateScanAttempt,
    GateScanOutcome,
    GuestAttendanceRecord,
    GuestCompanionEntitlement,
    _reject_attempt_update,
    _reject_attendance_update,
)
from app.models.attendance_corrections import AttendanceCorrectionDecision
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import generate_qr_credential, hash_qr_credential
from app.repositories.attendance_corrections import (
    AttendanceEvidence,
    AttendanceStateSnapshot,
    CorrectionHead,
)
from app.schemas.attendance import GateScanRequest, GateScanResponse
from app.services.attendance import (
    AttendanceIdempotencyConflictError,
    AttendancePersistenceError,
    AttendanceRequestError,
    AttendanceService,
    GateAdmissionCommand,
)

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def make_event(*, maximum: int = 2, status: EventStatus = EventStatus.ACTIVE) -> Event:
    return Event(
        id=uuid4(),
        event_name="Synthetic M7 Wedding",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=NOW,
        venue="Synthetic Hall",
        status=status,
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
        self.entitlements: dict[tuple[UUID, UUID], GuestCompanionEntitlement] = {}
        self.latest_corrections: dict[tuple[UUID, UUID], CorrectionHead] = {}
        self.attempts: list[GateScanAttempt] = []
        self.pending_attendance: GuestAttendanceRecord | None = None
        self.pending_entitlement: GuestCompanionEntitlement | None = None
        self.pending_attempt: GateScanAttempt | None = None
        self.entitlement_original: tuple[GuestCompanionEntitlement, int] | None = None
        self.fail_attempt = False
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

    def get_guest(self, event_id, guest_id):
        for _, guest in self.credentials.values():
            if guest.event_id == event_id and guest.id == guest_id:
                return guest
        return None

    def get_attempt_by_request(self, event_id, request_id):
        return next(
            (
                attempt
                for attempt in self.attempts
                if attempt.event_id == event_id and attempt.request_id == request_id
            ),
            None,
        )

    def get_attendance(self, event_id, guest_id):
        return self.attendance.get((event_id, guest_id))

    def get_entitlement_for_update(self, event_id, guest_id):
        entitlement = self.entitlements.get((event_id, guest_id))
        if entitlement is not None and self.entitlement_original is None:
            self.entitlement_original = (entitlement, entitlement.companions_admitted)
        return entitlement

    def get_attendance_state_snapshot(self, event_id, guest_id):
        guest = self.get_guest(event_id, guest_id)
        if guest is None:
            return None
        attendance = self.attendance.get((event_id, guest_id))
        evidence = None
        if attendance is not None:
            evidence = AttendanceEvidence(
                id=attendance.id,
                companions_admitted=attendance.companions_admitted,
                checked_in_at=attendance.checked_in_at,
            )
        return AttendanceStateSnapshot(
            guest_id=guest_id,
            attendance=evidence,
            latest_correction=self.latest_corrections.get((event_id, guest_id)),
        )

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

    def add_entitlement(self, entitlement):
        self.pending_entitlement = entitlement

    def add_attempt(self, attempt):
        if self.fail_attempt:
            from sqlalchemy.exc import SQLAlchemyError

            raise SQLAlchemyError("synthetic immutable evidence failure")
        self.pending_attempt = attempt

    def flush(self):
        return None

    def commit(self):
        self.commits += 1
        if self.pending_attendance is not None:
            attendance = self.pending_attendance
            if getattr(attendance, "checked_in_at", None) is None:
                attendance.checked_in_at = NOW
            if getattr(attendance, "created_at", None) is None:
                attendance.created_at = attendance.checked_in_at
            self.attendance[(attendance.event_id, attendance.guest_id)] = attendance
        if self.pending_entitlement is not None:
            entitlement = self.pending_entitlement
            if getattr(entitlement, "created_at", None) is None:
                entitlement.created_at = NOW
            if getattr(entitlement, "updated_at", None) is None:
                entitlement.updated_at = NOW
            self.entitlements[(entitlement.event_id, entitlement.guest_id)] = entitlement
        if self.pending_attempt is not None:
            if getattr(self.pending_attempt, "scanned_at", None) is None:
                self.pending_attempt.scanned_at = NOW
            self.attempts.append(self.pending_attempt)
        self.pending_attendance = None
        self.pending_entitlement = None
        self.pending_attempt = None
        self.entitlement_original = None

    def rollback(self):
        self.rollbacks += 1
        if self.entitlement_original is not None:
            entitlement, admitted = self.entitlement_original
            entitlement.companions_admitted = admitted
        self.pending_attendance = None
        self.pending_entitlement = None
        self.pending_attempt = None
        self.entitlement_original = None


def build_service(
    *,
    credential_status=QrCredentialStatus.ACTIVE,
    guest_status=GuestStatus.ACTIVE,
    guest_allowance: int = 2,
    event_maximum: int = 2,
    event_status: EventStatus = EventStatus.ACTIVE,
) -> tuple[AttendanceService, FakeAttendanceRepository, Event, Guest, GuestQrCredential, str]:
    event = make_event(maximum=event_maximum, status=event_status)
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


@pytest.mark.parametrize(
    ("allowed", "requested"),
    [(0, 0), (1, 0), (1, 1), (2, 0), (2, 1), (2, 2)],
    ids=[
        "allowed_0_enter_0",
        "allowed_1_enter_0",
        "allowed_1_enter_1",
        "allowed_2_enter_0",
        "allowed_2_enter_1",
        "allowed_2_enter_2",
    ],
)
def test_c21_current_first_scan_companion_matrix(allowed: int, requested: int) -> None:
    service, repository, event, guest, _, raw = build_service(guest_allowance=allowed)

    result = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=requested,
        administrator_id=event.created_by,
    )

    attendance = repository.attendance[(event.id, guest.id)]
    assert result.outcome is GateScanOutcome.ADMITTED
    assert result.companions_admitted == requested
    assert attendance.companions_admitted == requested
    assert len(repository.attendance) == 1
    assert len(repository.attempts) == 1


def test_c21_current_state_loses_declaration_when_primary_enters_alone() -> None:
    persisted_states: dict[int, tuple[int, int, int]] = {}
    for intended_total in (0, 2):
        service, repository, event, guest, _, raw = build_service(guest_allowance=2)
        service.scan(
            event_id=event.id,
            presented_credential=raw,
            companions_admitted=0,
            administrator_id=event.created_by,
        )
        attendance = repository.attendance[(event.id, guest.id)]
        persisted_states[intended_total] = (
            guest.allowed_companions,
            attendance.companions_admitted,
            attendance.admitted_party_size,
        )

    assert persisted_states[0] == persisted_states[2] == (2, 0, 1)
    assert "declared_companions" not in GuestAttendanceRecord.__table__.columns


def test_c21_current_later_companion_arrival_is_rejected_as_already_checked_in() -> None:
    service, repository, event, guest, _, raw = build_service(guest_allowance=2)
    first = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=0,
        administrator_id=event.created_by,
    )
    later = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=1,
        administrator_id=event.created_by,
    )

    attendance = repository.attendance[(event.id, guest.id)]
    assert first.outcome is GateScanOutcome.ADMITTED
    assert later.outcome is GateScanOutcome.ALREADY_CHECKED_IN
    assert later.attendance_id == first.attendance_id
    assert attendance.companions_admitted == 0
    assert len(repository.attendance) == 1


def test_c21_current_repeated_primary_preserves_exactly_one_attendance() -> None:
    service, repository, event, guest, _, raw = build_service()
    outcomes = [
        service.scan(
            event_id=event.id,
            presented_credential=raw,
            companions_admitted=0,
            administrator_id=event.created_by,
        ).outcome
        for _ in range(2)
    ]

    assert outcomes == [GateScanOutcome.ADMITTED, GateScanOutcome.ALREADY_CHECKED_IN]
    assert list(repository.attendance) == [(event.id, guest.id)]


@pytest.mark.parametrize("status", list(EventStatus), ids=lambda status: status.value)
def test_c23_scan_enforces_active_event_lifecycle(status: EventStatus) -> None:
    service, repository, event, guest, _, raw = build_service(event_status=status)

    result = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=0,
        administrator_id=event.created_by,
    )

    expected = (
        GateScanOutcome.ADMITTED
        if status is EventStatus.ACTIVE
        else GateScanOutcome.EVENT_NOT_ACTIVE
    )
    assert result.outcome is expected
    assert ((event.id, guest.id) in repository.attendance) is (status is EventStatus.ACTIVE)


def test_c21_current_qr_regeneration_does_not_reset_guest_attendance() -> None:
    service, repository, event, guest, first_credential, first_raw = build_service()
    first = service.scan(
        event_id=event.id,
        presented_credential=first_raw,
        companions_admitted=1,
        administrator_id=event.created_by,
    )
    regenerated_raw = generate_qr_credential()
    regenerated = make_credential(event.id, guest.id, regenerated_raw)
    first_credential.status = QrCredentialStatus.SUPERSEDED
    first_credential.superseded_at = NOW
    repository.credentials[regenerated.token_hash] = (regenerated, guest)

    repeated = service.scan(
        event_id=event.id,
        presented_credential=regenerated_raw,
        companions_admitted=0,
        administrator_id=event.created_by,
    )

    attendance = repository.attendance[(event.id, guest.id)]
    assert repeated.outcome is GateScanOutcome.ALREADY_CHECKED_IN
    assert repeated.attendance_id == first.attendance_id
    assert attendance.first_qr_credential_id == first_credential.id
    assert attendance.companions_admitted == 1


def test_c22_request_id_persistence_does_not_add_domain_replay() -> None:
    service, repository, event, _, _, raw = build_service()
    for _ in range(2):
        service.scan(
            event_id=event.id,
            presented_credential=raw,
            companions_admitted=0,
            administrator_id=event.created_by,
        )

    assert "request_id" not in GateScanRequest.model_fields
    assert "request_id" in GateScanAttempt.__table__.columns
    assert len(repository.attendance) == 1
    assert len({attempt.id for attempt in repository.attempts}) == 2


def test_c22_scan_attempt_evidence_field_inventory() -> None:
    persisted_fields = {
        "request_id",
        "arrival_mode",
        "requested_declared_companions",
        "primary_admitted_this_scan",
        "companions_admitted_this_scan",
        "declared_companions_after",
        "companions_admitted_after",
    }

    assert persisted_fields <= set(GateScanAttempt.__table__.columns.keys())
    assert set(GateScanAttempt.__table__.columns.keys()) == {
        "id",
        "event_id",
        "credential_fingerprint",
        "guest_id",
        "qr_credential_id",
        "attendance_id",
        "outcome",
        "requested_companions",
        "request_id",
        "arrival_mode",
        "requested_declared_companions",
        "primary_admitted_this_scan",
        "companions_admitted_this_scan",
        "declared_companions_after",
        "companions_admitted_after",
        "scanned_by",
        "scanned_at",
    }


def test_c21_current_attendance_is_one_primary_event_per_event_guest() -> None:
    constraint_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in GuestAttendanceRecord.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("event_id", "guest_id") in constraint_columns
    assert "companions_admitted" in GuestAttendanceRecord.__table__.columns
    assert "declared_companions" not in GuestAttendanceRecord.__table__.columns


def _future_entitlement_table():
    required = {"event_id", "guest_id", "declared_companions", "companions_admitted"}
    return next(
        (table for table in Base.metadata.tables.values() if required <= set(table.columns.keys())),
        None,
    )


def _scan_command_has_arrival_mode() -> bool:
    return any(
        "arrival" in field_name and "mode" in field_name
        for field_name in GateScanRequest.model_fields
    )


def test_c21_target_declaration_information_is_preserved() -> None:
    table = _future_entitlement_table()
    assert table is not None
    service, repository, event, _, _, raw = build_service()

    service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=1,
        administrator_id=event.created_by,
    )

    assert repository.attempts[-1].requested_declared_companions == 1


@pytest.mark.xfail(
    strict=True,
    reason="C2 target: a later companion arrival consumes remaining declared entitlement",
)
def test_c21_target_later_companion_arrival_is_expressible() -> None:
    assert _scan_command_has_arrival_mode()
    assert _future_entitlement_table() is not None


@pytest.mark.xfail(
    strict=True,
    reason="C2 target: declared zero keeps a valid invitation distinct from an invalid credential",
)
def test_c21_target_declared_zero_returns_no_remaining_entitlement() -> None:
    assert _scan_command_has_arrival_mode()
    assert _future_entitlement_table() is not None
    assert any(
        "remaining" in name and "companion" in name for name in GateScanResponse.model_fields
    )


@pytest.mark.xfail(
    strict=True,
    reason="C2 target: companion before primary returns PRIMARY_REQUIRED without mutation",
)
def test_c21_target_companion_before_primary_is_rejected_distinctly() -> None:
    assert _scan_command_has_arrival_mode()
    assert "PRIMARY_REQUIRED" in GateScanOutcome.__members__


@pytest.mark.parametrize(
    "status",
    [EventStatus.DRAFT, EventStatus.COMPLETED, EventStatus.CANCELLED],
    ids=lambda status: status.value,
)
def test_c21_target_non_active_event_rejects_admission(status: EventStatus) -> None:
    service, repository, event, _, _, raw = build_service(event_status=status)

    result = service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=0,
        administrator_id=event.created_by,
    )

    assert result.outcome is not GateScanOutcome.ADMITTED
    assert repository.attendance == {}


def test_c21_target_conflicting_declarations_have_one_persistence_winner() -> None:
    table = _future_entitlement_table()
    assert table is not None
    unique_column_sets = {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert frozenset({"event_id", "guest_id"}) in unique_column_sets


def test_c21_target_last_slot_is_protected_by_persistence_invariant() -> None:
    table = _future_entitlement_table()
    assert table is not None
    check_sql = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    )
    assert "declared_companions" in check_sql
    assert "companions_admitted" in check_sql


@pytest.mark.xfail(
    strict=True,
    reason="C2 target: mutation requests carry a persisted idempotency UUID",
)
def test_c21_target_scan_mutation_supports_idempotent_replay() -> None:
    assert "request_id" in GateScanRequest.model_fields
    assert "request_id" in GateScanAttempt.__table__.columns


def test_c21_target_scan_attempt_records_entitlement_transition() -> None:
    required = {
        "arrival_mode",
        "requested_declared_companions",
        "primary_admitted_this_scan",
        "companions_admitted_this_scan",
        "declared_companions_after",
        "companions_admitted_after",
    }
    assert required <= set(GateScanAttempt.__table__.columns.keys())
    service, repository, event, _, _, raw = build_service()

    service.scan(
        event_id=event.id,
        presented_credential=raw,
        companions_admitted=1,
        administrator_id=event.created_by,
    )

    attempt = repository.attempts[-1]
    assert attempt.primary_admitted_this_scan is True
    assert attempt.declared_companions_after == 1
    assert attempt.companions_admitted_after == 1


def test_c21_target_entitlement_persistence_is_unique_per_event_guest() -> None:
    table = _future_entitlement_table()
    assert table is not None
    unique_column_sets = {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert frozenset({"event_id", "guest_id"}) in unique_column_sets


def admission_command(
    event: Event,
    raw: str,
    *,
    mode: GateArrivalMode,
    entering: int,
    declared: int | None = None,
    request_id: UUID | None = None,
) -> GateAdmissionCommand:
    return GateAdmissionCommand(
        request_id=uuid4() if request_id is None else request_id,
        event_id=event.id,
        presented_credential=raw,
        arrival_mode=mode,
        declared_companions=declared,
        companions_entering_now=entering,
        administrator_id=event.created_by,
    )


def establish_correction_origin(
    repository: FakeAttendanceRepository,
    event: Event,
    guest: Guest,
    *,
    declared: int = 2,
    admitted: int = 0,
) -> GuestCompanionEntitlement:
    correction_id = uuid4()
    entitlement = GuestCompanionEntitlement(
        id=uuid4(),
        event_id=event.id,
        guest_id=guest.id,
        primary_attendance_id=None,
        establishing_correction_id=correction_id,
        declared_companions=declared,
        companions_admitted=admitted,
        created_at=NOW,
        updated_at=NOW,
    )
    repository.entitlements[(event.id, guest.id)] = entitlement
    repository.latest_corrections[(event.id, guest.id)] = CorrectionHead(
        id=correction_id,
        revision_number=1,
        decision=AttendanceCorrectionDecision.SET_ATTENDED,
        companions_admitted=0,
        created_at=NOW,
    )
    return entitlement


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
def test_c23_primary_matrix_locks_independent_declaration(
    allowed: int,
    declared: int,
    entering: int,
) -> None:
    service, repository, event, guest, _, raw = build_service(guest_allowance=allowed)

    result = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=declared,
            entering=entering,
        )
    )

    entitlement = repository.entitlements[(event.id, guest.id)]
    attendance = repository.attendance[(event.id, guest.id)]
    assert result.outcome is GateScanOutcome.ADMITTED
    assert result.primary_attended is True
    assert result.primary_admitted_this_scan is True
    assert result.declared_companions == declared
    assert result.companions_admitted == entering
    assert result.companions_admitted_this_scan == entering
    assert result.remaining_companion_slots == declared - entering
    assert result.people_admitted_this_scan == 1 + entering
    assert entitlement.declared_companions == declared
    assert entitlement.companions_admitted == entering
    assert attendance.companions_admitted == entering


@pytest.mark.parametrize(
    ("allowed", "event_maximum", "declared", "entering"),
    [(1, 2, 2, 0), (2, 1, 2, 0), (2, 2, 1, 2)],
)
def test_c23_primary_rejects_declaration_or_entering_above_authority(
    allowed: int,
    event_maximum: int,
    declared: int,
    entering: int,
) -> None:
    service, repository, event, _, _, raw = build_service(
        guest_allowance=allowed,
        event_maximum=event_maximum,
    )

    result = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=declared,
            entering=entering,
        )
    )

    assert result.outcome is GateScanOutcome.COMPANION_LIMIT_EXCEEDED
    assert result.people_admitted_this_scan == 0
    assert repository.attendance == {}
    assert repository.entitlements == {}


@pytest.mark.parametrize(
    ("mode", "declared", "entering"),
    [
        (GateArrivalMode.PRIMARY, -1, 0),
        (GateArrivalMode.PRIMARY, 0, -1),
        (GateArrivalMode.PRIMARY, 3, 0),
        (GateArrivalMode.COMPANION, None, 0),
        (GateArrivalMode.COMPANION, 1, 1),
    ],
)
def test_c23_internal_command_rejects_invalid_values(
    mode: GateArrivalMode,
    declared: int | None,
    entering: int,
) -> None:
    service, repository, event, _, _, raw = build_service()

    with pytest.raises(AttendanceRequestError):
        service.admit(
            admission_command(
                event,
                raw,
                mode=mode,
                declared=declared,
                entering=entering,
            )
        )

    assert repository.attempts == []


def test_c23_declared_zero_establishes_primary_then_exhausts_without_qr_change() -> None:
    service, repository, event, guest, credential, raw = build_service()

    primary = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=0,
            entering=0,
        )
    )
    companion = service.admit(
        admission_command(event, raw, mode=GateArrivalMode.COMPANION, entering=1)
    )

    assert primary.outcome is GateScanOutcome.ADMITTED
    assert primary.primary_attended is True
    assert primary.remaining_companion_slots == 0
    assert companion.outcome is GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED
    assert companion.people_admitted_this_scan == 0
    assert credential.status is QrCredentialStatus.ACTIVE
    assert guest.allowed_companions == 2
    assert repository.entitlements[(event.id, guest.id)].companions_admitted == 0


def test_c23_repeated_primary_preserves_gate_origin_and_locked_declaration() -> None:
    service, repository, event, guest, _, raw = build_service()
    first = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=2,
            entering=1,
        )
    )
    repeated = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=0,
            entering=0,
        )
    )

    assert repeated.outcome is GateScanOutcome.ALREADY_CHECKED_IN
    assert repeated.attendance_id == first.attendance_id
    assert repeated.primary_attended is True
    assert repeated.primary_admitted_this_scan is False
    assert repeated.declared_companions == 2
    assert repeated.companions_admitted == 1
    assert repeated.people_admitted_this_scan == 0
    assert len(repository.attendance) == 1
    assert repository.entitlements[(event.id, guest.id)].declared_companions == 2


def test_c23_companion_before_primary_records_primary_required() -> None:
    service, repository, event, guest, credential, raw = build_service()

    result = service.admit(
        admission_command(event, raw, mode=GateArrivalMode.COMPANION, entering=1)
    )

    attempt = repository.attempts[-1]
    assert result.outcome is GateScanOutcome.PRIMARY_REQUIRED
    assert result.primary_attended is False
    assert result.attendance_id is None
    assert result.people_admitted_this_scan == 0
    assert attempt.guest_id == guest.id
    assert attempt.qr_credential_id == credential.id
    assert attempt.companions_admitted_this_scan == 0
    assert repository.attendance == {}
    assert repository.entitlements == {}


@pytest.mark.parametrize(
    ("first", "requests", "outcomes", "final_admitted"),
    [
        (0, [1, 1], [GateScanOutcome.ADMITTED, GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED], 1),
        (0, [1, 1], [GateScanOutcome.ADMITTED, GateScanOutcome.ADMITTED], 2),
        (0, [2], [GateScanOutcome.ADMITTED], 2),
        (1, [2], [GateScanOutcome.INSUFFICIENT_COMPANION_SLOTS], 1),
    ],
    ids=["one_then_exhausted", "two_sequential", "two_together", "no_partial"],
)
def test_c23_later_companion_capacity_transitions(
    first: int,
    requests: list[int],
    outcomes: list[GateScanOutcome],
    final_admitted: int,
) -> None:
    declared = 1 if outcomes[-1] is GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED else 2
    service, repository, event, guest, _, raw = build_service()
    service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=declared,
            entering=first,
        )
    )

    results = [
        service.admit(admission_command(event, raw, mode=GateArrivalMode.COMPANION, entering=count))
        for count in requests
    ]

    assert [result.outcome for result in results] == outcomes
    assert repository.entitlements[(event.id, guest.id)].companions_admitted == final_admitted
    assert len(repository.attendance) == 1
    if outcomes[-1] is GateScanOutcome.INSUFFICIENT_COMPANION_SLOTS:
        assert results[-1].companions_admitted_this_scan == 0
        assert results[-1].people_admitted_this_scan == 0


def test_c23_correction_origin_allows_companion_and_repeated_primary_without_attendance() -> None:
    service, repository, event, guest, _, raw = build_service()
    entitlement = establish_correction_origin(repository, event, guest)

    companion = service.admit(
        admission_command(event, raw, mode=GateArrivalMode.COMPANION, entering=1)
    )
    repeated = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=0,
            entering=0,
        )
    )

    assert companion.outcome is GateScanOutcome.ADMITTED
    assert companion.attendance_id is None
    assert companion.primary_attended is True
    assert companion.companions_admitted_this_scan == 1
    assert repeated.outcome is GateScanOutcome.ALREADY_CHECKED_IN
    assert repeated.attendance_id is None
    assert repeated.primary_attended is True
    assert entitlement.companions_admitted == 1
    assert repository.attendance == {}


def test_c23_effective_not_attended_blocks_companion_and_primary_override() -> None:
    service, repository, event, guest, _, raw = build_service()
    first = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=2,
            entering=0,
        )
    )
    repository.latest_corrections[(event.id, guest.id)] = CorrectionHead(
        id=uuid4(),
        revision_number=1,
        decision=AttendanceCorrectionDecision.SET_NOT_ATTENDED,
        companions_admitted=None,
        created_at=NOW,
    )

    companion = service.admit(
        admission_command(event, raw, mode=GateArrivalMode.COMPANION, entering=1)
    )
    repeated = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=1,
            entering=1,
        )
    )

    assert companion.outcome is GateScanOutcome.PRIMARY_REQUIRED
    assert companion.primary_attended is False
    assert companion.attendance_id is None
    assert repeated.outcome is GateScanOutcome.ALREADY_CHECKED_IN
    assert repeated.primary_attended is False
    assert repeated.attendance_id == first.attendance_id
    assert repository.entitlements[(event.id, guest.id)].companions_admitted == 0


def test_c23_event_policy_reduction_does_not_invalidate_locked_entitlement() -> None:
    service, repository, event, guest, _, raw = build_service()
    service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=2,
            entering=0,
        )
    )
    event.max_companions_per_invitation = 0

    result = service.admit(
        admission_command(event, raw, mode=GateArrivalMode.COMPANION, entering=2)
    )

    assert result.outcome is GateScanOutcome.ADMITTED
    assert result.companions_admitted == 2
    assert repository.entitlements[(event.id, guest.id)].declared_companions == 2


def test_c23_qr_regeneration_resolves_same_entitlement_without_reset() -> None:
    service, repository, event, guest, first_credential, first_raw = build_service()
    service.admit(
        admission_command(
            event,
            first_raw,
            mode=GateArrivalMode.PRIMARY,
            declared=2,
            entering=1,
        )
    )
    regenerated_raw = generate_qr_credential()
    regenerated = make_credential(event.id, guest.id, regenerated_raw)
    first_credential.status = QrCredentialStatus.SUPERSEDED
    repository.credentials[regenerated.token_hash] = (regenerated, guest)

    result = service.admit(
        admission_command(
            event,
            regenerated_raw,
            mode=GateArrivalMode.COMPANION,
            entering=1,
        )
    )

    assert result.outcome is GateScanOutcome.ADMITTED
    assert result.companions_admitted == 2
    assert len(repository.entitlements) == 1


@pytest.mark.parametrize("mode", [GateArrivalMode.PRIMARY, GateArrivalMode.COMPANION])
def test_c23_exact_request_replay_returns_original_transition_once(mode: GateArrivalMode) -> None:
    service, repository, event, guest, _, raw = build_service()
    if mode is GateArrivalMode.COMPANION:
        service.admit(
            admission_command(
                event,
                raw,
                mode=GateArrivalMode.PRIMARY,
                declared=2,
                entering=0,
            )
        )
    request_id = uuid4()
    command = admission_command(
        event,
        raw,
        mode=mode,
        declared=1 if mode is GateArrivalMode.PRIMARY else None,
        entering=1,
        request_id=request_id,
    )

    first = service.admit(command)
    replay = service.admit(command)

    assert replay == first
    assert replay.scan_attempt_id == first.scan_attempt_id
    assert sum(attempt.request_id == request_id for attempt in repository.attempts) == 1
    assert repository.entitlements[(event.id, guest.id)].companions_admitted == 1
    assert len(repository.attendance) == 1


@pytest.mark.parametrize("change", ["credential", "mode", "declaration", "count"])
def test_c23_request_key_reuse_with_changed_command_fails_closed(change: str) -> None:
    service, repository, event, guest, _, raw = build_service()
    request_id = uuid4()
    original = admission_command(
        event,
        raw,
        mode=GateArrivalMode.PRIMARY,
        declared=2,
        entering=0,
        request_id=request_id,
    )
    service.admit(original)
    changed_raw = generate_qr_credential()
    command = admission_command(
        event,
        changed_raw if change == "credential" else raw,
        mode=GateArrivalMode.COMPANION if change == "mode" else GateArrivalMode.PRIMARY,
        declared=(None if change == "mode" else 1 if change == "declaration" else 2),
        entering=1 if change == "count" else 0 if change != "mode" else 1,
        request_id=request_id,
    )

    with pytest.raises(AttendanceIdempotencyConflictError, match="IDEMPOTENCY_KEY_REUSED"):
        service.admit(command)

    assert len(repository.attempts) == 1
    assert repository.entitlements[(event.id, guest.id)].declared_companions == 2


def test_c23_privacy_precedence_precedes_event_lifecycle() -> None:
    service, repository, event, _, credential, raw = build_service(
        event_status=EventStatus.DRAFT,
        credential_status=QrCredentialStatus.INACTIVE,
    )

    inactive = service.admit(
        admission_command(
            event,
            raw,
            mode=GateArrivalMode.PRIMARY,
            declared=0,
            entering=0,
        )
    )
    unknown = service.admit(
        admission_command(
            event,
            generate_qr_credential(),
            mode=GateArrivalMode.PRIMARY,
            declared=0,
            entering=0,
        )
    )

    assert inactive.outcome is GateScanOutcome.INACTIVE_CREDENTIAL
    assert inactive.guest_id is not None
    assert unknown.outcome is GateScanOutcome.UNKNOWN_CREDENTIAL
    assert unknown.guest_id is None
    assert credential.status is QrCredentialStatus.INACTIVE
    assert repository.attendance == {}


@pytest.mark.parametrize("mode", [GateArrivalMode.PRIMARY, GateArrivalMode.COMPANION])
def test_c23_evidence_failure_rolls_back_physical_admission(mode: GateArrivalMode) -> None:
    service, repository, event, guest, _, raw = build_service()
    if mode is GateArrivalMode.COMPANION:
        service.admit(
            admission_command(
                event,
                raw,
                mode=GateArrivalMode.PRIMARY,
                declared=2,
                entering=0,
            )
        )
    repository.fail_attempt = True

    with pytest.raises(AttendancePersistenceError, match="could not be recorded"):
        service.admit(
            admission_command(
                event,
                raw,
                mode=mode,
                declared=1 if mode is GateArrivalMode.PRIMARY else None,
                entering=1,
            )
        )

    if mode is GateArrivalMode.PRIMARY:
        assert repository.attendance == {}
        assert repository.entitlements == {}
    else:
        assert repository.entitlements[(event.id, guest.id)].companions_admitted == 0
