"""Append-only M8 attendance-correction decisions and derived-state enums."""

import unicodedata
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
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.event import MAX_COMPANIONS_PER_INVITATION

REASON_NOTE_MAX_LENGTH = 500


class AttendanceCorrectionDecision(StrEnum):
    """The only supported append-only attendance decisions."""

    SET_ATTENDED = "SET_ATTENDED"
    SET_NOT_ATTENDED = "SET_NOT_ATTENDED"
    RESTORE_GATE_DERIVED = "RESTORE_GATE_DERIVED"
    SET_COMPANION_STATE = "SET_COMPANION_STATE"


class AttendanceCorrectionReason(StrEnum):
    """Closed privacy-safe categories for attendance corrections."""

    MISSED_GATE_SCAN = "MISSED_GATE_SCAN"
    ERRONEOUS_GATE_ADMISSION = "ERRONEOUS_GATE_ADMISSION"
    OPERATOR_ERROR = "OPERATOR_ERROR"
    POST_EVENT_RECONCILIATION = "POST_EVENT_RECONCILIATION"
    OTHER = "OTHER"


class EffectiveAttendanceStatus(StrEnum):
    """Current derived attendance status, never a second mutable truth."""

    ATTENDED = "ATTENDED"
    NOT_ATTENDED = "NOT_ATTENDED"


class EffectiveAttendanceSource(StrEnum):
    """Persisted-evidence source that currently determines effective state."""

    GATE = "GATE"
    MANUAL = "MANUAL"
    NONE = "NONE"


class AttendanceCorrectionPersistenceError(ValueError):
    """Raised when ORM workflows try to create or mutate invalid M8 evidence."""


def normalize_reason_note(value: object | None) -> str | None:
    """Strip only outer whitespace and reject unsafe or oversized audit text."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise AttendanceCorrectionPersistenceError("correction reason note is invalid")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > REASON_NOTE_MAX_LENGTH or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise AttendanceCorrectionPersistenceError("correction reason note is invalid")
    return normalized


class GuestAttendanceCorrection(Base):
    """One immutable decision in a linear event/guest correction chain."""

    __tablename__ = "guest_attendance_corrections"
    __table_args__ = (
        CheckConstraint(
            "revision_number >= 1",
            name="ck_guest_attendance_corrections_revision_positive",
        ),
        CheckConstraint(
            "decision IN ('SET_ATTENDED', 'SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED', "
            "'SET_COMPANION_STATE')",
            name="ck_guest_attendance_corrections_decision",
        ),
        CheckConstraint(
            "reason_code IN ('MISSED_GATE_SCAN', 'ERRONEOUS_GATE_ADMISSION', "
            "'OPERATOR_ERROR', 'POST_EVENT_RECONCILIATION', 'OTHER')",
            name="ck_guest_attendance_corrections_reason",
        ),
        CheckConstraint(
            "previous_effective_status IN ('ATTENDED', 'NOT_ATTENDED')",
            name="ck_guest_attendance_corrections_previous_status",
        ),
        CheckConstraint(
            "resulting_effective_status IN ('ATTENDED', 'NOT_ATTENDED')",
            name="ck_guest_attendance_corrections_resulting_status",
        ),
        CheckConstraint(
            "resulting_effective_source IN ('GATE', 'MANUAL', 'NONE')",
            name="ck_guest_attendance_corrections_resulting_source",
        ),
        CheckConstraint(
            "previous_correction_id IS NULL OR previous_correction_id <> id",
            name="ck_guest_attendance_corrections_previous_not_self",
        ),
        CheckConstraint(
            "(revision_number = 1 AND previous_correction_id IS NULL) OR "
            "(revision_number > 1 AND previous_correction_id IS NOT NULL)",
            name="ck_guest_attendance_corrections_revision_link",
        ),
        CheckConstraint(
            "reason_note IS NULL OR (reason_note = btrim(reason_note) "
            "AND char_length(reason_note) BETWEEN 1 AND 500 "
            "AND reason_note !~ '[[:cntrl:]]')",
            name="ck_guest_attendance_corrections_reason_note",
        ),
        CheckConstraint(
            "reason_code <> 'OTHER' OR reason_note IS NOT NULL",
            name="ck_guest_attendance_corrections_other_note",
        ),
        CheckConstraint(
            f"companions_admitted IS NULL OR companions_admitted BETWEEN 0 AND "
            f"{MAX_COMPANIONS_PER_INVITATION}",
            name="ck_guest_attendance_corrections_companions_bounded",
        ),
        CheckConstraint(
            "(decision = 'SET_ATTENDED' AND companions_admitted IS NOT NULL) OR "
            "(decision IN ('SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED', "
            "'SET_COMPANION_STATE') "
            "AND companions_admitted IS NULL)",
            name="ck_guest_attendance_corrections_decision_companions",
        ),
        CheckConstraint(
            "(decision = 'SET_COMPANION_STATE' "
            "AND previous_declared_companions IS NOT NULL "
            "AND previous_companions_admitted IS NOT NULL "
            "AND resulting_declared_companions IS NOT NULL "
            "AND resulting_companions_admitted IS NOT NULL) OR "
            "(decision IN ('SET_ATTENDED', 'SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED') "
            "AND previous_declared_companions IS NULL "
            "AND previous_companions_admitted IS NULL "
            "AND resulting_declared_companions IS NULL "
            "AND resulting_companions_admitted IS NULL)",
            name="ck_guest_attendance_corrections_companion_snapshot_shape",
        ),
        CheckConstraint(
            "previous_declared_companions IS NULL OR "
            "(previous_companions_admitted >= 0 "
            "AND previous_companions_admitted <= previous_declared_companions "
            f"AND previous_declared_companions <= {MAX_COMPANIONS_PER_INVITATION})",
            name="ck_guest_attendance_corrections_previous_companion_state",
        ),
        CheckConstraint(
            "resulting_declared_companions IS NULL OR "
            "(resulting_companions_admitted >= 0 "
            "AND resulting_companions_admitted <= resulting_declared_companions "
            f"AND resulting_declared_companions <= {MAX_COMPANIONS_PER_INVITATION})",
            name="ck_guest_attendance_corrections_resulting_companion_state",
        ),
        CheckConstraint(
            "(decision = 'SET_ATTENDED' "
            "AND resulting_effective_status = 'ATTENDED' "
            "AND resulting_effective_source = 'MANUAL') OR "
            "(decision = 'SET_NOT_ATTENDED' "
            "AND resulting_effective_status = 'NOT_ATTENDED' "
            "AND resulting_effective_source = 'MANUAL') OR "
            "(decision = 'RESTORE_GATE_DERIVED' AND "
            "((gate_attendance_record_id IS NOT NULL "
            "AND resulting_effective_status = 'ATTENDED' "
            "AND resulting_effective_source = 'GATE') OR "
            "(gate_attendance_record_id IS NULL "
            "AND resulting_effective_status = 'NOT_ATTENDED' "
            "AND resulting_effective_source = 'NONE'))) OR "
            "(decision = 'SET_COMPANION_STATE' "
            "AND resulting_effective_status = previous_effective_status)",
            name="ck_guest_attendance_corrections_decision_result",
        ),
        ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_guest_attendance_corrections_guest_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["gate_attendance_record_id", "event_id", "guest_id"],
            [
                "guest_attendance_records.id",
                "guest_attendance_records.event_id",
                "guest_attendance_records.guest_id",
            ],
            name="fk_guest_attendance_corrections_attendance_event_guest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["previous_correction_id", "event_id", "guest_id"],
            [
                "guest_attendance_corrections.id",
                "guest_attendance_corrections.event_id",
                "guest_attendance_corrections.guest_id",
            ],
            name="fk_guest_attendance_corrections_previous_event_guest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "event_id",
            "guest_id",
            "revision_number",
            name="uq_guest_attendance_corrections_event_guest_revision",
        ),
        UniqueConstraint(
            "previous_correction_id",
            name="uq_guest_attendance_corrections_previous",
        ),
        UniqueConstraint(
            "id",
            "event_id",
            "guest_id",
            name="uq_guest_attendance_corrections_id_event_guest",
        ),
        Index(
            "ix_guest_attendance_corrections_guest_revision_id",
            "event_id",
            "guest_id",
            "revision_number",
            "id",
        ),
        Index(
            "ix_guest_attendance_corrections_event_revision_id",
            "event_id",
            "revision_number",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column()
    guest_id: Mapped[UUID] = mapped_column()
    revision_number: Mapped[int] = mapped_column(Integer)
    previous_correction_id: Mapped[UUID | None] = mapped_column()
    decision: Mapped[AttendanceCorrectionDecision] = mapped_column(
        Enum(
            AttendanceCorrectionDecision,
            name="attendance_correction_decision",
            native_enum=False,
            create_constraint=False,
            length=20,
        )
    )
    reason_code: Mapped[AttendanceCorrectionReason] = mapped_column(
        Enum(
            AttendanceCorrectionReason,
            name="attendance_correction_reason",
            native_enum=False,
            create_constraint=False,
            length=25,
        )
    )
    reason_note: Mapped[str | None] = mapped_column(String(REASON_NOTE_MAX_LENGTH))
    companions_admitted: Mapped[int | None] = mapped_column(SmallInteger)
    previous_declared_companions: Mapped[int | None] = mapped_column(SmallInteger)
    resulting_declared_companions: Mapped[int | None] = mapped_column(SmallInteger)
    previous_companions_admitted: Mapped[int | None] = mapped_column(SmallInteger)
    resulting_companions_admitted: Mapped[int | None] = mapped_column(SmallInteger)
    gate_attendance_record_id: Mapped[UUID | None] = mapped_column()
    previous_effective_status: Mapped[EffectiveAttendanceStatus] = mapped_column(
        Enum(
            EffectiveAttendanceStatus,
            name="effective_attendance_status",
            native_enum=False,
            create_constraint=False,
            length=12,
        )
    )
    resulting_effective_status: Mapped[EffectiveAttendanceStatus] = mapped_column(
        Enum(
            EffectiveAttendanceStatus,
            name="effective_attendance_status",
            native_enum=False,
            create_constraint=False,
            length=12,
        )
    )
    resulting_effective_source: Mapped[EffectiveAttendanceSource] = mapped_column(
        Enum(
            EffectiveAttendanceSource,
            name="effective_attendance_source",
            native_enum=False,
            create_constraint=False,
            length=6,
        )
    )
    corrected_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            "GuestAttendanceCorrection("
            f"id={self.id!r}, event_id={self.event_id!r}, guest_id={self.guest_id!r}, "
            f"revision_number={self.revision_number!r}, decision={self.decision!r})"
        )


_CORRECTION_FIELDS = tuple(column.key for column in GuestAttendanceCorrection.__table__.columns)


@event.listens_for(GuestAttendanceCorrection, "before_insert")
def _validate_correction_insert(_: Any, __: Any, target: GuestAttendanceCorrection) -> None:
    target.reason_note = normalize_reason_note(target.reason_note)
    if target.reason_code is AttendanceCorrectionReason.OTHER and target.reason_note is None:
        raise AttendanceCorrectionPersistenceError("OTHER correction reason requires a note")
    if target.decision is AttendanceCorrectionDecision.SET_ATTENDED:
        if (
            isinstance(target.companions_admitted, bool)
            or not isinstance(target.companions_admitted, int)
            or not 0 <= target.companions_admitted <= MAX_COMPANIONS_PER_INVITATION
        ):
            raise AttendanceCorrectionPersistenceError("correction companion count is invalid")
    elif target.companions_admitted is not None:
        raise AttendanceCorrectionPersistenceError("correction companion count is invalid")

    snapshots = (
        target.previous_declared_companions,
        target.previous_companions_admitted,
        target.resulting_declared_companions,
        target.resulting_companions_admitted,
    )
    if target.decision is AttendanceCorrectionDecision.SET_COMPANION_STATE:
        if any(isinstance(value, bool) or not isinstance(value, int) for value in snapshots):
            raise AttendanceCorrectionPersistenceError("correction companion snapshot is invalid")
        previous_declared, previous_admitted, resulting_declared, resulting_admitted = snapshots
        if not (
            0 <= previous_admitted <= previous_declared <= MAX_COMPANIONS_PER_INVITATION
            and 0 <= resulting_admitted <= resulting_declared <= MAX_COMPANIONS_PER_INVITATION
        ):
            raise AttendanceCorrectionPersistenceError("correction companion snapshot is invalid")
    elif any(value is not None for value in snapshots):
        raise AttendanceCorrectionPersistenceError("correction companion snapshot is invalid")


@event.listens_for(GuestAttendanceCorrection, "before_update")
def _reject_correction_update(_: Any, __: Any, target: GuestAttendanceCorrection) -> None:
    state = sqlalchemy_inspect(target)
    if any(state.attrs[field].history.has_changes() for field in _CORRECTION_FIELDS):
        raise AttendanceCorrectionPersistenceError("attendance corrections are immutable")


@event.listens_for(GuestAttendanceCorrection, "before_delete")
def _reject_correction_delete(_: Any, __: Any, ___: GuestAttendanceCorrection) -> None:
    raise AttendanceCorrectionPersistenceError("attendance corrections are append-only")
