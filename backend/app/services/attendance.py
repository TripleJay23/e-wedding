"""M7 online gate validation, atomic admission, and bounded read services."""

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.attendance import GateScanAttempt, GateScanOutcome, GuestAttendanceRecord
from app.models.event import MAX_COMPANIONS_PER_INVITATION
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import hash_qr_credential
from app.repositories.attendance import AttendanceRepository


class AttendanceResourceNotFoundError(LookupError):
    """Raised when an M7-scoped event or attendance record does not exist."""


class AttendanceRequestError(ValueError):
    """Raised when an internal caller bypasses the M7 HTTP request bounds."""


class AttendancePersistenceError(RuntimeError):
    """Raised with a bounded message when PostgreSQL cannot record M7 evidence."""


@dataclass(frozen=True, slots=True)
class GateScanResult:
    """Small scanner response with no credential, verifier, or contact data."""

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
    """Authenticated M7 attendance view with only gate-useful guest display data."""

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
    """Safe scan-history view that deliberately omits verifier and credential identity."""

    id: UUID
    event_id: UUID
    guest_id: UUID | None
    attendance_id: UUID | None
    outcome: GateScanOutcome
    requested_companions: int
    scanned_at: datetime


class AttendanceService:
    """Own the complete M7 scan transaction and immutable evidence semantics."""

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
        """Hash once, resolve by the indexed verifier, and atomically record the outcome."""

        if (
            not isinstance(presented_credential, str)
            or re.fullmatch(r"[A-Z2-7]{32}", presented_credential) is None
        ):
            raise AttendanceRequestError("credential shape is invalid")
        if (
            isinstance(companions_admitted, bool)
            or not isinstance(companions_admitted, int)
            or not 0 <= companions_admitted <= MAX_COMPANIONS_PER_INVITATION
        ):
            raise AttendanceRequestError("companion count is invalid")
        fingerprint = hash_qr_credential(presented_credential)
        try:
            context = self._repository.resolve_scan_context_for_update(event_id, fingerprint)
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("gate scan could not be recorded") from exc
        if context is None:
            try:
                event_exists = self._repository.get_event(event_id) is not None
            except SQLAlchemyError as exc:
                self._repository.rollback()
                raise AttendancePersistenceError("gate scan could not be recorded") from exc
            if not event_exists:
                self._repository.rollback()
                raise AttendanceResourceNotFoundError("event not found")
            return self._record_attempt(
                event_id=event_id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.UNKNOWN_CREDENTIAL,
                requested_companions=companions_admitted,
                administrator_id=administrator_id,
            )

        credential, guest, selected_event = context
        if credential.event_id != selected_event.id:
            return self._record_attempt(
                event_id=selected_event.id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.WRONG_EVENT,
                requested_companions=companions_admitted,
                administrator_id=administrator_id,
            )

        if credential.status is QrCredentialStatus.INACTIVE:
            return self._record_attempt(
                event_id=selected_event.id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.INACTIVE_CREDENTIAL,
                requested_companions=companions_admitted,
                administrator_id=administrator_id,
                guest=guest,
                credential=credential,
            )
        if credential.status is QrCredentialStatus.SUPERSEDED:
            return self._record_attempt(
                event_id=selected_event.id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.SUPERSEDED_CREDENTIAL,
                requested_companions=companions_admitted,
                administrator_id=administrator_id,
                guest=guest,
                credential=credential,
            )
        if guest.status is not GuestStatus.ACTIVE:
            return self._record_attempt(
                event_id=selected_event.id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.INELIGIBLE_GUEST,
                requested_companions=companions_admitted,
                administrator_id=administrator_id,
                guest=guest,
                credential=credential,
            )

        try:
            existing = self._repository.get_attendance(selected_event.id, guest.id)
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("gate scan could not be recorded") from exc
        if existing is not None:
            return self._record_attempt(
                event_id=selected_event.id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.ALREADY_CHECKED_IN,
                requested_companions=companions_admitted,
                administrator_id=administrator_id,
                guest=guest,
                credential=credential,
                attendance=existing,
            )

        companion_limit = min(
            guest.allowed_companions,
            selected_event.max_companions_per_invitation,
            MAX_COMPANIONS_PER_INVITATION,
        )
        if companions_admitted > companion_limit:
            return self._record_attempt(
                event_id=selected_event.id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.COMPANION_LIMIT_EXCEEDED,
                requested_companions=companions_admitted,
                administrator_id=administrator_id,
                guest=guest,
                credential=credential,
            )

        return self._record_first_admission(
            event_id=selected_event.id,
            fingerprint=fingerprint,
            requested_companions=companions_admitted,
            administrator_id=administrator_id,
            guest=guest,
            credential=credential,
        )

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

    def _record_first_admission(
        self,
        *,
        event_id: UUID,
        fingerprint: str,
        requested_companions: int,
        administrator_id: UUID,
        guest: Guest,
        credential: GuestQrCredential,
    ) -> GateScanResult:
        attendance = GuestAttendanceRecord(
            id=uuid4(),
            event_id=event_id,
            guest_id=guest.id,
            first_qr_credential_id=credential.id,
            companions_admitted=requested_companions,
            checked_in_by=administrator_id,
        )
        try:
            self._repository.add_attendance(attendance)
            self._repository.flush()
            attempt = self._build_attempt(
                event_id=event_id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.ADMITTED,
                requested_companions=requested_companions,
                administrator_id=administrator_id,
                occurred_at=attendance.checked_in_at,
                guest=guest,
                credential=credential,
                attendance=attendance,
            )
            self._repository.add_attempt(attempt)
            self._repository.commit()
        except IntegrityError as exc:
            self._repository.rollback()
            winner = self._repository.get_attendance(event_id, guest.id)
            if winner is None:
                self._repository.rollback()
                raise AttendancePersistenceError("gate scan could not be recorded") from exc
            return self._record_attempt(
                event_id=event_id,
                fingerprint=fingerprint,
                outcome=GateScanOutcome.ALREADY_CHECKED_IN,
                requested_companions=requested_companions,
                administrator_id=administrator_id,
                guest=guest,
                credential=credential,
                attendance=winner,
            )
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("gate scan could not be recorded") from exc
        return self._scan_result(attempt, guest=guest, attendance=attendance)

    def _record_attempt(
        self,
        *,
        event_id: UUID,
        fingerprint: str,
        outcome: GateScanOutcome,
        requested_companions: int,
        administrator_id: UUID,
        guest: Guest | None = None,
        credential: GuestQrCredential | None = None,
        attendance: GuestAttendanceRecord | None = None,
    ) -> GateScanResult:
        attempt = self._build_attempt(
            event_id=event_id,
            fingerprint=fingerprint,
            outcome=outcome,
            requested_companions=requested_companions,
            administrator_id=administrator_id,
            occurred_at=None,
            guest=guest,
            credential=credential,
            attendance=attendance,
        )
        try:
            self._repository.add_attempt(attempt)
            self._repository.commit()
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise AttendancePersistenceError("gate scan could not be recorded") from exc
        return self._scan_result(attempt, guest=guest, attendance=attendance)

    @staticmethod
    def _build_attempt(
        *,
        event_id: UUID,
        fingerprint: str,
        outcome: GateScanOutcome,
        requested_companions: int,
        administrator_id: UUID,
        occurred_at: datetime | None,
        guest: Guest | None,
        credential: GuestQrCredential | None,
        attendance: GuestAttendanceRecord | None,
    ) -> GateScanAttempt:
        attempt = GateScanAttempt(
            id=uuid4(),
            event_id=event_id,
            credential_fingerprint=fingerprint,
            guest_id=None if guest is None else guest.id,
            qr_credential_id=None if credential is None else credential.id,
            attendance_id=None if attendance is None else attendance.id,
            outcome=outcome,
            requested_companions=requested_companions,
            scanned_by=administrator_id,
        )
        if occurred_at is not None:
            attempt.scanned_at = occurred_at
        return attempt

    @staticmethod
    def _scan_result(
        attempt: GateScanAttempt,
        *,
        guest: Guest | None,
        attendance: GuestAttendanceRecord | None,
    ) -> GateScanResult:
        return GateScanResult(
            scan_attempt_id=attempt.id,
            event_id=attempt.event_id,
            outcome=attempt.outcome,
            admitted=attempt.outcome is GateScanOutcome.ADMITTED,
            already_checked_in=attempt.outcome is GateScanOutcome.ALREADY_CHECKED_IN,
            guest_id=None if guest is None else guest.id,
            attendance_id=None if attendance is None else attendance.id,
            guest_display_name=None if guest is None else guest.full_name,
            invitation_type=None if guest is None else guest.invitation_type,
            table_number=None if guest is None else guest.table_number,
            allowed_companions=None if guest is None else guest.allowed_companions,
            companions_admitted=(None if attendance is None else attendance.companions_admitted),
            admitted_party_size=(None if attendance is None else attendance.admitted_party_size),
            checked_in_at=None if attendance is None else attendance.checked_in_at,
            scanned_at=attempt.scanned_at,
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
