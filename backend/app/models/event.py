"""Wedding event and event-scoped guest-field persistence."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MAX_COMPANIONS_PER_INVITATION = 2


class EventStatus(StrEnum):
    """Supported event lifecycle states."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class GuestFieldKey(StrEnum):
    """The seven canonical guest fields locked by the M3 contract."""

    FULL_NAME = "full_name"
    PHONE_NUMBER = "phone_number"
    EMAIL = "email"
    INVITATION_TYPE = "invitation_type"
    ALLOWED_COMPANIONS = "allowed_companions"
    TABLE_NUMBER = "table_number"
    CATEGORY = "category"


DEFAULT_GUEST_FIELD_CONFIGURATION: tuple[tuple[GuestFieldKey, bool, bool], ...] = (
    (GuestFieldKey.FULL_NAME, True, True),
    (GuestFieldKey.PHONE_NUMBER, True, False),
    (GuestFieldKey.EMAIL, True, False),
    (GuestFieldKey.INVITATION_TYPE, True, True),
    (GuestFieldKey.ALLOWED_COMPANIONS, True, False),
    (GuestFieldKey.TABLE_NUMBER, True, False),
    (GuestFieldKey.CATEGORY, True, False),
)


class Event(Base):
    """Administrator-created wedding event and companion policy."""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(event_name)) BETWEEN 1 AND 160",
            name="ck_events_event_name_bounded",
        ),
        CheckConstraint(
            "char_length(btrim(bride_name)) BETWEEN 1 AND 120",
            name="ck_events_bride_name_bounded",
        ),
        CheckConstraint(
            "char_length(btrim(groom_name)) BETWEEN 1 AND 120",
            name="ck_events_groom_name_bounded",
        ),
        CheckConstraint(
            "char_length(btrim(venue)) BETWEEN 1 AND 255",
            name="ck_events_venue_bounded",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'COMPLETED', 'CANCELLED')",
            name="ck_events_status",
        ),
        CheckConstraint(
            f"max_companions_per_invitation BETWEEN 0 AND {MAX_COMPANIONS_PER_INVITATION}",
            name="ck_events_max_companions_bounded",
        ),
        Index("ix_events_event_datetime_id", "event_datetime", "id"),
        Index("ix_events_status_event_datetime_id", "status", "event_datetime", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_name: Mapped[str] = mapped_column(String(160))
    bride_name: Mapped[str] = mapped_column(String(120))
    groom_name: Mapped[str] = mapped_column(String(120))
    event_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    venue: Mapped[str] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EventStatus] = mapped_column(
        Enum(
            EventStatus,
            name="event_status",
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        default=EventStatus.DRAFT,
        server_default=EventStatus.DRAFT.value,
    )
    max_companions_per_invitation: Mapped[int] = mapped_column(
        SmallInteger,
        default=MAX_COMPANIONS_PER_INVITATION,
        server_default=str(MAX_COMPANIONS_PER_INVITATION),
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class EventGuestField(Base):
    """One canonical field's enabled and required rules for an event."""

    __tablename__ = "event_guest_fields"
    __table_args__ = (
        CheckConstraint(
            "field_key IN "
            "('full_name', 'phone_number', 'email', 'invitation_type', "
            "'allowed_companions', 'table_number', 'category')",
            name="ck_event_guest_fields_key",
        ),
        CheckConstraint(
            "sort_order BETWEEN 0 AND 6",
            name="ck_event_guest_fields_sort_order",
        ),
        CheckConstraint(
            "NOT required OR enabled",
            name="ck_event_guest_fields_required_enabled",
        ),
        UniqueConstraint("event_id", "field_key", name="uq_event_guest_fields_event_key"),
        UniqueConstraint("event_id", "sort_order", name="uq_event_guest_fields_event_order"),
        Index("ix_event_guest_fields_event_order", "event_id", "sort_order"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
    )
    field_key: Mapped[GuestFieldKey] = mapped_column(
        Enum(
            GuestFieldKey,
            values_callable=lambda enum: [member.value for member in enum],
            name="guest_field_key",
            native_enum=False,
            create_constraint=False,
            length=32,
        )
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(SmallInteger)
