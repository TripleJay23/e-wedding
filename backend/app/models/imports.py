"""Persisted M4 Excel import batches, staged rows, and revision evidence."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ImportBatchStatus(StrEnum):
    """Current aggregate lifecycle of a staged import batch."""

    VALIDATED = "VALIDATED"
    PARTIALLY_IMPORTED = "PARTIALLY_IMPORTED"
    RECONCILED = "RECONCILED"


class ImportRowStatus(StrEnum):
    """Current deterministic outcome for one actual guest row."""

    VALID = "VALID"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"
    PENDING_REVALIDATION = "PENDING_REVALIDATION"
    IMPORTED = "IMPORTED"


class ImportRevisionKind(StrEnum):
    """Administrator-driven staged-row revision types."""

    CORRECTION = "CORRECTION"
    REVALIDATION = "REVALIDATION"


class ImportBatch(Base):
    """One immutable workbook/policy snapshot with a mutable aggregate status."""

    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(
            "workbook_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_import_batches_sha256",
        ),
        CheckConstraint("workbook_byte_size > 0", name="ck_import_batches_byte_size"),
        CheckConstraint("header_row_number > 0", name="ck_import_batches_header_row"),
        CheckConstraint(
            "candidate_row_count > 0 AND ignored_row_count >= 0",
            name="ck_import_batches_row_counts",
        ),
        CheckConstraint(
            "max_companions_snapshot BETWEEN 0 AND 2",
            name="ck_import_batches_companion_snapshot",
        ),
        CheckConstraint(
            "status IN ('VALIDATED', 'PARTIALLY_IMPORTED', 'RECONCILED')",
            name="ck_import_batches_status",
        ),
        Index("ix_import_batches_event_created_id", "event_id", "created_at", "id"),
        Index("ix_import_batches_event_digest", "event_id", "workbook_sha256"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    workbook_sha256: Mapped[str] = mapped_column(String(64))
    workbook_byte_size: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(255))
    detected_sheet_name: Mapped[str] = mapped_column(String(255))
    header_row_number: Mapped[int] = mapped_column(Integer)
    column_mapping_snapshot: Mapped[dict[str, int]] = mapped_column(JSONB)
    structure_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    guest_field_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    max_companions_snapshot: Mapped[int] = mapped_column(SmallInteger)
    candidate_row_count: Mapped[int] = mapped_column(Integer)
    ignored_row_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[ImportBatchStatus] = mapped_column(
        Enum(
            ImportBatchStatus,
            name="import_batch_status",
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        default=ImportBatchStatus.VALIDATED,
        server_default=ImportBatchStatus.VALIDATED.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImportBatchRow(Base):
    """One actual guest row with immutable origin and mutable validated state."""

    __tablename__ = "import_batch_rows"
    __table_args__ = (
        CheckConstraint("source_row_number > 0", name="ck_import_batch_rows_source_row"),
        CheckConstraint("row_version > 0", name="ck_import_batch_rows_version"),
        CheckConstraint(
            "status IN ('VALID', 'INVALID', 'DUPLICATE', 'PENDING_REVALIDATION', 'IMPORTED')",
            name="ck_import_batch_rows_status",
        ),
        CheckConstraint(
            "(status = 'IMPORTED' AND guest_id IS NOT NULL) OR "
            "(status <> 'IMPORTED' AND guest_id IS NULL)",
            name="ck_import_batch_rows_guest_link",
        ),
        UniqueConstraint("batch_id", "source_row_number", name="uq_import_rows_batch_source"),
        UniqueConstraint("guest_id", name="uq_import_rows_guest"),
        Index(
            "ix_import_batch_rows_batch_status_source",
            "batch_id",
            "status",
            "source_row_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    source_row_number: Mapped[int] = mapped_column(Integer)
    original_values: Mapped[dict[str, object]] = mapped_column(JSONB)
    original_validation_errors: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    current_values: Mapped[dict[str, object]] = mapped_column(JSONB)
    validation_errors: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    identity_fingerprints: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[ImportRowStatus] = mapped_column(
        Enum(
            ImportRowStatus,
            name="import_row_status",
            native_enum=False,
            create_constraint=False,
            length=32,
        )
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    guest_id: Mapped[UUID | None] = mapped_column(ForeignKey("guests.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImportRowRevision(Base):
    """Append-only correction/revalidation evidence for one staged row."""

    __tablename__ = "import_row_revisions"
    __table_args__ = (
        CheckConstraint("revision > 1", name="ck_import_row_revisions_revision"),
        CheckConstraint(
            "kind IN ('CORRECTION', 'REVALIDATION')",
            name="ck_import_row_revisions_kind",
        ),
        CheckConstraint(
            "validation_status IN ('VALID', 'INVALID', 'DUPLICATE', 'PENDING_REVALIDATION')",
            name="ck_import_row_revisions_status",
        ),
        UniqueConstraint("row_id", "revision", name="uq_import_row_revisions_row_revision"),
        Index("ix_import_row_revisions_row_revision", "row_id", "revision"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    row_id: Mapped[UUID] = mapped_column(ForeignKey("import_batch_rows.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[ImportRevisionKind] = mapped_column(
        Enum(
            ImportRevisionKind,
            name="import_revision_kind",
            native_enum=False,
            create_constraint=False,
            length=24,
        )
    )
    changed_values: Mapped[dict[str, object]] = mapped_column(JSONB)
    values_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    validation_status: Mapped[ImportRowStatus] = mapped_column(
        Enum(
            ImportRowStatus,
            name="import_revision_validation_status",
            native_enum=False,
            create_constraint=False,
            length=32,
        )
    )
    validation_errors: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
