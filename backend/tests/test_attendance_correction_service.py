"""M8 reducer, transition, privacy, and service-boundary tests."""

from dataclasses import asdict
from datetime import UTC, datetime
from inspect import signature
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.attendance import GuestCompanionEntitlement
from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
    GuestAttendanceCorrection,
    _reject_correction_delete,
    _reject_correction_update,
    _validate_correction_insert,
)
from app.models.attendance_corrections import (
    AttendanceCorrectionPersistenceError as ModelPersistenceError,
)
from app.models.event import EventStatus
from app.models.guest import GuestStatus
from app.repositories.attendance_corrections import (
    AttendanceEvidence,
    AttendanceStateSnapshot,
    CorrectionAuditRecord,
    CorrectionHead,
    CorrectionRecord,
    EventCorrectionContext,
    GuestCorrectionContext,
)
from app.services.attendance_corrections import (
    AttendanceCorrectionConflictError,
    AttendanceCorrectionPersistenceError,
    AttendanceCorrectionRequestError,
    AttendanceCorrectionResourceNotFoundError,
    AttendanceCorrectionService,
    derive_effective_attendance_state,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def make_snapshot(*, gate_companions: int | None = None) -> AttendanceStateSnapshot:
    attendance = None
    if gate_companions is not None:
        attendance = AttendanceEvidence(uuid4(), gate_companions, NOW)
    return AttendanceStateSnapshot(uuid4(), attendance, None)


class FakeCorrectionRepository:
    def __init__(
        self,
        *,
        event_status: EventStatus = EventStatus.ACTIVE,
        guest_status: GuestStatus = GuestStatus.ACTIVE,
        allowed_companions: int = 2,
        event_maximum: int = 2,
        gate_companions: int | None = None,
    ) -> None:
        self.event = EventCorrectionContext(uuid4(), event_status, event_maximum)
        self.guest = GuestCorrectionContext(uuid4(), guest_status, allowed_companions)
        attendance = (
            None if gate_companions is None else AttendanceEvidence(uuid4(), gate_companions, NOW)
        )
        self.snapshots = {self.guest.id: AttendanceStateSnapshot(self.guest.id, attendance, None)}
        self.entitlements: dict[UUID, GuestCompanionEntitlement] = {}
        if attendance is not None:
            self.entitlements[self.guest.id] = GuestCompanionEntitlement(
                id=uuid4(),
                event_id=self.event.id,
                guest_id=self.guest.id,
                primary_attendance_id=attendance.id,
                establishing_correction_id=None,
                declared_companions=gate_companions,
                companions_admitted=gate_companions,
            )
        self.gate_evidence = 0 if gate_companions is None else gate_companions
        self.corrections: list[GuestAttendanceCorrection] = []
        self.pending: GuestAttendanceCorrection | None = None
        self.pending_recorded = False
        self.commits = 0
        self.rollbacks = 0

    def get_event_for_share(self, event_id: UUID):
        return self.event if event_id == self.event.id else None

    def get_guest_for_update(self, event_id: UUID, guest_id: UUID):
        if event_id == self.event.id and guest_id == self.guest.id:
            return self.guest
        return None

    def get_entitlement_for_update(self, event_id: UUID, guest_id: UUID):
        if event_id != self.event.id:
            return None
        return self.entitlements.get(guest_id)

    def get_evidenced_companion_ceiling(
        self,
        event_id: UUID,
        guest_id: UUID,
        *,
        establishing_correction_id: UUID | None,
    ) -> int | None:
        if event_id != self.event.id or guest_id != self.guest.id:
            return None
        correction_origin = 0
        if establishing_correction_id is not None:
            correction = next(
                (
                    item
                    for item in self.corrections
                    if item.id == establishing_correction_id
                    and item.decision is AttendanceCorrectionDecision.SET_ATTENDED
                ),
                None,
            )
            if correction is None or correction.companions_admitted is None:
                return None
            correction_origin = correction.companions_admitted
        return self.gate_evidence + correction_origin

    def event_exists(self, event_id: UUID) -> bool:
        return event_id == self.event.id

    def guest_exists(self, event_id: UUID, guest_id: UUID) -> bool:
        return event_id == self.event.id and guest_id in self.snapshots

    def get_state_snapshot(self, event_id: UUID, guest_id: UUID):
        if event_id != self.event.id:
            return None
        snapshot = self.snapshots.get(guest_id)
        if snapshot is None:
            return None
        entitlement = self.entitlements.get(guest_id)
        return AttendanceStateSnapshot(
            guest_id=snapshot.guest_id,
            attendance=snapshot.attendance,
            latest_correction=snapshot.latest_correction,
            allowed_companions=self.guest.allowed_companions,
            declared_companions=(None if entitlement is None else entitlement.declared_companions),
            companions_admitted=(None if entitlement is None else entitlement.companions_admitted),
        )

    def list_event_state_snapshots(self, event_id: UUID, *, limit: int, offset: int):
        if event_id != self.event.id:
            return []
        rows = sorted(self.snapshots.values(), key=lambda item: item.guest_id)
        return rows[offset : offset + limit]

    def list_corrections(self, event_id: UUID, guest_id: UUID, *, limit: int, offset: int):
        rows = [
            self._record(item)
            for item in self.corrections
            if item.event_id == event_id and item.guest_id == guest_id
        ]
        return rows[offset : offset + limit]

    def get_correction(self, event_id: UUID, correction_id: UUID):
        for item in self.corrections:
            if item.event_id == event_id and item.id == correction_id:
                return self._record(item)
        return None

    def get_correction_audit_detail(self, event_id: UUID, correction_id: UUID):
        for item in self.corrections:
            if item.event_id == event_id and item.id == correction_id:
                safe = self._record(item)
                return CorrectionAuditRecord(**asdict(safe), reason_note=item.reason_note)
        return None

    def add(self, correction: GuestAttendanceCorrection) -> None:
        self.pending = correction
        self.pending_recorded = False

    def add_entitlement(self, entitlement: GuestCompanionEntitlement) -> None:
        self.entitlements[entitlement.guest_id] = entitlement

    def flush(self) -> None:
        if self.pending is not None and not self.pending_recorded:
            self.pending.created_at = NOW
            self.corrections.append(self.pending)
            current = self.snapshots[self.pending.guest_id]
            self.snapshots[self.pending.guest_id] = AttendanceStateSnapshot(
                guest_id=current.guest_id,
                attendance=current.attendance,
                latest_correction=CorrectionHead(
                    id=self.pending.id,
                    revision_number=self.pending.revision_number,
                    decision=self.pending.decision,
                    companions_admitted=self.pending.companions_admitted,
                    created_at=NOW,
                    resulting_effective_status=self.pending.resulting_effective_status,
                    resulting_effective_source=self.pending.resulting_effective_source,
                    resulting_declared_companions=(self.pending.resulting_declared_companions),
                    resulting_companions_admitted=(self.pending.resulting_companions_admitted),
                ),
            )
            self.pending_recorded = True

    def commit(self) -> None:
        self.commits += 1
        self.pending = None
        self.pending_recorded = False

    def rollback(self) -> None:
        self.rollbacks += 1
        self.pending = None
        self.pending_recorded = False

    @staticmethod
    def _record(item: GuestAttendanceCorrection) -> CorrectionRecord:
        return CorrectionRecord(
            id=item.id,
            event_id=item.event_id,
            guest_id=item.guest_id,
            revision_number=item.revision_number,
            previous_correction_id=item.previous_correction_id,
            decision=item.decision,
            reason_code=item.reason_code,
            companions_admitted=item.companions_admitted,
            gate_attendance_record_id=item.gate_attendance_record_id,
            previous_effective_status=item.previous_effective_status,
            resulting_effective_status=item.resulting_effective_status,
            resulting_effective_source=item.resulting_effective_source,
            created_at=item.created_at,
            previous_declared_companions=item.previous_declared_companions,
            previous_companions_admitted=item.previous_companions_admitted,
            resulting_declared_companions=item.resulting_declared_companions,
            resulting_companions_admitted=item.resulting_companions_admitted,
        )


class FailingFlushCorrectionRepository(FakeCorrectionRepository):
    def __init__(self, *, fail_on_flush: int) -> None:
        super().__init__(gate_companions=1)
        entitlement = self.entitlements[self.guest.id]
        entitlement.declared_companions = 2
        self.fail_on_flush = fail_on_flush
        self.flush_calls = 0
        self.original_entitlement = (
            entitlement.declared_companions,
            entitlement.companions_admitted,
        )

    def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_calls == self.fail_on_flush:
            raise SQLAlchemyError("synthetic persistence failure")
        super().flush()

    def rollback(self) -> None:
        entitlement = self.entitlements[self.guest.id]
        (
            entitlement.declared_companions,
            entitlement.companions_admitted,
        ) = self.original_entitlement
        self.corrections.clear()
        snapshot = self.snapshots[self.guest.id]
        self.snapshots[self.guest.id] = AttendanceStateSnapshot(
            guest_id=snapshot.guest_id,
            attendance=snapshot.attendance,
            latest_correction=None,
        )
        super().rollback()


def command(
    service: AttendanceCorrectionService,
    repository: FakeCorrectionRepository,
    decision: AttendanceCorrectionDecision,
    *,
    revision: int,
    status: EffectiveAttendanceStatus,
    companions: int | None = None,
    declared: int | None = None,
    reason: AttendanceCorrectionReason = AttendanceCorrectionReason.OPERATOR_ERROR,
    note: str | None = None,
):
    return service.record_correction(
        event_id=repository.event.id,
        guest_id=repository.guest.id,
        expected_correction_revision=revision,
        expected_effective_status=status,
        decision=decision,
        reason_code=reason,
        reason_note=note,
        companions_admitted=companions,
        administrator_id=uuid4(),
        declared_companions=declared,
    )


def test_reducer_distinguishes_none_gate_manual_and_restore() -> None:
    event_id = uuid4()
    empty = make_snapshot()
    none_state = derive_effective_attendance_state(event_id=event_id, snapshot=empty)
    assert (
        none_state.effective_status,
        none_state.effective_source,
        none_state.correction_revision,
        none_state.effective_party_size,
    ) == (
        EffectiveAttendanceStatus.NOT_ATTENDED,
        EffectiveAttendanceSource.NONE,
        0,
        0,
    )

    gated = make_snapshot(gate_companions=2)
    gate_state = derive_effective_attendance_state(event_id=event_id, snapshot=gated)
    assert gate_state.effective_status is EffectiveAttendanceStatus.ATTENDED
    assert gate_state.effective_source is EffectiveAttendanceSource.GATE
    assert gate_state.effective_companions_admitted == 2
    assert gate_state.effective_party_size == 3

    manual = AttendanceStateSnapshot(
        guest_id=gated.guest_id,
        attendance=gated.attendance,
        latest_correction=CorrectionHead(
            uuid4(),
            1,
            AttendanceCorrectionDecision.SET_NOT_ATTENDED,
            None,
            NOW,
        ),
    )
    manual_state = derive_effective_attendance_state(event_id=event_id, snapshot=manual)
    assert manual_state.effective_status is EffectiveAttendanceStatus.NOT_ATTENDED
    assert manual_state.effective_source is EffectiveAttendanceSource.MANUAL
    assert manual_state.gate_attendance_record_id == gated.attendance.id

    restored = AttendanceStateSnapshot(
        guest_id=gated.guest_id,
        attendance=gated.attendance,
        latest_correction=CorrectionHead(
            uuid4(),
            2,
            AttendanceCorrectionDecision.RESTORE_GATE_DERIVED,
            None,
            NOW,
        ),
    )
    assert derive_effective_attendance_state(
        event_id=event_id, snapshot=restored
    ) == derive_effective_attendance_state(event_id=event_id, snapshot=restored)
    assert (
        derive_effective_attendance_state(event_id=event_id, snapshot=restored).effective_source
        is EffectiveAttendanceSource.GATE
    )


def test_set_attended_set_not_attended_and_restore_without_gate_are_append_only() -> None:
    repository = FakeCorrectionRepository()
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]

    attended = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_ATTENDED,
        revision=0,
        status=EffectiveAttendanceStatus.NOT_ATTENDED,
        companions=2,
        note="  Sahihisho la msimamizi  ",
    )
    assert attended.correction.revision_number == 1
    assert attended.correction.previous_correction_id is None
    assert attended.effective_state.effective_source is EffectiveAttendanceSource.MANUAL
    assert attended.effective_state.effective_party_size == 3
    assert repository.corrections[-1].reason_note == "Sahihisho la msimamizi"

    not_attended = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_NOT_ATTENDED,
        revision=1,
        status=EffectiveAttendanceStatus.ATTENDED,
    )
    assert not_attended.correction.previous_correction_id == attended.correction.id
    assert not_attended.effective_state.effective_status is EffectiveAttendanceStatus.NOT_ATTENDED

    restored = command(
        service,
        repository,
        AttendanceCorrectionDecision.RESTORE_GATE_DERIVED,
        revision=2,
        status=EffectiveAttendanceStatus.NOT_ATTENDED,
    )
    assert restored.effective_state.effective_source is EffectiveAttendanceSource.NONE
    assert restored.effective_state.correction_revision == 3
    assert [item.revision_number for item in repository.corrections] == [1, 2, 3]
    assert repository.commits == 3


def test_restore_with_gate_returns_to_original_companion_evidence() -> None:
    repository = FakeCorrectionRepository(gate_companions=2)
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]
    suppressed = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_NOT_ATTENDED,
        revision=0,
        status=EffectiveAttendanceStatus.ATTENDED,
    )
    restored = command(
        service,
        repository,
        AttendanceCorrectionDecision.RESTORE_GATE_DERIVED,
        revision=1,
        status=EffectiveAttendanceStatus.NOT_ATTENDED,
    )

    assert suppressed.effective_state.effective_party_size == 0
    assert restored.effective_state.effective_source is EffectiveAttendanceSource.GATE
    assert restored.effective_state.effective_companions_admitted == 2
    assert restored.effective_state.gate_attendance_record_id is not None


def test_c21_current_attended_to_attended_companion_change_is_not_supported() -> None:
    repository = FakeCorrectionRepository(gate_companions=1)
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]
    before = repository.snapshots[repository.guest.id].attendance

    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            service,
            repository,
            AttendanceCorrectionDecision.SET_ATTENDED,
            revision=0,
            status=EffectiveAttendanceStatus.ATTENDED,
            companions=2,
        )

    after = repository.snapshots[repository.guest.id].attendance
    assert after is before
    assert after is not None
    assert after.companions_admitted == 1
    assert repository.corrections == []


def _has_declaration_correction_capability() -> bool:
    has_decision = "SET_COMPANION_STATE" in AttendanceCorrectionDecision.__members__
    parameters = signature(AttendanceCorrectionService.record_correction).parameters
    has_declared_value = any(
        "declared" in parameter_name and "companion" in parameter_name
        for parameter_name in parameters
    )
    has_persisted_value = any(
        "declared" in column.name and "companion" in column.name
        for column in GuestAttendanceCorrection.__table__.columns
    )
    return has_decision and has_declared_value and has_persisted_value


def test_c21_target_authorized_declaration_increase() -> None:
    repository = FakeCorrectionRepository()
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]
    command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_ATTENDED,
        revision=0,
        status=EffectiveAttendanceStatus.NOT_ATTENDED,
        companions=0,
    )

    result = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_COMPANION_STATE,
        revision=1,
        status=EffectiveAttendanceStatus.ATTENDED,
        declared=2,
        companions=0,
    )

    assert _has_declaration_correction_capability()
    assert result.correction.previous_declared_companions == 0
    assert result.correction.resulting_declared_companions == 2
    assert result.effective_state.remaining_companion_slots == 2


def test_c21_target_authorized_declaration_decrease() -> None:
    repository = FakeCorrectionRepository(gate_companions=1)
    entitlement = repository.entitlements[repository.guest.id]
    entitlement.declared_companions = 2
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]

    result = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_COMPANION_STATE,
        revision=0,
        status=EffectiveAttendanceStatus.ATTENDED,
        declared=1,
        companions=1,
    )

    assert _has_declaration_correction_capability()
    assert result.effective_state.declared_companions == 1
    assert result.effective_state.companions_admitted == 1


def test_c21_target_declaration_decrease_below_admitted_is_rejected() -> None:
    repository = FakeCorrectionRepository(gate_companions=1)
    entitlement = repository.entitlements[repository.guest.id]
    entitlement.declared_companions = 2
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]

    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            service,
            repository,
            AttendanceCorrectionDecision.SET_COMPANION_STATE,
            revision=0,
            status=EffectiveAttendanceStatus.ATTENDED,
            declared=0,
            companions=1,
        )

    assert entitlement.declared_companions == 2
    assert entitlement.companions_admitted == 1
    assert repository.corrections == []


def test_companion_correction_rejects_noop_missing_entitlement_and_unevidenced_increase() -> None:
    no_op_repository = FakeCorrectionRepository(gate_companions=1)
    no_op_service = AttendanceCorrectionService(no_op_repository)  # type: ignore[arg-type]
    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            no_op_service,
            no_op_repository,
            AttendanceCorrectionDecision.SET_COMPANION_STATE,
            revision=0,
            status=EffectiveAttendanceStatus.ATTENDED,
            declared=1,
            companions=1,
        )

    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            no_op_service,
            no_op_repository,
            AttendanceCorrectionDecision.SET_COMPANION_STATE,
            revision=0,
            status=EffectiveAttendanceStatus.ATTENDED,
            declared=2,
            companions=2,
        )

    missing_repository = FakeCorrectionRepository(gate_companions=1)
    missing_repository.entitlements.clear()
    missing_service = AttendanceCorrectionService(missing_repository)  # type: ignore[arg-type]
    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            missing_service,
            missing_repository,
            AttendanceCorrectionDecision.SET_COMPANION_STATE,
            revision=0,
            status=EffectiveAttendanceStatus.ATTENDED,
            declared=2,
            companions=1,
        )

    assert no_op_repository.corrections == []
    assert missing_repository.corrections == []


def test_companion_correction_can_reduce_then_restore_to_immutable_evidence() -> None:
    repository = FakeCorrectionRepository(gate_companions=2)
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]

    reduced = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_COMPANION_STATE,
        revision=0,
        status=EffectiveAttendanceStatus.ATTENDED,
        declared=1,
        companions=1,
    )
    restored = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_COMPANION_STATE,
        revision=1,
        status=EffectiveAttendanceStatus.ATTENDED,
        declared=2,
        companions=2,
    )

    assert reduced.correction.previous_companions_admitted == 2
    assert restored.correction.previous_companions_admitted == 1
    assert restored.effective_state.companions_admitted == 2
    assert [item.revision_number for item in repository.corrections] == [1, 2]


@pytest.mark.parametrize("fail_on_flush", [1, 2])
def test_companion_correction_rolls_back_audit_and_entitlement_together(
    fail_on_flush: int,
) -> None:
    repository = FailingFlushCorrectionRepository(fail_on_flush=fail_on_flush)
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]

    with pytest.raises(AttendanceCorrectionPersistenceError):
        command(
            service,
            repository,
            AttendanceCorrectionDecision.SET_COMPANION_STATE,
            revision=0,
            status=EffectiveAttendanceStatus.ATTENDED,
            declared=1,
            companions=1,
        )

    entitlement = repository.entitlements[repository.guest.id]
    assert (entitlement.declared_companions, entitlement.companions_admitted) == (2, 1)
    assert repository.corrections == []
    assert repository.commits == 0
    assert repository.rollbacks == 1


@pytest.mark.parametrize(
    ("decision", "revision", "expected_status"),
    [
        (AttendanceCorrectionDecision.SET_NOT_ATTENDED, 0, EffectiveAttendanceStatus.NOT_ATTENDED),
        (
            AttendanceCorrectionDecision.RESTORE_GATE_DERIVED,
            0,
            EffectiveAttendanceStatus.NOT_ATTENDED,
        ),
    ],
)
def test_meaningless_initial_transitions_are_conflicts(decision, revision, expected_status) -> None:
    repository = FakeCorrectionRepository()
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]

    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            service,
            repository,
            decision,
            revision=revision,
            status=expected_status,
        )

    assert repository.corrections == []


def test_duplicate_stale_revision_and_stale_effective_status_are_conflicts() -> None:
    repository = FakeCorrectionRepository()
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]
    command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_ATTENDED,
        revision=0,
        status=EffectiveAttendanceStatus.NOT_ATTENDED,
        companions=0,
    )

    attempts = [
        (1, EffectiveAttendanceStatus.ATTENDED, AttendanceCorrectionDecision.SET_ATTENDED, 0),
        (
            0,
            EffectiveAttendanceStatus.ATTENDED,
            AttendanceCorrectionDecision.SET_NOT_ATTENDED,
            None,
        ),
        (
            1,
            EffectiveAttendanceStatus.NOT_ATTENDED,
            AttendanceCorrectionDecision.SET_NOT_ATTENDED,
            None,
        ),
    ]
    for revision, status, decision, companions in attempts:
        with pytest.raises(AttendanceCorrectionConflictError):
            command(
                service,
                repository,
                decision,
                revision=revision,
                status=status,
                companions=companions,
            )
    assert len(repository.corrections) == 1


@pytest.mark.parametrize("event_status", [EventStatus.DRAFT, EventStatus.CANCELLED])
def test_ineligible_event_states_reject_commands_but_reads_remain_available(event_status) -> None:
    repository = FakeCorrectionRepository(event_status=event_status)
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]
    assert (
        service.get_effective_state(repository.event.id, repository.guest.id).effective_source
        is EffectiveAttendanceSource.NONE
    )

    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            service,
            repository,
            AttendanceCorrectionDecision.SET_ATTENDED,
            revision=0,
            status=EffectiveAttendanceStatus.NOT_ATTENDED,
            companions=0,
        )


@pytest.mark.parametrize("event_status", [EventStatus.ACTIVE, EventStatus.COMPLETED])
def test_active_and_completed_events_accept_corrections(event_status) -> None:
    repository = FakeCorrectionRepository(event_status=event_status)
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]
    result = command(
        service,
        repository,
        AttendanceCorrectionDecision.SET_ATTENDED,
        revision=0,
        status=EffectiveAttendanceStatus.NOT_ATTENDED,
        companions=0,
    )
    assert result.effective_state.effective_status is EffectiveAttendanceStatus.ATTENDED


def test_inactive_guest_and_companion_policy_block_only_manual_set_attended() -> None:
    inactive = FakeCorrectionRepository(guest_status=GuestStatus.INACTIVE)
    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            AttendanceCorrectionService(inactive),  # type: ignore[arg-type]
            inactive,
            AttendanceCorrectionDecision.SET_ATTENDED,
            revision=0,
            status=EffectiveAttendanceStatus.NOT_ATTENDED,
            companions=0,
        )

    limited = FakeCorrectionRepository(allowed_companions=1)
    with pytest.raises(AttendanceCorrectionConflictError):
        command(
            AttendanceCorrectionService(limited),  # type: ignore[arg-type]
            limited,
            AttendanceCorrectionDecision.SET_ATTENDED,
            revision=0,
            status=EffectiveAttendanceStatus.NOT_ATTENDED,
            companions=2,
        )

    inactive_with_gate = FakeCorrectionRepository(
        guest_status=GuestStatus.INACTIVE,
        gate_companions=0,
    )
    result = command(
        AttendanceCorrectionService(inactive_with_gate),  # type: ignore[arg-type]
        inactive_with_gate,
        AttendanceCorrectionDecision.SET_NOT_ATTENDED,
        revision=0,
        status=EffectiveAttendanceStatus.ATTENDED,
    )
    assert result.effective_state.effective_status is EffectiveAttendanceStatus.NOT_ATTENDED


@pytest.mark.parametrize(
    ("reason", "note"),
    [
        (AttendanceCorrectionReason.OTHER, None),
        (AttendanceCorrectionReason.OTHER, "   "),
        (AttendanceCorrectionReason.OPERATOR_ERROR, "unsafe\x00note"),
        (AttendanceCorrectionReason.OPERATOR_ERROR, "x" * 501),
    ],
)
def test_reason_note_validation_is_bounded_and_never_echoes_values(reason, note) -> None:
    repository = FakeCorrectionRepository()
    with pytest.raises(AttendanceCorrectionRequestError) as raised:
        command(
            AttendanceCorrectionService(repository),  # type: ignore[arg-type]
            repository,
            AttendanceCorrectionDecision.SET_ATTENDED,
            revision=0,
            status=EffectiveAttendanceStatus.NOT_ATTENDED,
            companions=0,
            reason=reason,
            note=note,
        )
    assert note is None or note not in str(raised.value)
    assert repository.corrections == []


def test_read_operations_are_bounded_ordered_and_cross_event_safe() -> None:
    repository = FakeCorrectionRepository()
    second = uuid4()
    repository.snapshots[second] = AttendanceStateSnapshot(second, None, None)
    service = AttendanceCorrectionService(repository)  # type: ignore[arg-type]
    states = service.list_effective_states(repository.event.id, limit=100, offset=0)
    assert [state.guest_id for state in states] == sorted([repository.guest.id, second])
    assert (
        service.list_corrections(
            repository.event.id,
            repository.guest.id,
            limit=50,
            offset=0,
        )
        == []
    )

    with pytest.raises(AttendanceCorrectionResourceNotFoundError):
        service.get_effective_state(uuid4(), repository.guest.id)
    with pytest.raises(AttendanceCorrectionResourceNotFoundError):
        service.get_correction(repository.event.id, uuid4())


def test_model_normalizes_note_hides_sensitive_fields_and_rejects_mutation() -> None:
    correction = GuestAttendanceCorrection(
        id=uuid4(),
        event_id=uuid4(),
        guest_id=uuid4(),
        revision_number=1,
        previous_correction_id=None,
        decision=AttendanceCorrectionDecision.SET_ATTENDED,
        reason_code=AttendanceCorrectionReason.OTHER,
        reason_note="  Maelezo salama  ",
        companions_admitted=0,
        gate_attendance_record_id=None,
        previous_effective_status=EffectiveAttendanceStatus.NOT_ATTENDED,
        resulting_effective_status=EffectiveAttendanceStatus.ATTENDED,
        resulting_effective_source=EffectiveAttendanceSource.MANUAL,
        corrected_by_user_id=uuid4(),
    )
    _validate_correction_insert(None, None, correction)
    assert correction.reason_note == "Maelezo salama"
    assert correction.reason_note not in repr(correction)
    assert str(correction.corrected_by_user_id) not in repr(correction)

    correction.companions_admitted = 1
    with pytest.raises(ModelPersistenceError):
        _reject_correction_update(None, None, correction)
    with pytest.raises(ModelPersistenceError):
        _reject_correction_delete(None, None, correction)
