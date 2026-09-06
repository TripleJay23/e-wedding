"""Transactional gate admission, companion entitlement, and bounded read services."""

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.attendance import (
    GateArrivalMode,
    GateInspectionState,
    GatePermittedAction,
    GateScanAttempt,
    GateScanOutcome,
    GuestAttendanceRecord,
    GuestCompanionEntitlement,
)
from app.models.attendance_corrections import EffectiveAttendanceStatus
from app.models.event import MAX_COMPANIONS_PER_INVITATION, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import hash_qr_credential
from app.repositories.attendance import AttendanceRepository
from app.services.attendance_corrections import (
    AttendanceCorrectionPersistenceError,
    EffectiveAttendanceState,
    derive_effective_attendance_state,
)


class AttendanceResourceNotFoundError(LookupError):
    """Raised when an event or attendance record does not exist."""


class AttendanceRequestError(ValueError):
    """Raised when an internal caller bypasses strict admission command bounds."""


class AttendanceIdempotencyConflictError(ValueError):
    """Raised when an event-scoped request ID is reused for another command."""

    code = "IDEMPOTENCY_KEY_REUSED"


class AttendancePersistenceError(RuntimeError):
    """Raised with a bounded message when PostgreSQL cannot record gate evidence."""


@dataclass(frozen=True, slots=True)
class GateAdmissionCommand:
    """Internal C2.3 mutation command; the public request remains unchanged."""

    request_id: UUID
    event_id: UUID
    presented_credential: str
    arrival_mode: GateArrivalMode
    declared_companions: int | None
    companions_entering_now: int
    administrator_id: UUID


@dataclass(frozen=True, slots=True)
class GateAdmissionResult:
    """Authoritative internal transition result plus legacy-safe display context."""

    request_id: UUID
    scan_attempt_id: UUID
    event_id: UUID
    guest_id: UUID | None
    outcome: GateScanOutcome
    arrival_mode: GateArrivalMode
    primary_attended: bool | None
    primary_admitted_this_scan: bool
    allowed_companions: int | None
    declared_companions: int | None
    companions_admitted: int | None
    companions_admitted_this_scan: int
    remaining_companion_slots: int | None
    people_admitted_this_scan: int
    attendance_id: UUID | None
    scanned_at: datetime
    guest_display_name: str | None
    invitation_type: str | None
    table_number: str | None
    checked_in_at: datetime | None

    @property
    def admitted(self) -> bool:
        return self.outcome is GateScanOutcome.ADMITTED


@dataclass(frozen=True, slots=True)
class GateInspectionResult:
    """Read-only, privacy-bounded projection of current gate admission state."""

    event_id: UUID
    inspection_state: GateInspectionState
    permitted_actions: list[GatePermittedAction]
    guest_id: UUID | None
    guest_display_name: str | None
    invitation_type: str | None
    table_number: str | None
    primary_attended: bool | None
    primary_admission_locked: bool | None
    allowed_companions: int | None
    declared_companions: int | None
    companions_admitted: int | None
    remaining_companion_slots: int | None


@dataclass(frozen=True, slots=True)
class GateScanResult:
    """Legacy scanner response with no credential, verifier, or contact data."""

    scan_attempt_id: UUID
    event_id: UUID
    outcome: GateScanOutcome
    admitted: bool
    already_checked_in: bool
    guest_id: UUID | None
    attendance_id: UUID | None
    guest_display_name: str | None
    invitation_type: str | None
    table_number: str | None
    allowed_companions: int | None
    companions_admitted: int | None
    admitted_party_size: int | None
    checked_in_at: datetime | None
    scanned_at: datetime


@dataclass(frozen=True, slots=True)
class AttendanceRecordView:
    """Authenticated attendance view with only gate-useful guest display data."""

    id: UUID
    event_id: UUID
    guest_id: UUID
    guest_display_name: str
    invitation_type: str
    table_number: str | None
    allowed_companions: int
    companions_admitted: int
    admitted_party_size: int
    checked_in_at: datetime


@dataclass(frozen=True, slots=True)
class GateScanAttemptView:
    """Safe scan-history view that omits verifier and credential identity."""

    id: UUID
    event_id: UUID
    guest_id: UUID | None
    attendance_id: UUID | None
    outcome: GateScanOutcome
    requested_companions: int
    scanned_at: datetime


class AttendanceService:
    """Own admission decisions, serialization, immutable evidence, and transactions."""

    def __init__(self, repository: AttendanceRepository) -> None:
        self._repository = repository

    def scan(
        self,
        *,
        event_id: UUID,
        presented_credential: str,
        companions_admitted: int,
        administrator_id: UUID,
    ) -> GateScanResult:
        """Adapt the unchanged legacy first-scan contract to the internal command."""

        result = self.admit(
            GateAdmissionCommand(
                request_id=uuid4(),
                event_id=event_id,
                presented_credential=presented_credential,
                arrival_mode=GateArrivalMode.PRIMARY,
                declared_companions=companions_admitted,
                companions_entering_now=companions_admitted,
                administrator_id=administrator_id,
            )
        )
        return self._legacy_result(result)

    def inspect(
        self,
        *,
        event_id: UUID,
        presented_credential: str,
    ) -> GateInspectionResult:
        """Read current credential and admission state without recording gate evidence."""

        fingerprint = hash_qr_credential(presented_credential)
        try:
            context = self._repository.resolve_inspection_context(event_id, fingerprint)
            if context is None:
                if self._repository.get_event(event_id) is None:
                    raise AttendanceResourceNotFoundError("event not found")
                return self._inspection_result(
                    event_id=event_id,
                    inspection_state=GateInspectionState.UNKNOWN_CREDENTIAL,
                )

            credential, guest, selected_event = context
            if credential.event_id != selected_event.id:
                return self._inspection_result(
                    event_id=event_id,
                    inspection_state=GateInspectionState.WRONG_EVENT,
                )

            lifecycle_outcome = self._credential_lifecycle_outcome(credential, guest)
            if lifecycle_outcome is not None:
                return self._inspection_result(
                    event_id=event_id,
                    inspection_state=GateInspectionState(lifecycle_outcome.value),
                )
            if selected_event.status is not EventStatus.ACTIVE:
                return self._inspection_result(
                    event_id=event_id,
                    inspection_state=GateInspectionState.EVENT_NOT_ACTIVE,
                )

            attendance = self._repository.get_attendance(event_id, guest.id)
            entitlement = self._repository.get_entitlement(event_id, guest.id)
            snapshot = self._repository.get_attendance_state_snapshot(event_id, guest.id)
            if snapshot is None:
                raise AttendancePersistenceError("gate inspection state could not be read")
            state = derive_effective_attendance_state(event_id=event_id, snapshot=snapshot)
            primary_attended = self._is_attended(state)
            primary_locked = (
                attendance is not None or entitlement is not None or state.correction_revision > 0
            )

            declared = None if entitlement is None else entitlement.declared_companions
            admitted = None if entitlement is None else entitlement.companions_admitted
            remaining = None if declared is None or admitted is None else declared - admitted
            if primary_locked and not primary_attended:
                inspection_state = GateInspectionState.ADMISSION_LOCKED
                permitted_actions: list[GatePermittedAction] = []
            elif not primary_locked:
                inspection_state = GateInspectionState.READY_FOR_PRIMARY
                permitted_actions = [GatePermittedAction.PRIMARY]
            elif entitlement is None:
                raise AttendancePersistenceError("companion entitlement state is missing")
            elif remaining == 0:
                inspection_state = GateInspectionState.ENTITLEMENT_EXHAUSTED
                permitted_actions = []
            else:
                inspection_state = GateInspectionState.READY_FOR_COMPANION
                permitted_actions = [GatePermittedAction.COMPANION]

            return self._inspection_result(
                event_id=event_id,
                inspection_state=inspection_state,
                permitted_actions=permitted_actions,
                guest=guest,
                primary_attended=primary_attended,
                primary_admission_locked=primary_locked,
                declared_companions=declared,
                companions_admitted=admitted,
                remaining_companion_slots=remaining,
            )
        except AttendanceCorrectionPersistenceError as exc:
            raise AttendancePersistenceError("gate inspection state could not be read") from exc
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("gate inspection state could not be read") from exc

    def admit(self, command: GateAdmissionCommand) -> GateAdmissionResult:
        """Execute or replay one authoritative internal admission command."""

        self._validate_command(command)
        fingerprint = hash_qr_credential(command.presented_credential)
        try:
            prior = self._repository.get_attempt_by_request(command.event_id, command.request_id)
            if prior is not None:
                return self._replay_or_reject(prior, command, fingerprint)

            context = self._repository.resolve_scan_context_for_update(
                command.event_id,
                fingerprint,
            )
            if context is None:
                if self._repository.get_event(command.event_id) is None:
                    self._repository.rollback()
                    raise AttendanceResourceNotFoundError("event not found")
                return self._record_transition(
                    command=command,
                    fingerprint=fingerprint,
                    outcome=GateScanOutcome.UNKNOWN_CREDENTIAL,
                    guest=None,
                    credential=None,
                    attendance=None,
                    entitlement=None,
                    primary_attended=None,
                )

            credential, guest, selected_event = context
            concurrent_prior = self._repository.get_attempt_by_request(
                command.event_id,
                command.request_id,
            )
            if concurrent_prior is not None:
                return self._replay_or_reject(concurrent_prior, command, fingerprint)

            if credential.event_id != selected_event.id:
                return self._record_transition(
                    command=command,
                    fingerprint=fingerprint,
                    outcome=GateScanOutcome.WRONG_EVENT,
                    guest=None,
                    credential=None,
                    attendance=None,
                    entitlement=None,
                    primary_attended=None,
                )
            lifecycle_outcome = self._credential_lifecycle_outcome(credential, guest)
            if lifecycle_outcome is not None:
                return self._record_transition(
                    command=command,
                    fingerprint=fingerprint,
                    outcome=lifecycle_outcome,
                    guest=guest,
                    credential=credential,
                    attendance=None,
                    entitlement=None,
                    primary_attended=None,
                )

            attendance, entitlement, state = self._read_authoritative_state(
                selected_event.id,
                guest.id,
            )
            if selected_event.status is not EventStatus.ACTIVE:
                return self._record_transition(
                    command=command,
                    fingerprint=fingerprint,
                    outcome=GateScanOutcome.EVENT_NOT_ACTIVE,
                    guest=guest,
                    credential=credential,
                    attendance=attendance,
                    entitlement=entitlement,
                    primary_attended=self._is_attended(state),
                )
            if command.arrival_mode is GateArrivalMode.PRIMARY:
                return self._admit_primary(
                    command=command,
                    fingerprint=fingerprint,
                    event_maximum=selected_event.max_companions_per_invitation,
                    guest=guest,
                    credential=credential,
                    attendance=attendance,
                    entitlement=entitlement,
                    state=state,
                )
            return self._admit_companions(
                command=command,
                fingerprint=fingerprint,
                guest=guest,
                credential=credential,
                attendance=attendance,
                entitlement=entitlement,
                state=state,
            )
        except AttendanceIdempotencyConflictError:
            self._repository.rollback()
            raise
        except AttendanceCorrectionPersistenceError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("gate admission state could not be read") from exc
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("gate scan could not be recorded") from exc

    def list_event_attendance(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[AttendanceRecordView]:
        try:
            if self._repository.get_event(event_id) is None:
                raise AttendanceResourceNotFoundError("event not found")
            return [
                self._attendance_view(attendance, guest)
                for attendance, guest in self._repository.list_event_attendance(
                    event_id,
                    limit=limit,
                    offset=offset,
                )
            ]
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("attendance could not be read") from exc

    def get_guest_attendance(self, guest_id: UUID) -> AttendanceRecordView:
        try:
            row = self._repository.get_guest_attendance(guest_id)
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("attendance could not be read") from exc
        if row is None or row[1] is None:
            raise AttendanceResourceNotFoundError("attendance not found")
        guest, attendance = row
        return self._attendance_view(attendance, guest)

    def list_event_scan_attempts(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[GateScanAttemptView]:
        try:
            if self._repository.get_event(event_id) is None:
                raise AttendanceResourceNotFoundError("event not found")
            return [
                GateScanAttemptView(
                    id=attempt.id,
                    event_id=attempt.event_id,
                    guest_id=attempt.guest_id,
                    attendance_id=attempt.attendance_id,
                    outcome=attempt.outcome,
                    requested_companions=attempt.requested_companions,
                    scanned_at=attempt.scanned_at,
                )
                for attempt in self._repository.list_event_scan_attempts(
                    event_id,
                    limit=limit,
                    offset=offset,
                )
            ]
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("scan history could not be read") from exc

    def _admit_primary(
        self,
        *,
        command: GateAdmissionCommand,
        fingerprint: str,
        event_maximum: int,
        guest: Guest,
        credential: GuestQrCredential,
        attendance: GuestAttendanceRecord | None,
        entitlement: GuestCompanionEntitlement | None,
        state: EffectiveAttendanceState,
    ) -> GateAdmissionResult:
        if attendance is not None or entitlement is not None or self._is_attended(state):
            return self._record_transition(
                command=command,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.ALREADY_CHECKED_IN,
                guest=guest,
                credential=credential,
                attendance=attendance,
                entitlement=entitlement,
                primary_attended=self._is_attended(state),
            )

        declaration = command.declared_companions
        assert declaration is not None
        companion_limit = min(
            guest.allowed_companions,
            event_maximum,
            MAX_COMPANIONS_PER_INVITATION,
        )
        if declaration > companion_limit or command.companions_entering_now > declaration:
            return self._record_transition(
                command=command,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.COMPANION_LIMIT_EXCEEDED,
                guest=guest,
                credential=credential,
                attendance=None,
                entitlement=None,
                primary_attended=False,
            )

        attendance = GuestAttendanceRecord(
            id=uuid4(),
            event_id=command.event_id,
            guest_id=guest.id,
            first_qr_credential_id=credential.id,
            companions_admitted=command.companions_entering_now,
            checked_in_by=command.administrator_id,
        )
        entitlement = GuestCompanionEntitlement(
            id=uuid4(),
            event_id=command.event_id,
            guest_id=guest.id,
            primary_attendance_id=attendance.id,
            establishing_correction_id=None,
            declared_companions=declaration,
            companions_admitted=command.companions_entering_now,
        )
        attempt = self._build_attempt(
            command=command,
            fingerprint=fingerprint,
            outcome=GateScanOutcome.ADMITTED,
            guest=guest,
            credential=credential,
            attendance=attendance,
            primary_admitted_this_scan=True,
            companions_admitted_this_scan=command.companions_entering_now,
            declared_companions_after=declaration,
            companions_admitted_after=command.companions_entering_now,
        )
        try:
            self._repository.add_attendance(attendance)
            self._repository.flush()
            self._repository.add_entitlement(entitlement)
            self._repository.flush()
            self._repository.add_attempt(attempt)
            self._repository.commit()
        except IntegrityError as exc:
            return self._recover_idempotent_race(command, fingerprint, exc)
        return self._result_from_attempt(
            attempt,
            guest=guest,
            attendance=attendance,
            primary_attended=True,
        )

    def _admit_companions(
        self,
        *,
        command: GateAdmissionCommand,
        fingerprint: str,
        guest: Guest,
        credential: GuestQrCredential,
        attendance: GuestAttendanceRecord | None,
        entitlement: GuestCompanionEntitlement | None,
        state: EffectiveAttendanceState,
    ) -> GateAdmissionResult:
        if not self._is_attended(state):
            return self._record_transition(
                command=command,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.PRIMARY_REQUIRED,
                guest=guest,
                credential=credential,
                attendance=None,
                entitlement=entitlement,
                primary_attended=False,
            )
        if entitlement is None:
            self._repository.rollback()
            raise AttendancePersistenceError("companion entitlement state is missing")

        remaining = entitlement.declared_companions - entitlement.companions_admitted
        if remaining == 0:
            return self._record_transition(
                command=command,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED,
                guest=guest,
                credential=credential,
                attendance=attendance,
                entitlement=entitlement,
                primary_attended=True,
            )
        if command.companions_entering_now > remaining:
            return self._record_transition(
                command=command,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.INSUFFICIENT_COMPANION_SLOTS,
                guest=guest,
                credential=credential,
                attendance=attendance,
                entitlement=entitlement,
                primary_attended=True,
            )

        entitlement.companions_admitted += command.companions_entering_now
        attempt = self._build_attempt(
            command=command,
            fingerprint=fingerprint,
            outcome=GateScanOutcome.ADMITTED,
            guest=guest,
            credential=credential,
            attendance=attendance,
            primary_admitted_this_scan=False,
            companions_admitted_this_scan=command.companions_entering_now,
            declared_companions_after=entitlement.declared_companions,
            companions_admitted_after=entitlement.companions_admitted,
        )
        try:
            self._repository.add_attempt(attempt)
            self._repository.commit()
        except IntegrityError as exc:
            return self._recover_idempotent_race(command, fingerprint, exc)
        return self._result_from_attempt(
            attempt,
            guest=guest,
            attendance=attendance,
            primary_attended=True,
        )

    def _record_transition(
        self,
        *,
        command: GateAdmissionCommand,
        fingerprint: str,
        outcome: GateScanOutcome,
        guest: Guest | None,
        credential: GuestQrCredential | None,
        attendance: GuestAttendanceRecord | None,
        entitlement: GuestCompanionEntitlement | None,
        primary_attended: bool | None,
    ) -> GateAdmissionResult:
        attempt = self._build_attempt(
            command=command,
            fingerprint=fingerprint,
            outcome=outcome,
            guest=guest,
            credential=credential,
            attendance=attendance,
            primary_admitted_this_scan=False,
            companions_admitted_this_scan=0,
            declared_companions_after=(
                None if entitlement is None else entitlement.declared_companions
            ),
            companions_admitted_after=(
                None if entitlement is None else entitlement.companions_admitted
            ),
        )
        try:
            self._repository.add_attempt(attempt)
            self._repository.commit()
        except IntegrityError as exc:
            return self._recover_idempotent_race(command, fingerprint, exc)
        return self._result_from_attempt(
            attempt,
            guest=guest,
            attendance=attendance,
            primary_attended=primary_attended,
        )

    def _recover_idempotent_race(
        self,
        command: GateAdmissionCommand,
        fingerprint: str,
        error: IntegrityError,
    ) -> GateAdmissionResult:
        self._repository.rollback()
        winner = self._repository.get_attempt_by_request(command.event_id, command.request_id)
        if winner is None:
            self._repository.rollback()
            raise AttendancePersistenceError("gate scan could not be recorded") from error
        return self._replay_or_reject(winner, command, fingerprint)

    def _replay_or_reject(
        self,
        attempt: GateScanAttempt,
        command: GateAdmissionCommand,
        fingerprint: str,
    ) -> GateAdmissionResult:
        normalized_declaration = (
            command.declared_companions if command.arrival_mode is GateArrivalMode.PRIMARY else None
        )
        if (
            attempt.credential_fingerprint != fingerprint
            or attempt.arrival_mode is not command.arrival_mode
            or attempt.requested_declared_companions != normalized_declaration
            or attempt.requested_companions != command.companions_entering_now
        ):
            self._repository.rollback()
            raise AttendanceIdempotencyConflictError(AttendanceIdempotencyConflictError.code)

        guest = (
            None
            if attempt.guest_id is None
            else self._repository.get_guest(attempt.event_id, attempt.guest_id)
        )
        attendance = None
        state = None
        if guest is not None:
            if attempt.attendance_id is not None:
                candidate = self._repository.get_attendance(attempt.event_id, guest.id)
                if candidate is not None and candidate.id == attempt.attendance_id:
                    attendance = candidate
            snapshot = self._repository.get_attendance_state_snapshot(attempt.event_id, guest.id)
            if snapshot is not None:
                state = derive_effective_attendance_state(
                    event_id=attempt.event_id,
                    snapshot=snapshot,
                )
        primary_attended = self._replayed_primary_state(attempt, state)
        return self._result_from_attempt(
            attempt,
            guest=guest,
            attendance=attendance,
            primary_attended=primary_attended,
        )

    def _read_authoritative_state(
        self,
        event_id: UUID,
        guest_id: UUID,
    ) -> tuple[
        GuestAttendanceRecord | None,
        GuestCompanionEntitlement | None,
        EffectiveAttendanceState,
    ]:
        attendance = self._repository.get_attendance(event_id, guest_id)
        entitlement = self._repository.get_entitlement_for_update(event_id, guest_id)
        snapshot = self._repository.get_attendance_state_snapshot(event_id, guest_id)
        if snapshot is None:
            self._repository.rollback()
            raise AttendancePersistenceError("gate admission state could not be read")
        return (
            attendance,
            entitlement,
            derive_effective_attendance_state(event_id=event_id, snapshot=snapshot),
        )

    @staticmethod
    def _credential_lifecycle_outcome(
        credential: GuestQrCredential,
        guest: Guest,
    ) -> GateScanOutcome | None:
        if credential.status is QrCredentialStatus.INACTIVE:
            return GateScanOutcome.INACTIVE_CREDENTIAL
        if credential.status is QrCredentialStatus.SUPERSEDED:
            return GateScanOutcome.SUPERSEDED_CREDENTIAL
        if guest.status is not GuestStatus.ACTIVE:
            return GateScanOutcome.INELIGIBLE_GUEST
        return None

    @staticmethod
    def _validate_command(command: GateAdmissionCommand) -> None:
        if not isinstance(command, GateAdmissionCommand):
            raise AttendanceRequestError("admission command is invalid")
        if not all(
            isinstance(value, UUID)
            for value in (command.request_id, command.event_id, command.administrator_id)
        ):
            raise AttendanceRequestError("admission identifier is invalid")
        if (
            not isinstance(command.presented_credential, str)
            or re.fullmatch(r"[A-Z2-7]{32}", command.presented_credential) is None
        ):
            raise AttendanceRequestError("credential shape is invalid")
        if not isinstance(command.arrival_mode, GateArrivalMode):
            raise AttendanceRequestError("arrival mode is invalid")
        entering = command.companions_entering_now
        if (
            isinstance(entering, bool)
            or not isinstance(entering, int)
            or not 0 <= entering <= MAX_COMPANIONS_PER_INVITATION
        ):
            raise AttendanceRequestError("companion count is invalid")
        if command.arrival_mode is GateArrivalMode.PRIMARY:
            declaration = command.declared_companions
            if (
                isinstance(declaration, bool)
                or not isinstance(declaration, int)
                or not 0 <= declaration <= MAX_COMPANIONS_PER_INVITATION
            ):
                raise AttendanceRequestError("companion declaration is invalid")
        elif command.declared_companions is not None or entering == 0:
            raise AttendanceRequestError("companion admission command is invalid")

    @staticmethod
    def _build_attempt(
        *,
        command: GateAdmissionCommand,
        fingerprint: str,
        outcome: GateScanOutcome,
        guest: Guest | None,
        credential: GuestQrCredential | None,
        attendance: GuestAttendanceRecord | None,
        primary_admitted_this_scan: bool,
        companions_admitted_this_scan: int,
        declared_companions_after: int | None,
        companions_admitted_after: int | None,
    ) -> GateScanAttempt:
        return GateScanAttempt(
            id=uuid4(),
            event_id=command.event_id,
            credential_fingerprint=fingerprint,
            guest_id=None if guest is None else guest.id,
            qr_credential_id=None if credential is None else credential.id,
            attendance_id=None if attendance is None else attendance.id,
            outcome=outcome,
            requested_companions=command.companions_entering_now,
            request_id=command.request_id,
            arrival_mode=command.arrival_mode,
            requested_declared_companions=(
                command.declared_companions
                if command.arrival_mode is GateArrivalMode.PRIMARY
                else None
            ),
            primary_admitted_this_scan=primary_admitted_this_scan,
            companions_admitted_this_scan=companions_admitted_this_scan,
            declared_companions_after=declared_companions_after,
            companions_admitted_after=companions_admitted_after,
            scanned_by=command.administrator_id,
        )

    @staticmethod
    def _is_attended(state: EffectiveAttendanceState) -> bool:
        return state.effective_status is EffectiveAttendanceStatus.ATTENDED

    @classmethod
    def _replayed_primary_state(
        cls,
        attempt: GateScanAttempt,
        state: EffectiveAttendanceState | None,
    ) -> bool | None:
        if attempt.outcome is GateScanOutcome.ADMITTED:
            return True
        if attempt.outcome is GateScanOutcome.PRIMARY_REQUIRED:
            return False
        if attempt.outcome in {
            GateScanOutcome.ALREADY_CHECKED_IN,
            GateScanOutcome.COMPANION_ENTITLEMENT_EXHAUSTED,
            GateScanOutcome.INSUFFICIENT_COMPANION_SLOTS,
            GateScanOutcome.EVENT_NOT_ACTIVE,
        }:
            return None if state is None else cls._is_attended(state)
        return None

    @staticmethod
    def _result_from_attempt(
        attempt: GateScanAttempt,
        *,
        guest: Guest | None,
        attendance: GuestAttendanceRecord | None,
        primary_attended: bool | None,
    ) -> GateAdmissionResult:
        declared = attempt.declared_companions_after
        admitted = attempt.companions_admitted_after
        remaining = None if declared is None or admitted is None else declared - admitted
        companion_delta = attempt.companions_admitted_this_scan or 0
        people_delta = 0
        if attempt.outcome is GateScanOutcome.ADMITTED:
            people_delta = companion_delta
            if attempt.arrival_mode is GateArrivalMode.PRIMARY:
                people_delta += 1
        return GateAdmissionResult(
            request_id=attempt.request_id,
            scan_attempt_id=attempt.id,
            event_id=attempt.event_id,
            guest_id=None if guest is None else guest.id,
            outcome=attempt.outcome,
            arrival_mode=attempt.arrival_mode,
            primary_attended=primary_attended,
            primary_admitted_this_scan=bool(attempt.primary_admitted_this_scan),
            allowed_companions=None if guest is None else guest.allowed_companions,
            declared_companions=declared,
            companions_admitted=admitted,
            companions_admitted_this_scan=companion_delta,
            remaining_companion_slots=remaining,
            people_admitted_this_scan=people_delta,
            attendance_id=attempt.attendance_id,
            scanned_at=attempt.scanned_at,
            guest_display_name=None if guest is None else guest.full_name,
            invitation_type=None if guest is None else guest.invitation_type,
            table_number=None if guest is None else guest.table_number,
            checked_in_at=None if attendance is None else attendance.checked_in_at,
        )

    @staticmethod
    def _legacy_result(result: GateAdmissionResult) -> GateScanResult:
        admitted_party_size = (
            None if result.companions_admitted is None else 1 + result.companions_admitted
        )
        return GateScanResult(
            scan_attempt_id=result.scan_attempt_id,
            event_id=result.event_id,
            outcome=result.outcome,
            admitted=result.outcome is GateScanOutcome.ADMITTED,
            already_checked_in=result.outcome is GateScanOutcome.ALREADY_CHECKED_IN,
            guest_id=result.guest_id,
            attendance_id=result.attendance_id,
            guest_display_name=result.guest_display_name,
            invitation_type=result.invitation_type,
            table_number=result.table_number,
            allowed_companions=result.allowed_companions,
            companions_admitted=result.companions_admitted,
            admitted_party_size=admitted_party_size,
            checked_in_at=result.checked_in_at,
            scanned_at=result.scanned_at,
        )

    @staticmethod
    def _attendance_view(
        attendance: GuestAttendanceRecord,
        guest: Guest,
    ) -> AttendanceRecordView:
        return AttendanceRecordView(
            id=attendance.id,
            event_id=attendance.event_id,
            guest_id=attendance.guest_id,
            guest_display_name=guest.full_name,
            invitation_type=guest.invitation_type,
            table_number=guest.table_number,
            allowed_companions=guest.allowed_companions,
            companions_admitted=attendance.companions_admitted,
            admitted_party_size=attendance.admitted_party_size,
            checked_in_at=attendance.checked_in_at,
        )

    @staticmethod
    def _inspection_result(
        *,
        event_id: UUID,
        inspection_state: GateInspectionState,
        permitted_actions: list[GatePermittedAction] | None = None,
        guest: Guest | None = None,
        primary_attended: bool | None = None,
        primary_admission_locked: bool | None = None,
        declared_companions: int | None = None,
        companions_admitted: int | None = None,
        remaining_companion_slots: int | None = None,
    ) -> GateInspectionResult:
        return GateInspectionResult(
            event_id=event_id,
            inspection_state=inspection_state,
            permitted_actions=[] if permitted_actions is None else permitted_actions,
            guest_id=None if guest is None else guest.id,
            guest_display_name=None if guest is None else guest.full_name,
            invitation_type=None if guest is None else guest.invitation_type,
            table_number=None if guest is None else guest.table_number,
            primary_attended=primary_attended,
            primary_admission_locked=primary_admission_locked,
            allowed_companions=None if guest is None else guest.allowed_companions,
            declared_companions=declared_companions,
            companions_admitted=companions_admitted,
            remaining_companion_slots=remaining_companion_slots,
        )
