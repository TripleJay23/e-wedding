"""M8 append-only attendance decisions and deterministic effective-state reduction."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
    GuestAttendanceCorrection,
    normalize_reason_note,
)
from app.models.attendance_corrections import (
    AttendanceCorrectionPersistenceError as ModelAttendanceCorrectionPersistenceError,
)
from app.models.event import MAX_COMPANIONS_PER_INVITATION, EventStatus
from app.models.guest import GuestStatus
from app.repositories.attendance_corrections import (
    AttendanceCorrectionRepository,
    AttendanceStateSnapshot,
    CorrectionAuditRecord,
    CorrectionHead,
    CorrectionRecord,
)

CORRECTION_ELIGIBLE_EVENT_STATUSES = frozenset({EventStatus.ACTIVE, EventStatus.COMPLETED})


class AttendanceCorrectionResourceNotFoundError(LookupError):
    """Raised with one safe response for missing and cross-event M8 resources."""


class AttendanceCorrectionConflictError(ValueError):
    """Raised for stale, ineligible, or meaningless correction commands."""


class AttendanceCorrectionRequestError(ValueError):
    """Raised when a non-HTTP caller bypasses strict M8 input validation."""


class AttendanceCorrectionPersistenceError(RuntimeError):
    """Raised with a bounded message when PostgreSQL cannot serve M8 state."""


@dataclass(frozen=True, slots=True)
class EffectiveAttendanceState:
    """One deterministic reduction of immutable gate evidence plus correction head."""

    event_id: UUID
    guest_id: UUID
    effective_status: EffectiveAttendanceStatus
    effective_source: EffectiveAttendanceSource
    effective_companions_admitted: int
    effective_party_size: int
    gate_attendance_record_id: UUID | None
    current_correction_id: UUID | None
    correction_revision: int
    effective_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttendanceCorrectionResult:
    correction: CorrectionRecord
    effective_state: EffectiveAttendanceState


def derive_effective_attendance_state(
    *,
    event_id: UUID,
    snapshot: AttendanceStateSnapshot,
) -> EffectiveAttendanceState:
    """Reduce persisted evidence without mutating or materializing a second truth."""

    attendance = snapshot.attendance
    correction = snapshot.latest_correction
    correction_id = None if correction is None else correction.id
    revision = 0 if correction is None else correction.revision_number

    if correction is not None and correction.decision in {
        AttendanceCorrectionDecision.SET_ATTENDED,
        AttendanceCorrectionDecision.SET_NOT_ATTENDED,
    }:
        attended = correction.decision is AttendanceCorrectionDecision.SET_ATTENDED
        companions = correction.companions_admitted if attended else 0
        if (
            isinstance(companions, bool)
            or not isinstance(companions, int)
            or not 0 <= companions <= MAX_COMPANIONS_PER_INVITATION
        ):
            raise AttendanceCorrectionPersistenceError(
                "attendance correction history is inconsistent"
            )
        return EffectiveAttendanceState(
            event_id=event_id,
            guest_id=snapshot.guest_id,
            effective_status=(
                EffectiveAttendanceStatus.ATTENDED
                if attended
                else EffectiveAttendanceStatus.NOT_ATTENDED
            ),
            effective_source=EffectiveAttendanceSource.MANUAL,
            effective_companions_admitted=companions,
            effective_party_size=1 + companions if attended else 0,
            gate_attendance_record_id=None if attendance is None else attendance.id,
            current_correction_id=correction_id,
            correction_revision=revision,
            effective_at=correction.created_at,
        )

    if attendance is not None:
        return EffectiveAttendanceState(
            event_id=event_id,
            guest_id=snapshot.guest_id,
            effective_status=EffectiveAttendanceStatus.ATTENDED,
            effective_source=EffectiveAttendanceSource.GATE,
            effective_companions_admitted=attendance.companions_admitted,
            effective_party_size=1 + attendance.companions_admitted,
            gate_attendance_record_id=attendance.id,
            current_correction_id=correction_id,
            correction_revision=revision,
            effective_at=attendance.checked_in_at,
        )

    return EffectiveAttendanceState(
        event_id=event_id,
        guest_id=snapshot.guest_id,
        effective_status=EffectiveAttendanceStatus.NOT_ATTENDED,
        effective_source=EffectiveAttendanceSource.NONE,
        effective_companions_admitted=0,
        effective_party_size=0,
        gate_attendance_record_id=None,
        current_correction_id=correction_id,
        correction_revision=revision,
        effective_at=None,
    )


class AttendanceCorrectionService:
    """Own M8 transitions, optimistic revisioning, privacy, and transactions."""

    def __init__(self, repository: AttendanceCorrectionRepository) -> None:
        self._repository = repository

    def record_correction(
        self,
        *,
        event_id: UUID,
        guest_id: UUID,
        expected_correction_revision: int,
        expected_effective_status: EffectiveAttendanceStatus,
        decision: AttendanceCorrectionDecision,
        reason_code: AttendanceCorrectionReason,
        reason_note: str | None,
        companions_admitted: int | None,
        administrator_id: UUID,
    ) -> AttendanceCorrectionResult:
        """Append one decision after locking only its event-shared and guest-exclusive rows."""

        expected_revision = self._validate_expected_revision(expected_correction_revision)
        expected_status = self._validate_expected_status(expected_effective_status)
        validated_decision = self._validate_decision(decision)
        validated_reason = self._validate_reason(reason_code)
        normalized_note = self._validate_note(reason_note, validated_reason)
        validated_companions = self._validate_companions(
            companions_admitted,
            validated_decision,
        )

        try:
            event = self._repository.get_event_for_share(event_id)
            if event is None:
                raise AttendanceCorrectionResourceNotFoundError(
                    "attendance correction resource not found"
                )
            if event.status not in CORRECTION_ELIGIBLE_EVENT_STATUSES:
                raise AttendanceCorrectionConflictError(
                    "attendance correction conflicts with current state"
                )

            guest = self._repository.get_guest_for_update(event_id, guest_id)
            if guest is None:
                raise AttendanceCorrectionResourceNotFoundError(
                    "attendance correction resource not found"
                )
            snapshot = self._repository.get_state_snapshot(event_id, guest_id)
            if snapshot is None:
                raise AttendanceCorrectionPersistenceError(
                    "attendance correction state could not be read"
                )
            state = derive_effective_attendance_state(event_id=event_id, snapshot=snapshot)
            if (
                state.correction_revision != expected_revision
                or state.effective_status is not expected_status
            ):
                raise AttendanceCorrectionConflictError(
                    "attendance correction conflicts with current state"
                )

            resulting_status, resulting_source = self._validate_transition(
                decision=validated_decision,
                state=state,
                has_gate_attendance=snapshot.attendance is not None,
            )
            if validated_decision is AttendanceCorrectionDecision.SET_ATTENDED:
                maximum = min(
                    guest.allowed_companions,
                    event.max_companions_per_invitation,
                    MAX_COMPANIONS_PER_INVITATION,
                )
                if guest.status is not GuestStatus.ACTIVE or validated_companions is None:
                    raise AttendanceCorrectionConflictError(
                        "attendance correction conflicts with current state"
                    )
                if validated_companions > maximum:
                    raise AttendanceCorrectionConflictError(
                        "attendance correction conflicts with current state"
                    )

            correction = GuestAttendanceCorrection(
                id=uuid4(),
                event_id=event_id,
                guest_id=guest_id,
                revision_number=state.correction_revision + 1,
                previous_correction_id=state.current_correction_id,
                decision=validated_decision,
                reason_code=validated_reason,
                reason_note=normalized_note,
                companions_admitted=validated_companions,
                gate_attendance_record_id=state.gate_attendance_record_id,
                previous_effective_status=state.effective_status,
                resulting_effective_status=resulting_status,
                resulting_effective_source=resulting_source,
                corrected_by_user_id=administrator_id,
            )
            self._repository.add(correction)
            self._repository.flush()
            if not isinstance(correction.created_at, datetime):
                raise AttendanceCorrectionPersistenceError(
                    "attendance correction could not be recorded"
                )
            record = self._record(correction)
            resulting_snapshot = AttendanceStateSnapshot(
                guest_id=guest_id,
                attendance=snapshot.attendance,
                latest_correction=CorrectionHead(
                    id=correction.id,
                    revision_number=correction.revision_number,
                    decision=correction.decision,
                    companions_admitted=correction.companions_admitted,
                    created_at=correction.created_at,
                ),
            )
            effective = derive_effective_attendance_state(
                event_id=event_id,
                snapshot=resulting_snapshot,
            )
            self._repository.commit()
            return AttendanceCorrectionResult(correction=record, effective_state=effective)
        except (
            AttendanceCorrectionResourceNotFoundError,
            AttendanceCorrectionConflictError,
            AttendanceCorrectionRequestError,
            AttendanceCorrectionPersistenceError,
        ):
            self._repository.rollback()
            raise
        except ModelAttendanceCorrectionPersistenceError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionPersistenceError(
                "attendance correction could not be recorded"
            ) from exc
        except IntegrityError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionConflictError(
                "attendance correction conflicts with current state"
            ) from exc
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionPersistenceError(
                "attendance correction could not be recorded"
            ) from exc

    def get_effective_state(
        self,
        event_id: UUID,
        guest_id: UUID,
    ) -> EffectiveAttendanceState:
        try:
            snapshot = self._repository.get_state_snapshot(event_id, guest_id)
            if snapshot is None:
                raise AttendanceCorrectionResourceNotFoundError(
                    "attendance correction resource not found"
                )
            return derive_effective_attendance_state(event_id=event_id, snapshot=snapshot)
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionPersistenceError(
                "attendance correction state could not be read"
            ) from exc

    def list_effective_states(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[EffectiveAttendanceState]:
        self._validate_pagination(limit, offset)
        try:
            if not self._repository.event_exists(event_id):
                raise AttendanceCorrectionResourceNotFoundError(
                    "attendance correction resource not found"
                )
            snapshots = self._repository.list_event_state_snapshots(
                event_id,
                limit=limit,
                offset=offset,
            )
            return [
                derive_effective_attendance_state(event_id=event_id, snapshot=snapshot)
                for snapshot in snapshots
            ]
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionPersistenceError(
                "attendance correction state could not be read"
            ) from exc

    def list_corrections(
        self,
        event_id: UUID,
        guest_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[CorrectionRecord]:
        self._validate_pagination(limit, offset)
        try:
            if not self._repository.guest_exists(event_id, guest_id):
                raise AttendanceCorrectionResourceNotFoundError(
                    "attendance correction resource not found"
                )
            return self._repository.list_corrections(
                event_id,
                guest_id,
                limit=limit,
                offset=offset,
            )
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionPersistenceError(
                "attendance correction history could not be read"
            ) from exc

    def get_correction(self, event_id: UUID, correction_id: UUID) -> CorrectionRecord:
        try:
            correction = self._repository.get_correction(event_id, correction_id)
            if correction is None:
                raise AttendanceCorrectionResourceNotFoundError(
                    "attendance correction resource not found"
                )
            return correction
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionPersistenceError(
                "attendance correction history could not be read"
            ) from exc

    def get_correction_audit_detail(
        self,
        event_id: UUID,
        correction_id: UUID,
    ) -> CorrectionAuditRecord:
        try:
            correction = self._repository.get_correction_audit_detail(event_id, correction_id)
            if correction is None:
                raise AttendanceCorrectionResourceNotFoundError(
                    "attendance correction resource not found"
                )
            return correction
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendanceCorrectionPersistenceError(
                "attendance correction history could not be read"
            ) from exc

    @staticmethod
    def _validate_transition(
        *,
        decision: AttendanceCorrectionDecision,
        state: EffectiveAttendanceState,
        has_gate_attendance: bool,
    ) -> tuple[EffectiveAttendanceStatus, EffectiveAttendanceSource]:
        if decision is AttendanceCorrectionDecision.SET_ATTENDED:
            if state.effective_status is not EffectiveAttendanceStatus.NOT_ATTENDED:
                raise AttendanceCorrectionConflictError(
                    "attendance correction conflicts with current state"
                )
            return EffectiveAttendanceStatus.ATTENDED, EffectiveAttendanceSource.MANUAL
        if decision is AttendanceCorrectionDecision.SET_NOT_ATTENDED:
            if state.effective_status is not EffectiveAttendanceStatus.ATTENDED:
                raise AttendanceCorrectionConflictError(
                    "attendance correction conflicts with current state"
                )
            return EffectiveAttendanceStatus.NOT_ATTENDED, EffectiveAttendanceSource.MANUAL
        if state.effective_source is not EffectiveAttendanceSource.MANUAL:
            raise AttendanceCorrectionConflictError(
                "attendance correction conflicts with current state"
            )
        if has_gate_attendance:
            return EffectiveAttendanceStatus.ATTENDED, EffectiveAttendanceSource.GATE
        return EffectiveAttendanceStatus.NOT_ATTENDED, EffectiveAttendanceSource.NONE

    @staticmethod
    def _validate_expected_revision(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AttendanceCorrectionRequestError("expected correction revision is invalid")
        return value

    @staticmethod
    def _validate_expected_status(value: object) -> EffectiveAttendanceStatus:
        if not isinstance(value, EffectiveAttendanceStatus):
            raise AttendanceCorrectionRequestError("expected effective status is invalid")
        return value

    @staticmethod
    def _validate_decision(value: object) -> AttendanceCorrectionDecision:
        if not isinstance(value, AttendanceCorrectionDecision):
            raise AttendanceCorrectionRequestError("attendance correction decision is invalid")
        return value

    @staticmethod
    def _validate_reason(value: object) -> AttendanceCorrectionReason:
        if not isinstance(value, AttendanceCorrectionReason):
            raise AttendanceCorrectionRequestError("attendance correction reason is invalid")
        return value

    @staticmethod
    def _validate_note(
        value: object | None,
        reason: AttendanceCorrectionReason,
    ) -> str | None:
        try:
            normalized = normalize_reason_note(value)
        except ModelAttendanceCorrectionPersistenceError as exc:
            raise AttendanceCorrectionRequestError("correction reason note is invalid") from exc
        if reason is AttendanceCorrectionReason.OTHER and normalized is None:
            raise AttendanceCorrectionRequestError("OTHER correction reason requires a note")
        return normalized

    @staticmethod
    def _validate_companions(
        value: object | None,
        decision: AttendanceCorrectionDecision,
    ) -> int | None:
        if decision is AttendanceCorrectionDecision.SET_ATTENDED:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_COMPANIONS_PER_INVITATION
            ):
                raise AttendanceCorrectionRequestError(
                    "attendance correction companion count is invalid"
                )
            return value
        if value is not None:
            raise AttendanceCorrectionRequestError(
                "attendance correction companion count is invalid"
            )
        return None

    @staticmethod
    def _validate_pagination(limit: object, offset: object) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or not 0 <= offset <= 100_000
        ):
            raise AttendanceCorrectionRequestError("attendance correction page is invalid")

    @staticmethod
    def _record(correction: GuestAttendanceCorrection) -> CorrectionRecord:
        return CorrectionRecord(
            id=correction.id,
            event_id=correction.event_id,
            guest_id=correction.guest_id,
            revision_number=correction.revision_number,
            previous_correction_id=correction.previous_correction_id,
            decision=correction.decision,
            reason_code=correction.reason_code,
            companions_admitted=correction.companions_admitted,
            gate_attendance_record_id=correction.gate_attendance_record_id,
            previous_effective_status=correction.previous_effective_status,
            resulting_effective_status=correction.resulting_effective_status,
            resulting_effective_source=correction.resulting_effective_source,
            created_at=correction.created_at,
        )
