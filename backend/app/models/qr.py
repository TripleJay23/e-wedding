"""Secure event-bound guest QR credential persistence."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QrCredentialStatus(StrEnum):
    """Supported M5 credential lifecycle states."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUPERSEDED = "SUPERSEDED"


class GuestQrCredential(Base):
    """A hash-only verifier plus protected renderable QR artifact reference."""

    __tablename__ = "guest_qr_credentials"
    __table_args__ = (
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_guest_qr_credentials_token_hash",
        ),
        CheckConstraint(
            "image_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_guest_qr_credentials_image_sha256",
        ),
        CheckConstraint(
            "image_storage_key = replace(event_id::text, '-', '') || '/' || "
            "replace(guest_id::text, '-', '') || '/' || replace(id::text, '-', '') || '.png'",
            name="ck_guest_qr_credentials_storage_key",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'SUPERSEDED')",
            name="ck_guest_qr_credentials_status",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND deactivated_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'INACTIVE' AND deactivated_at IS NOT NULL AND superseded_at IS NULL) OR "
            "(status = 'SUPERSEDED' AND superseded_at IS NOT NULL)",
            name="ck_guest_qr_credentials_status_timestamps",
        ),
        CheckConstraint(
            "replaces_credential_id IS NULL OR replaces_credential_id <> id",
            name="ck_guest_qr_credentials_not_self_replacement",
        ),
        ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_guest_qr_credentials_guest_event",
            ondelete="CASCADE",
        ),
        UniqueConstraint("token_hash", name="uq_guest_qr_credentials_token_hash"),
        UniqueConstraint("image_storage_key", name="uq_guest_qr_credentials_storage_key"),
        UniqueConstraint("replaces_credential_id", name="uq_guest_qr_credentials_replacement"),
        UniqueConstraint(
            "id",
            "event_id",
            "guest_id",
            name="uq_guest_qr_credentials_id_event_guest",
        ),
        Index(
            "ix_guest_qr_credentials_guest_issued_id",
            "guest_id",
            "issued_at",
            "id",
        ),
        Index(
            "ix_guest_qr_credentials_event_issued_id",
            "event_id",
            "issued_at",
            "id",
        ),
        Index(
            "uq_guest_qr_credentials_one_active_guest",
            "guest_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column()
    guest_id: Mapped[UUID] = mapped_column()
    token_hash: Mapped[str] = mapped_column(String(64))
    image_storage_key: Mapped[str] = mapped_column(String(255))
    image_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[QrCredentialStatus] = mapped_column(
        Enum(
            QrCredentialStatus,
            name="qr_credential_status",
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        default=QrCredentialStatus.ACTIVE,
        server_default=QrCredentialStatus.ACTIVE.value,
    )
    replaces_credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("guest_qr_credentials.id", ondelete="SET NULL")
    )
    issued_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
