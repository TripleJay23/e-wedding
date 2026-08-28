"""Bounded persistence queries for M8 attendance corrections and effective state."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.attendance import GuestAttendanceRecord
from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
    GuestAttendanceCorrection,
)
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus


@dataclass(frozen=True, slots=True)
class EventCorrectionContext:
    id: UUID
    status: EventStatus
    max_companions_per_invitation: int


@dataclass(frozen=True, slots=True)
class GuestCorrectionContext:
    id: UUID
    status: GuestStatus
    allowed_companions: int


@dataclass(frozen=True, slots=True)
class AttendanceEvidence:
    id: UUID
    companions_admitted: int
    checked_in_at: datetime


@dataclass(frozen=True, slots=True)
class CorrectionHead:
    id: UUID
    revision_number: int
    decision: AttendanceCorrectionDecision
    companions_admitted: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AttendanceStateSnapshot:
    guest_id: UUID
    attendance: AttendanceEvidence | None
    latest_correction: CorrectionHead | None


@dataclass(frozen=True, slots=True)
class CorrectionRecord:
    id: UUID
    event_id: UUID
    guest_id: UUID
    revision_number: int
    previous_correction_id: UUID | None
    decision: AttendanceCorrectionDecision
    reason_code: AttendanceCorrectionReason
    companions_admitted: int | None
    gate_attendance_record_id: UUID | None
    previous_effective_status: EffectiveAttendanceStatus
    resulting_effective_status: EffectiveAttendanceStatus
    resulting_effective_source: EffectiveAttendanceSource
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CorrectionAuditRecord(CorrectionRecord):
    reason_note: str | None


class AttendanceCorrectionRepository:
    """Use per-guest locks and projection-only reads with no QR/card dependencies."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_event_for_share(self, event_id: UUID) -> EventCorrectionContext | None:
        statement = (
            select(Event.id, Event.status, Event.max_companions_per_invitation)
            .where(Event.id == event_id)
            .with_for_update(read=True, of=Event)
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return EventCorrectionContext(row.id, row.status, row.max_companions_per_invitation)

    def get_guest_for_update(
        self,
        event_id: UUID,
        guest_id: UUID,
    ) -> GuestCorrectionContext | None:
        statement = (
            select(Guest.id, Guest.status, Guest.allowed_companions)
            .where(Guest.id == guest_id, Guest.event_id == event_id)
            .with_for_update(of=Guest)
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return GuestCorrectionContext(row.id, row.status, row.allowed_companions)

    def event_exists(self, event_id: UUID) -> bool:
        return (
            self._session.execute(select(Event.id).where(Event.id == event_id)).scalar_one_or_none()
            is not None
        )

    def guest_exists(self, event_id: UUID, guest_id: UUID) -> bool:
        statement = select(Guest.id).where(Guest.id == guest_id, Guest.event_id == event_id)
        return self._session.execute(statement).scalar_one_or_none() is not None

    def get_state_snapshot(
        self,
        event_id: UUID,
        guest_id: UUID,
    ) -> AttendanceStateSnapshot | None:
        statement = self._state_statement(event_id).where(Guest.id == guest_id)
        row = self._session.execute(statement).mappings().one_or_none()
        return None if row is None else self._state_snapshot(row)

    def list_event_state_snapshots(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[AttendanceStateSnapshot]:
        statement = self._state_statement(event_id).order_by(Guest.id).limit(limit).offset(offset)
        return [self._state_snapshot(row) for row in self._session.execute(statement).mappings()]

    def list_corrections(
        self,
        event_id: UUID,
        guest_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[CorrectionRecord]:
        statement = (
            select(*self._safe_correction_columns())
            .where(
                GuestAttendanceCorrection.event_id == event_id,
                GuestAttendanceCorrection.guest_id == guest_id,
            )
            .order_by(
                GuestAttendanceCorrection.revision_number,
                GuestAttendanceCorrection.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return [self._correction_record(row) for row in self._session.execute(statement).mappings()]

    def get_correction(
        self,
        event_id: UUID,
        correction_id: UUID,
    ) -> CorrectionRecord | None:
        statement = select(*self._safe_correction_columns()).where(
            GuestAttendanceCorrection.id == correction_id,
            GuestAttendanceCorrection.event_id == event_id,
        )
        row = self._session.execute(statement).mappings().one_or_none()
        return None if row is None else self._correction_record(row)

    def get_correction_audit_detail(
        self,
        event_id: UUID,
        correction_id: UUID,
    ) -> CorrectionAuditRecord | None:
        statement = select(
            *self._safe_correction_columns(),
            GuestAttendanceCorrection.reason_note,
        ).where(
            GuestAttendanceCorrection.id == correction_id,
            GuestAttendanceCorrection.event_id == event_id,
        )
        row = self._session.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        safe = self._correction_record(row)
        return CorrectionAuditRecord(
            id=safe.id,
            event_id=safe.event_id,
            guest_id=safe.guest_id,
            revision_number=safe.revision_number,
            previous_correction_id=safe.previous_correction_id,
            decision=safe.decision,
            reason_code=safe.reason_code,
            companions_admitted=safe.companions_admitted,
            gate_attendance_record_id=safe.gate_attendance_record_id,
            previous_effective_status=safe.previous_effective_status,
            resulting_effective_status=safe.resulting_effective_status,
            resulting_effective_source=safe.resulting_effective_source,
            created_at=safe.created_at,
            reason_note=row["reason_note"],
        )

    def add(self, correction: GuestAttendanceCorrection) -> None:
        self._session.add(correction)

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    @staticmethod
    def _state_statement(event_id: UUID):
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
        return (
            select(
                Guest.id.label("guest_id"),
                GuestAttendanceRecord.id.label("attendance_id"),
                GuestAttendanceRecord.companions_admitted.label("gate_companions_admitted"),
                GuestAttendanceRecord.checked_in_at.label("gate_checked_in_at"),
                GuestAttendanceCorrection.id.label("correction_id"),
                GuestAttendanceCorrection.revision_number.label("correction_revision"),
                GuestAttendanceCorrection.decision.label("correction_decision"),
                GuestAttendanceCorrection.companions_admitted.label("manual_companions_admitted"),
                GuestAttendanceCorrection.created_at.label("correction_created_at"),
            )
            .select_from(Guest)
            .outerjoin(
                GuestAttendanceRecord,
                and_(
                    GuestAttendanceRecord.guest_id == Guest.id,
                    GuestAttendanceRecord.event_id == Guest.event_id,
                ),
            )
            .outerjoin(
                GuestAttendanceCorrection, GuestAttendanceCorrection.id == latest_correction_id
            )
            .where(Guest.event_id == event_id)
        )

    @staticmethod
    def _state_snapshot(row) -> AttendanceStateSnapshot:
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
        return AttendanceStateSnapshot(
            guest_id=row["guest_id"],
            attendance=attendance,
            latest_correction=correction,
        )

    @staticmethod
    def _safe_correction_columns() -> tuple:
        return (
            GuestAttendanceCorrection.id,
            GuestAttendanceCorrection.event_id,
            GuestAttendanceCorrection.guest_id,
            GuestAttendanceCorrection.revision_number,
            GuestAttendanceCorrection.previous_correction_id,
            GuestAttendanceCorrection.decision,
            GuestAttendanceCorrection.reason_code,
            GuestAttendanceCorrection.companions_admitted,
            GuestAttendanceCorrection.gate_attendance_record_id,
            GuestAttendanceCorrection.previous_effective_status,
            GuestAttendanceCorrection.resulting_effective_status,
            GuestAttendanceCorrection.resulting_effective_source,
            GuestAttendanceCorrection.created_at,
        )

    @staticmethod
    def _correction_record(row) -> CorrectionRecord:
        return CorrectionRecord(
            id=row["id"],
            event_id=row["event_id"],
            guest_id=row["guest_id"],
            revision_number=row["revision_number"],
            previous_correction_id=row["previous_correction_id"],
            decision=row["decision"],
            reason_code=row["reason_code"],
            companions_admitted=row["companions_admitted"],
            gate_attendance_record_id=row["gate_attendance_record_id"],
            previous_effective_status=row["previous_effective_status"],
            resulting_effective_status=row["resulting_effective_status"],
            resulting_effective_source=row["resulting_effective_source"],
            created_at=row["created_at"],
        )
