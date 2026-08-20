"""Immutable M6 template, design, photo, and generated-card persistence."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TemplateVersionStatus(StrEnum):
    """Supported immutable template verification state."""

    VERIFIED = "VERIFIED"


class PhotoFit(StrEnum):
    """Generic manifest photo-fit controls."""

    COVER = "COVER"
    CONTAIN = "CONTAIN"


class DesignApprovalStatus(StrEnum):
    """Approval lifecycle for an immutable design revision."""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class GeneratedCardStatus(StrEnum):
    """Whether an artifact still represents the event's current inputs."""

    CURRENT = "CURRENT"
    STALE = "STALE"


class TemplatePreset(Base):
    """Supported template identity independent of immutable versions."""

    __tablename__ = "template_presets"
    __table_args__ = (
        CheckConstraint(
            "template_key = 'wedding_floral'",
            name="ck_template_presets_supported_key",
        ),
        CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 120",
            name="ck_template_presets_display_name_bounded",
        ),
        UniqueConstraint("template_key", name="uq_template_presets_template_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    template_key: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TemplatePresetVersion(Base):
    """Verified immutable package identity used by design revisions."""

    __tablename__ = "template_preset_versions"
    __table_args__ = (
        CheckConstraint("version = 10", name="ck_template_preset_versions_supported_version"),
        CheckConstraint(
            "raw_package_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_raw_sha256",
        ),
        CheckConstraint(
            "logical_package_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_logical_sha256",
        ),
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_manifest_sha256",
        ),
        CheckConstraint(
            "approved_preview_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_preview_sha256",
        ),
        CheckConstraint(
            "package_storage_key = 'wedding_floral/v10/wedding_floral_v10.template-package.zip'",
            name="ck_template_preset_versions_storage_key",
        ),
        CheckConstraint(
            "canvas_width = 1240 AND canvas_height = 1748",
            name="ck_template_preset_versions_canvas",
        ),
        CheckConstraint(
            "asset_count = 6 AND font_count = 2",
            name="ck_template_preset_versions_inventory",
        ),
        CheckConstraint(
            "status = 'VERIFIED'",
            name="ck_template_preset_versions_status",
        ),
        UniqueConstraint(
            "preset_id",
            "version",
            name="uq_template_preset_versions_preset_version",
        ),
        UniqueConstraint(
            "raw_package_sha256",
            name="uq_template_preset_versions_raw_sha256",
        ),
        UniqueConstraint(
            "logical_package_sha256",
            name="uq_template_preset_versions_logical_sha256",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    preset_id: Mapped[UUID] = mapped_column(ForeignKey("template_presets.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(SmallInteger)
    raw_package_sha256: Mapped[str] = mapped_column(String(64))
    logical_package_sha256: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    approved_preview_sha256: Mapped[str] = mapped_column(String(64))
    package_storage_key: Mapped[str] = mapped_column(String(255))
    canvas_width: Mapped[int] = mapped_column(Integer)
    canvas_height: Mapped[int] = mapped_column(Integer)
    asset_count: Mapped[int] = mapped_column(SmallInteger)
    font_count: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[TemplateVersionStatus] = mapped_column(
        Enum(
            TemplateVersionStatus,
            name="template_version_status",
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        default=TemplateVersionStatus.VERIFIED,
        server_default=TemplateVersionStatus.VERIFIED.value,
    )
    verified_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventPhoto(Base):
    """Canonical metadata-free private event photo."""

    __tablename__ = "event_photos"
    __table_args__ = (
        CheckConstraint(
            "storage_key = replace(event_id::text, '-', '') || '/' || "
            "replace(id::text, '-', '') || '.png'",
            name="ck_event_photos_storage_key",
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_event_photos_sha256"),
        CheckConstraint("media_type = 'image/png'", name="ck_event_photos_media_type"),
        CheckConstraint("byte_size > 0", name="ck_event_photos_byte_size"),
        CheckConstraint(
            "pixel_width BETWEEN 256 AND 12000 AND pixel_height BETWEEN 256 AND 12000",
            name="ck_event_photos_dimensions",
        ),
        UniqueConstraint("id", "event_id", name="uq_event_photos_id_event"),
        UniqueConstraint("storage_key", name="uq_event_photos_storage_key"),
        Index("ix_event_photos_event_created_id", "event_id", "created_at", "id"),
        Index(
            "uq_event_photos_one_primary_event",
            "event_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(32), default="image/png")
    byte_size: Mapped[int] = mapped_column(BigInteger)
    pixel_width: Mapped[int] = mapped_column(Integer)
    pixel_height: Mapped[int] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class EventCardDesign(Base):
    """Stable event-level design identity with immutable revisions."""

    __tablename__ = "event_card_designs"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_card_designs_event"),
        UniqueConstraint("id", "event_id", name="uq_event_card_designs_id_event"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventCardDesignRevision(Base):
    """Append-only selection of template, photo, and generic photo controls."""

    __tablename__ = "event_card_design_revisions"
    __table_args__ = (
        CheckConstraint("revision_number >= 1", name="ck_event_card_design_revisions_number"),
        CheckConstraint(
            "photo_fit IN ('COVER', 'CONTAIN')",
            name="ck_event_card_design_revisions_photo_fit",
        ),
        CheckConstraint(
            "photo_focus_x BETWEEN 0 AND 1 AND photo_focus_y BETWEEN 0 AND 1",
            name="ck_event_card_design_revisions_focus",
        ),
        CheckConstraint(
            "photo_zoom BETWEEN 1 AND 3",
            name="ck_event_card_design_revisions_zoom",
        ),
        CheckConstraint(
            "photo_fit = 'COVER' OR photo_zoom = 1",
            name="ck_event_card_design_revisions_contain_zoom",
        ),
        ForeignKeyConstraint(
            ["design_id", "event_id"],
            ["event_card_designs.id", "event_card_designs.event_id"],
            name="fk_event_card_design_revisions_design_event",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["event_photo_id", "event_id"],
            ["event_photos.id", "event_photos.event_id"],
            name="fk_event_card_design_revisions_photo_event",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "design_id",
            "revision_number",
            name="uq_event_card_design_revisions_design_number",
        ),
        UniqueConstraint(
            "id",
            "event_id",
            name="uq_event_card_design_revisions_id_event",
        ),
        UniqueConstraint(
            "id",
            "event_id",
            "template_version_id",
            name="uq_event_card_design_revisions_id_event_template",
        ),
        Index(
            "ix_event_card_design_revisions_event_number",
            "event_id",
            "revision_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    design_id: Mapped[UUID] = mapped_column()
    event_id: Mapped[UUID] = mapped_column()
    revision_number: Mapped[int] = mapped_column(Integer)
    template_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("template_preset_versions.id", ondelete="RESTRICT")
    )
    event_photo_id: Mapped[UUID] = mapped_column()
    photo_fit: Mapped[PhotoFit] = mapped_column(
        Enum(
            PhotoFit,
            name="photo_fit",
            native_enum=False,
            create_constraint=False,
            length=16,
        )
    )
    photo_focus_x: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    photo_focus_y: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    photo_zoom: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventCardDesignApproval(Base):
    """Versioned approval/revocation evidence for one immutable design revision."""

    __tablename__ = "event_card_design_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED')",
            name="ck_event_card_design_approvals_status",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND revoked_by IS NULL AND revoked_at IS NULL) OR "
            "(status = 'REVOKED' AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_event_card_design_approvals_status_timestamps",
        ),
        ForeignKeyConstraint(
            ["design_revision_id", "event_id"],
            ["event_card_design_revisions.id", "event_card_design_revisions.event_id"],
            name="fk_event_card_design_approvals_revision_event",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "event_id",
            "design_revision_id",
            name="uq_event_card_design_approvals_id_event_revision",
        ),
        Index(
            "ix_event_card_design_approvals_event_approved_id",
            "event_id",
            "approved_at",
            "id",
        ),
        Index(
            "uq_event_card_design_approvals_one_active_event",
            "event_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column()
    design_revision_id: Mapped[UUID] = mapped_column()
    status: Mapped[DesignApprovalStatus] = mapped_column(
        Enum(
            DesignApprovalStatus,
            name="design_approval_status",
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        default=DesignApprovalStatus.ACTIVE,
        server_default=DesignApprovalStatus.ACTIVE.value,
    )
    approved_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GeneratedGuestCard(Base):
    """Private immutable artifact pinned to the exact approved QR/render inputs."""

    __tablename__ = "generated_guest_cards"
    __table_args__ = (
        CheckConstraint(
            "render_input_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_guest_cards_render_input_sha256",
        ),
        CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_guest_cards_artifact_sha256",
        ),
        CheckConstraint(
            "storage_key = replace(event_id::text, '-', '') || '/' || "
            "replace(guest_id::text, '-', '') || '/' || replace(id::text, '-', '') || '.png'",
            name="ck_generated_guest_cards_storage_key",
        ),
        CheckConstraint(
            "media_type = 'image/png'",
            name="ck_generated_guest_cards_media_type",
        ),
        CheckConstraint("byte_size > 0", name="ck_generated_guest_cards_byte_size"),
        CheckConstraint(
            "pixel_width = 1240 AND pixel_height = 1748",
            name="ck_generated_guest_cards_dimensions",
        ),
        CheckConstraint(
            "status IN ('CURRENT', 'STALE')",
            name="ck_generated_guest_cards_status",
        ),
        CheckConstraint(
            "(status = 'CURRENT' AND stale_at IS NULL) OR "
            "(status = 'STALE' AND stale_at IS NOT NULL)",
            name="ck_generated_guest_cards_status_timestamp",
        ),
        ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_generated_guest_cards_guest_event",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["design_revision_id", "event_id", "template_version_id"],
            [
                "event_card_design_revisions.id",
                "event_card_design_revisions.event_id",
                "event_card_design_revisions.template_version_id",
            ],
            name="fk_generated_guest_cards_revision_event_template",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approval_id", "event_id", "design_revision_id"],
            [
                "event_card_design_approvals.id",
                "event_card_design_approvals.event_id",
                "event_card_design_approvals.design_revision_id",
            ],
            name="fk_generated_guest_cards_approval_event_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["qr_credential_id", "event_id", "guest_id"],
            [
                "guest_qr_credentials.id",
                "guest_qr_credentials.event_id",
                "guest_qr_credentials.guest_id",
            ],
            name="fk_generated_guest_cards_qr_event_guest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "render_input_sha256",
            name="uq_generated_guest_cards_render_input_sha256",
        ),
        UniqueConstraint("storage_key", name="uq_generated_guest_cards_storage_key"),
        Index(
            "ix_generated_guest_cards_event_generated_id",
            "event_id",
            "generated_at",
            "id",
        ),
        Index(
            "ix_generated_guest_cards_guest_generated_id",
            "guest_id",
            "generated_at",
            "id",
        ),
        Index(
            "uq_generated_guest_cards_one_current_guest",
            "guest_id",
            unique=True,
            postgresql_where=text("status = 'CURRENT'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column()
    guest_id: Mapped[UUID] = mapped_column()
    design_revision_id: Mapped[UUID] = mapped_column()
    approval_id: Mapped[UUID] = mapped_column()
    template_version_id: Mapped[UUID] = mapped_column()
    qr_credential_id: Mapped[UUID] = mapped_column()
    render_input_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(255))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(32), default="image/png")
    byte_size: Mapped[int] = mapped_column(BigInteger)
    pixel_width: Mapped[int] = mapped_column(Integer)
    pixel_height: Mapped[int] = mapped_column(Integer)
    status: Mapped[GeneratedCardStatus] = mapped_column(
        Enum(
            GeneratedCardStatus,
            name="generated_card_status",
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        default=GeneratedCardStatus.CURRENT,
        server_default=GeneratedCardStatus.CURRENT.value,
    )
    generated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
