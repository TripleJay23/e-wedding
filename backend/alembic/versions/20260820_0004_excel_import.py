"""Add complete M4 staged Excel import persistence.

Revision ID: 20260820_0004
Revises: 20260814_0003
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0004"
down_revision: str | None = "20260814_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create M4 batches, staged rows, revision evidence, and immutability guards."""
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("workbook_sha256", sa.String(length=64), nullable=False),
        sa.Column("workbook_byte_size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("detected_sheet_name", sa.String(length=255), nullable=False),
        sa.Column("header_row_number", sa.Integer(), nullable=False),
        sa.Column(
            "column_mapping_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("structure_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("guest_field_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("max_companions_snapshot", sa.SmallInteger(), nullable=False),
        sa.Column("candidate_row_count", sa.Integer(), nullable=False),
        sa.Column("ignored_row_count", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "VALIDATED",
                "PARTIALLY_IMPORTED",
                "RECONCILED",
                name="import_batch_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="VALIDATED",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "workbook_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_import_batches_sha256",
        ),
        sa.CheckConstraint("workbook_byte_size > 0", name="ck_import_batches_byte_size"),
        sa.CheckConstraint("header_row_number > 0", name="ck_import_batches_header_row"),
        sa.CheckConstraint(
            "candidate_row_count > 0 AND ignored_row_count >= 0",
            name="ck_import_batches_row_counts",
        ),
        sa.CheckConstraint(
            "max_companions_snapshot BETWEEN 0 AND 2",
            name="ck_import_batches_companion_snapshot",
        ),
        sa.CheckConstraint(
            "status IN ('VALIDATED', 'PARTIALLY_IMPORTED', 'RECONCILED')",
            name="ck_import_batches_status",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_batches_event_created_id",
        "import_batches",
        ["event_id", "created_at", "id"],
    )
    op.create_index(
        "ix_import_batches_event_digest",
        "import_batches",
        ["event_id", "workbook_sha256"],
    )

    op.create_table(
        "import_batch_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("original_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "original_validation_errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("current_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("identity_fingerprints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "VALID",
                "INVALID",
                "DUPLICATE",
                "PENDING_REVALIDATION",
                "IMPORTED",
                name="import_row_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source_row_number > 0", name="ck_import_batch_rows_source_row"),
        sa.CheckConstraint("row_version > 0", name="ck_import_batch_rows_version"),
        sa.CheckConstraint(
            "status IN ('VALID', 'INVALID', 'DUPLICATE', 'PENDING_REVALIDATION', 'IMPORTED')",
            name="ck_import_batch_rows_status",
        ),
        sa.CheckConstraint(
            "(status = 'IMPORTED' AND guest_id IS NOT NULL) OR "
            "(status <> 'IMPORTED' AND guest_id IS NULL)",
            name="ck_import_batch_rows_guest_link",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "source_row_number", name="uq_import_rows_batch_source"),
        sa.UniqueConstraint("guest_id", name="uq_import_rows_guest"),
    )
    op.create_index(
        "ix_import_batch_rows_batch_status_source",
        "import_batch_rows",
        ["batch_id", "status", "source_row_number"],
    )

    op.create_table(
        "import_row_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "CORRECTION",
                "REVALIDATION",
                name="import_revision_kind",
                native_enum=False,
                create_constraint=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("changed_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("values_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "validation_status",
            sa.Enum(
                "VALID",
                "INVALID",
                "DUPLICATE",
                "PENDING_REVALIDATION",
                name="import_revision_validation_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision > 1", name="ck_import_row_revisions_revision"),
        sa.CheckConstraint(
            "kind IN ('CORRECTION', 'REVALIDATION')",
            name="ck_import_row_revisions_kind",
        ),
        sa.CheckConstraint(
            "validation_status IN ('VALID', 'INVALID', 'DUPLICATE', 'PENDING_REVALIDATION')",
            name="ck_import_row_revisions_status",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["row_id"], ["import_batch_rows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("row_id", "revision", name="uq_import_row_revisions_row_revision"),
    )
    op.create_index(
        "ix_import_row_revisions_row_revision",
        "import_row_revisions",
        ["row_id", "revision"],
    )

    op.execute(
        """
        CREATE FUNCTION protect_import_batch_snapshot()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by
               OR NEW.workbook_sha256 IS DISTINCT FROM OLD.workbook_sha256
               OR NEW.workbook_byte_size IS DISTINCT FROM OLD.workbook_byte_size
               OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
               OR NEW.detected_sheet_name IS DISTINCT FROM OLD.detected_sheet_name
               OR NEW.header_row_number IS DISTINCT FROM OLD.header_row_number
               OR NEW.column_mapping_snapshot IS DISTINCT FROM OLD.column_mapping_snapshot
               OR NEW.structure_snapshot IS DISTINCT FROM OLD.structure_snapshot
               OR NEW.guest_field_snapshot IS DISTINCT FROM OLD.guest_field_snapshot
               OR NEW.max_companions_snapshot IS DISTINCT FROM OLD.max_companions_snapshot
               OR NEW.candidate_row_count IS DISTINCT FROM OLD.candidate_row_count
               OR NEW.ignored_row_count IS DISTINCT FROM OLD.ignored_row_count
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'import batch snapshot is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_import_batches_snapshot_immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_import_batches_snapshot_immutable
        BEFORE UPDATE ON import_batches
        FOR EACH ROW
        EXECUTE FUNCTION protect_import_batch_snapshot()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_import_row_origin()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.batch_id IS DISTINCT FROM OLD.batch_id
               OR NEW.source_row_number IS DISTINCT FROM OLD.source_row_number
               OR NEW.original_values IS DISTINCT FROM OLD.original_values
               OR NEW.original_validation_errors IS DISTINCT FROM OLD.original_validation_errors
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'import row origin is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_import_batch_rows_origin_immutable';
            END IF;
            IF OLD.guest_id IS NOT NULL
               AND (NEW.guest_id IS DISTINCT FROM OLD.guest_id OR NEW.status <> 'IMPORTED') THEN
                RAISE EXCEPTION 'imported row linkage is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_import_batch_rows_import_link_immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_import_batch_rows_origin_immutable
        BEFORE UPDATE ON import_batch_rows
        FOR EACH ROW
        EXECUTE FUNCTION protect_import_row_origin()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_import_revision_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'import row revisions are append-only'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_import_row_revisions_append_only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_import_row_revisions_append_only
        BEFORE UPDATE OR DELETE ON import_row_revisions
        FOR EACH ROW
        EXECUTE FUNCTION reject_import_revision_mutation()
        """
    )


def downgrade() -> None:
    """Remove only the M4 import persistence and its guards."""
    op.execute("DROP TRIGGER trg_import_row_revisions_append_only ON import_row_revisions")
    op.execute("DROP FUNCTION reject_import_revision_mutation()")
    op.execute("DROP TRIGGER trg_import_batch_rows_origin_immutable ON import_batch_rows")
    op.execute("DROP FUNCTION protect_import_row_origin()")
    op.execute("DROP TRIGGER trg_import_batches_snapshot_immutable ON import_batches")
    op.execute("DROP FUNCTION protect_import_batch_snapshot()")
    op.drop_index("ix_import_row_revisions_row_revision", table_name="import_row_revisions")
    op.drop_table("import_row_revisions")
    op.drop_index("ix_import_batch_rows_batch_status_source", table_name="import_batch_rows")
    op.drop_table("import_batch_rows")
    op.drop_index("ix_import_batches_event_digest", table_name="import_batches")
    op.drop_index("ix_import_batches_event_created_id", table_name="import_batches")
    op.drop_table("import_batches")
