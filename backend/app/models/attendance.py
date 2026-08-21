"""Immutable M7 gate-scan evidence and successful attendance persistence."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.event import MAX_COMPANIONS_PER_INVITATION


class GateScanOutcome(StrEnum):
    """Closed mobile-facing outcomes for a completed meaningful M7 scan."""

    ADMITTED = "ADMITTED"
    ALREADY_CHECKED_IN = "ALREADY_CHECKED_IN"
    UNKNOWN_CREDENTIAL = "UNKNOWN_CREDENTIAL"
    WRONG_EVENT = "WRONG_EVENT"
    INACTIVE_CREDENTIAL = "INACTIVE_CREDENTIAL"
    SUPERSEDED_CREDENTIAL = "SUPERSEDED_CREDENTIAL"
    INELIGIBLE_GUEST = "INELIGIBLE_GUEST"
    COMPANION_LIMIT_EXCEEDED = "COMPANION_LIMIT_EXCEEDED"


class AttendancePersistenceError(ValueError):
    """Raised when ORM workflows try to rewrite immutable M7 evidence."""


class GuestAttendanceRecord(Base):
    """One immutable successful base check-in for one event-scoped guest."""

    __tablename__ = "guest_attendance_records"
    __table_args__ = (
        CheckConstraint(
            f"companions_admitted BETWEEN 0 AND {MAX_COMPANIONS_PER_INVITATION}",
            name="ck_guest_attendance_records_companions_bounded",
        ),
        ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_guest_attendance_records_guest_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["first_qr_credential_id", "event_id", "guest_id"],
            [
                "guest_qr_credentials.id",
                "guest_qr_credentials.event_id",
                "guest_qr_credentials.guest_id",
            ],
            name="fk_guest_attendance_records_credential_event_guest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "event_id",
            "guest_id",
            name="uq_guest_attendance_records_event_guest",
        ),
        UniqueConstraint(
            "id",
            "event_id",
            "guest_id",
            name="uq_guest_attendance_records_id_event_guest",
        ),
        Index(
            "ix_guest_attendance_records_event_checked_id",
            "event_id",
            "checked_in_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column()
    guest_id: Mapped[UUID] = mapped_column()
    first_qr_credential_id: Mapped[UUID] = mapped_column()
    companions_admitted: Mapped[int] = mapped_column(SmallInteger)
    checked_in_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    @property
    def admitted_party_size(self) -> int:
        """Return the named invitation holder plus admitted companions."""

        return 1 + self.companions_admitted


class GateScanAttempt(Base):
    """One append-only, privacy-safe record of a meaningful authenticated scan."""

    __tablename__ = "gate_scan_attempts"
    __table_args__ = (
        CheckConstraint(
            "credential_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_gate_scan_attempts_credential_fingerprint",
        ),
        CheckConstraint(
            "outcome IN ('ADMITTED', 'ALREADY_CHECKED_IN', 'UNKNOWN_CREDENTIAL', "
            "'WRONG_EVENT', 'INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL', "
            "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED')",
            name="ck_gate_scan_attempts_outcome",
        ),
        CheckConstraint(
            f"requested_companions BETWEEN 0 AND {MAX_COMPANIONS_PER_INVITATION}",
            name="ck_gate_scan_attempts_requested_companions_bounded",
        ),
        CheckConstraint(
            "(outcome IN ('ADMITTED', 'ALREADY_CHECKED_IN') "
            "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
            "AND attendance_id IS NOT NULL) OR "
            "(outcome IN ('UNKNOWN_CREDENTIAL', 'WRONG_EVENT') "
            "AND guest_id IS NULL AND qr_credential_id IS NULL AND attendance_id IS NULL) OR "
            "(outcome IN ('INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL', "
            "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED') "
            "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
            "AND attendance_id IS NULL)",
            name="ck_gate_scan_attempts_outcome_references",
        ),
        ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_gate_scan_attempts_guest_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["qr_credential_id", "event_id", "guest_id"],
            [
                "guest_qr_credentials.id",
                "guest_qr_credentials.event_id",
                "guest_qr_credentials.guest_id",
            ],
            name="fk_gate_scan_attempts_credential_event_guest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attendance_id", "event_id", "guest_id"],
            [
                "guest_attendance_records.id",
                "guest_attendance_records.event_id",
                "guest_attendance_records.guest_id",
            ],
            name="fk_gate_scan_attempts_attendance_event_guest",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_gate_scan_attempts_event_scanned_id",
            "event_id",
            "scanned_at",
            "id",
        ),
        Index(
            "ix_gate_scan_attempts_fingerprint_scanned_id",
            "credential_fingerprint",
            "scanned_at",
            "id",
        ),
        Index(
            "uq_gate_scan_attempts_one_admitted_attendance",
            "attendance_id",
            unique=True,
            postgresql_where=text("outcome = 'ADMITTED'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    guest_id: Mapped[UUID | None] = mapped_column()
    qr_credential_id: Mapped[UUID | None] = mapped_column()
    attendance_id: Mapped[UUID | None] = mapped_column()
    outcome: Mapped[GateScanOutcome] = mapped_column(
        Enum(
            GateScanOutcome,
            name="gate_scan_outcome",
            native_enum=False,
            create_constraint=False,
            length=32,
        )
    )
    requested_companions: Mapped[int] = mapped_column(SmallInteger)
    scanned_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


_ATTENDANCE_FIELDS = tuple(column.key for column in GuestAttendanceRecord.__table__.columns)
_ATTEMPT_FIELDS = tuple(column.key for column in GateScanAttempt.__table__.columns)


def _reject_changed_fields(target: object, fields: tuple[str, ...], message: str) -> None:
    state = sqlalchemy_inspect(target)
    if any(state.attrs[field].history.has_changes() for field in fields):
        raise AttendancePersistenceError(message)


@event.listens_for(GuestAttendanceRecord, "before_update")
def _reject_attendance_update(_: Any, __: Any, target: GuestAttendanceRecord) -> None:
    _reject_changed_fields(target, _ATTENDANCE_FIELDS, "attendance records are immutable")


@event.listens_for(GateScanAttempt, "before_update")
def _reject_attempt_update(_: Any, __: Any, target: GateScanAttempt) -> None:
    _reject_changed_fields(target, _ATTEMPT_FIELDS, "gate scan attempts are immutable")


@event.listens_for(GuestAttendanceRecord, "before_delete")
@event.listens_for(GateScanAttempt, "before_delete")
def _reject_evidence_delete(_: Any, __: Any, ___: object) -> None:
    raise AttendancePersistenceError("M7 gate evidence is append-only")
