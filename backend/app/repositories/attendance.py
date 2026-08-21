"""Bounded PostgreSQL persistence operations for M7 gate validation."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.attendance import GateScanAttempt, GuestAttendanceRecord
from app.models.event import Event
from app.models.guest import Guest
from app.models.qr import GuestQrCredential


class AttendanceRepository:
    """Keep the latency-sensitive scan path explicit and relationship-free."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_scan_context_for_update(
        self,
        event_id: UUID,
        credential_fingerprint: str,
    ) -> tuple[GuestQrCredential, Guest, Event] | None:
        """Resolve one verifier and serialize scans for only its guest."""

        statement = (
            select(GuestQrCredential, Guest, Event)
            .select_from(GuestQrCredential)
            .join(
                Guest,
                and_(
                    Guest.id == GuestQrCredential.guest_id,
                    Guest.event_id == GuestQrCredential.event_id,
                ),
            )
            .join(Event, Event.id == event_id)
            .where(GuestQrCredential.token_hash == credential_fingerprint)
            .with_for_update(of=Guest)
        )
        row = self._session.execute(statement).one_or_none()
        return None if row is None else (row[0], row[1], row[2])

    def get_event(self, event_id: UUID) -> Event | None:
        return self._session.get(Event, event_id)

    def get_attendance(
        self,
        event_id: UUID,
        guest_id: UUID,
    ) -> GuestAttendanceRecord | None:
        statement = select(GuestAttendanceRecord).where(
            GuestAttendanceRecord.event_id == event_id,
            GuestAttendanceRecord.guest_id == guest_id,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get_guest_attendance(
        self,
        guest_id: UUID,
    ) -> tuple[Guest, GuestAttendanceRecord | None] | None:
        statement = (
            select(Guest, GuestAttendanceRecord)
            .outerjoin(
                GuestAttendanceRecord,
                and_(
                    GuestAttendanceRecord.guest_id == Guest.id,
                    GuestAttendanceRecord.event_id == Guest.event_id,
                ),
            )
            .where(Guest.id == guest_id)
        )
        row = self._session.execute(statement).one_or_none()
        return None if row is None else (row[0], row[1])

    def list_event_attendance(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[tuple[GuestAttendanceRecord, Guest]]:
        statement = (
            select(GuestAttendanceRecord, Guest)
            .join(
                Guest,
                and_(
                    Guest.id == GuestAttendanceRecord.guest_id,
                    Guest.event_id == GuestAttendanceRecord.event_id,
                ),
            )
            .where(GuestAttendanceRecord.event_id == event_id)
            .order_by(
                GuestAttendanceRecord.checked_in_at.desc(),
                GuestAttendanceRecord.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return [(row[0], row[1]) for row in self._session.execute(statement)]

    def list_event_scan_attempts(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[GateScanAttempt]:
        statement = (
            select(GateScanAttempt)
            .where(GateScanAttempt.event_id == event_id)
            .order_by(GateScanAttempt.scanned_at.desc(), GateScanAttempt.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def add_attendance(self, attendance: GuestAttendanceRecord) -> None:
        self._session.add(attendance)

    def add_attempt(self, attempt: GateScanAttempt) -> None:
        self._session.add(attempt)

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
