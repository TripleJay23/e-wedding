"""Manual guest foundation persistence."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.event import MAX_COMPANIONS_PER_INVITATION


class GuestStatus(StrEnum):
    """Manual guest availability states."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Guest(Base):
    """An event-scoped invitation holder entered manually at M3."""

    __tablename__ = "guests"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(full_name)) BETWEEN 1 AND 160",
            name="ck_guests_full_name_bounded",
        ),
        CheckConstraint(
            "char_length(btrim(invitation_type)) BETWEEN 1 AND 64",
            name="ck_guests_invitation_type_bounded",
        ),
        CheckConstraint(
            f"allowed_companions BETWEEN 0 AND {MAX_COMPANIONS_PER_INVITATION}",
            name="ck_guests_allowed_companions_bounded",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="ck_guests_status",
        ),
        UniqueConstraint("id", "event_id", name="uq_guests_id_event"),
        Index("ix_guests_event_full_name_id", "event_id", "full_name", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
    )
    full_name: Mapped[str] = mapped_column(String(160))
    phone_number: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    invitation_type: Mapped[str] = mapped_column(String(64))
    allowed_companions: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    table_number: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[GuestStatus] = mapped_column(
        Enum(
            GuestStatus,
            name="guest_status",
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        default=GuestStatus.ACTIVE,
        server_default=GuestStatus.ACTIVE.value,
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
