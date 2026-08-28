"""Single-snapshot projection queries for M9 reports and exports."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import Session

from app.models.attendance import GateScanAttempt, GateScanOutcome, GuestAttendanceRecord
from app.models.attendance_corrections import GuestAttendanceCorrection
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.repositories.attendance_corrections import (
    AttendanceEvidence,
    AttendanceStateSnapshot,
    CorrectionHead,
)


@dataclass(frozen=True, slots=True)
class ReportEventEvidence:
    id: UUID
    event_name: str
    bride_name: str
    groom_name: str
    event_datetime: datetime
    venue: str
    status: EventStatus


@dataclass(frozen=True, slots=True)
class ReportRawGateEvidence:
    real_gate_admissions: int
    duplicate_scan_attempts: int
    unknown_credential_attempts: int
    wrong_event_attempts: int
    inactive_credential_attempts: int
    superseded_credential_attempts: int
    ineligible_guest_attempts: int
    companion_limit_exceeded_attempts: int
    actual_companions_admitted_at_gate: int


@dataclass(frozen=True, slots=True)
class ReportGuestEvidence:
    id: UUID
    full_name: str
    invitation_type: str
    category: str | None
    table_number: str | None
    allowed_companions: int
    state_snapshot: AttendanceStateSnapshot
    raw_gate_attended: bool
    raw_gate_checked_in_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReportDatasetEvidence:
    event: ReportEventEvidence
    raw_gate: ReportRawGateEvidence
    guests: tuple[ReportGuestEvidence, ...]


class ReportRepository:
    """Read one bounded event snapshot without loading unrelated PII or file data."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def fetch_event_dataset(
        self,
        event_id: UUID,
        *,
        maximum_rows: int,
    ) -> ReportDatasetEvidence | None:
        """Return event, effective-state evidence, and raw M7 metrics in one SQL read."""

        latest_correction_id = (
            select(GuestAttendanceCorrection.id)
            .where(
                GuestAttendanceCorrection.event_id == Guest.event_id,
                GuestAttendanceCorrection.guest_id == Guest.id,
            )
            .order_by(
                GuestAttendanceCorrection.revision_number.desc(),
                GuestAttendanceCorrection.id.desc(),
            )
            .limit(1)
            .correlate(Guest)
            .scalar_subquery()
        )
        scan_metrics = (
            select(
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.ADMITTED)
                .label("real_gate_admissions"),
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.ALREADY_CHECKED_IN)
                .label("duplicate_scan_attempts"),
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.UNKNOWN_CREDENTIAL)
                .label("unknown_credential_attempts"),
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.WRONG_EVENT)
                .label("wrong_event_attempts"),
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.INACTIVE_CREDENTIAL)
                .label("inactive_credential_attempts"),
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.SUPERSEDED_CREDENTIAL)
                .label("superseded_credential_attempts"),
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.INELIGIBLE_GUEST)
                .label("ineligible_guest_attempts"),
                func.count()
                .filter(GateScanAttempt.outcome == GateScanOutcome.COMPANION_LIMIT_EXCEEDED)
                .label("companion_limit_exceeded_attempts"),
            )
            .where(GateScanAttempt.event_id == event_id)
            .subquery()
        )
        gate_companions = (
            select(
                func.coalesce(func.sum(GuestAttendanceRecord.companions_admitted), 0).label(
                    "actual_companions_admitted_at_gate"
                )
            )
            .where(GuestAttendanceRecord.event_id == event_id)
            .subquery()
        )
        statement = (
            select(
                Event.id.label("event_id"),
                Event.event_name,
                Event.bride_name,
                Event.groom_name,
                Event.event_datetime,
                Event.venue,
                Event.status.label("event_status"),
                Guest.id.label("guest_id"),
                Guest.full_name,
                Guest.invitation_type,
                Guest.category,
                Guest.table_number,
                Guest.allowed_companions,
                GuestAttendanceRecord.id.label("attendance_id"),
                GuestAttendanceRecord.companions_admitted.label("gate_companions_admitted"),
                GuestAttendanceRecord.checked_in_at.label("gate_checked_in_at"),
                GuestAttendanceCorrection.id.label("correction_id"),
                GuestAttendanceCorrection.revision_number.label("correction_revision"),
                GuestAttendanceCorrection.decision.label("correction_decision"),
                GuestAttendanceCorrection.companions_admitted.label("manual_companions_admitted"),
                GuestAttendanceCorrection.created_at.label("correction_created_at"),
                *scan_metrics.c,
                gate_companions.c.actual_companions_admitted_at_gate,
            )
            .select_from(Event)
            .outerjoin(
                Guest,
                and_(Guest.event_id == Event.id, Guest.status == GuestStatus.ACTIVE),
            )
            .outerjoin(
                GuestAttendanceRecord,
                and_(
                    GuestAttendanceRecord.guest_id == Guest.id,
                    GuestAttendanceRecord.event_id == Guest.event_id,
                ),
            )
            .outerjoin(
                GuestAttendanceCorrection,
                GuestAttendanceCorrection.id == latest_correction_id,
            )
            .join(scan_metrics, true())
            .join(gate_companions, true())
            .where(Event.id == event_id)
            .order_by(Guest.full_name, Guest.id)
            .limit(maximum_rows + 1)
        )
        rows = list(self._session.execute(statement).mappings())
        if not rows:
            return None

        first = rows[0]
        event = ReportEventEvidence(
            id=first["event_id"],
            event_name=first["event_name"],
            bride_name=first["bride_name"],
            groom_name=first["groom_name"],
            event_datetime=first["event_datetime"],
            venue=first["venue"],
            status=first["event_status"],
        )
        raw_gate = ReportRawGateEvidence(
            real_gate_admissions=first["real_gate_admissions"],
            duplicate_scan_attempts=first["duplicate_scan_attempts"],
            unknown_credential_attempts=first["unknown_credential_attempts"],
            wrong_event_attempts=first["wrong_event_attempts"],
            inactive_credential_attempts=first["inactive_credential_attempts"],
            superseded_credential_attempts=first["superseded_credential_attempts"],
            ineligible_guest_attempts=first["ineligible_guest_attempts"],
            companion_limit_exceeded_attempts=first["companion_limit_exceeded_attempts"],
            actual_companions_admitted_at_gate=first["actual_companions_admitted_at_gate"],
        )
        guests = tuple(self._guest_evidence(row) for row in rows if row["guest_id"] is not None)
        return ReportDatasetEvidence(event=event, raw_gate=raw_gate, guests=guests)

    def end_read(self) -> None:
        """Release the read transaction before CPU-bound export encoding."""

        self._session.rollback()

    @staticmethod
    def _guest_evidence(row) -> ReportGuestEvidence:
        attendance = None
        if row["attendance_id"] is not None:
            attendance = AttendanceEvidence(
                id=row["attendance_id"],
                companions_admitted=row["gate_companions_admitted"],
                checked_in_at=row["gate_checked_in_at"],
            )
        correction = None
        if row["correction_id"] is not None:
            correction = CorrectionHead(
                id=row["correction_id"],
                revision_number=row["correction_revision"],
                decision=row["correction_decision"],
                companions_admitted=row["manual_companions_admitted"],
                created_at=row["correction_created_at"],
            )
        return ReportGuestEvidence(
            id=row["guest_id"],
            full_name=row["full_name"],
            invitation_type=row["invitation_type"],
            category=row["category"],
            table_number=row["table_number"],
            allowed_companions=row["allowed_companions"],
            state_snapshot=AttendanceStateSnapshot(
                guest_id=row["guest_id"],
                attendance=attendance,
                latest_correction=correction,
            ),
            raw_gate_attended=attendance is not None,
            raw_gate_checked_in_at=None if attendance is None else attendance.checked_in_at,
        )
