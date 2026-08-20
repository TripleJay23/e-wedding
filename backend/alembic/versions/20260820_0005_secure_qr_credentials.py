"""Add secure M5 guest QR credentials and lifecycle constraints.

Revision ID: 20260820_0005
Revises: 20260820_0004
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0005"
down_revision: str | None = "20260820_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create hash-only, event-bound credentials with one active row per guest."""
    op.create_unique_constraint("uq_guests_id_event", "guests", ["id", "event_id"])
    op.create_table(
        "guest_qr_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("image_storage_key", sa.String(length=255), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "INACTIVE",
                "SUPERSEDED",
                name="qr_credential_status",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("replaces_credential_id", sa.Uuid(), nullable=True),
        sa.Column("issued_by", sa.Uuid(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
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
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_guest_qr_credentials_token_hash",
        ),
        sa.CheckConstraint(
            "image_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_guest_qr_credentials_image_sha256",
        ),
        sa.CheckConstraint(
            "image_storage_key = replace(event_id::text, '-', '') || '/' || "
            "replace(guest_id::text, '-', '') || '/' || replace(id::text, '-', '') || '.png'",
            name="ck_guest_qr_credentials_storage_key",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'SUPERSEDED')",
            name="ck_guest_qr_credentials_status",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND deactivated_at IS NULL AND superseded_at IS NULL) OR "
            "(status = 'INACTIVE' AND deactivated_at IS NOT NULL AND superseded_at IS NULL) OR "
            "(status = 'SUPERSEDED' AND superseded_at IS NOT NULL)",
            name="ck_guest_qr_credentials_status_timestamps",
        ),
        sa.CheckConstraint(
            "replaces_credential_id IS NULL OR replaces_credential_id <> id",
            name="ck_guest_qr_credentials_not_self_replacement",
        ),
        sa.ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_guest_qr_credentials_guest_event",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaces_credential_id"],
            ["guest_qr_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_guest_qr_credentials_token_hash"),
        sa.UniqueConstraint("image_storage_key", name="uq_guest_qr_credentials_storage_key"),
        sa.UniqueConstraint("replaces_credential_id", name="uq_guest_qr_credentials_replacement"),
    )
    op.create_index(
        "ix_guest_qr_credentials_guest_issued_id",
        "guest_qr_credentials",
        ["guest_id", "issued_at", "id"],
    )
    op.create_index(
        "ix_guest_qr_credentials_event_issued_id",
        "guest_qr_credentials",
        ["event_id", "issued_at", "id"],
    )
    op.create_index(
        "uq_guest_qr_credentials_one_active_guest",
        "guest_qr_credentials",
        ["guest_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.execute(
        """
        CREATE FUNCTION protect_guest_qr_credential_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.guest_id IS DISTINCT FROM OLD.guest_id
               OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
               OR NEW.image_storage_key IS DISTINCT FROM OLD.image_storage_key
               OR NEW.image_sha256 IS DISTINCT FROM OLD.image_sha256
               OR NEW.replaces_credential_id IS DISTINCT FROM OLD.replaces_credential_id
               OR NEW.issued_by IS DISTINCT FROM OLD.issued_by
               OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'QR credential identity is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_qr_credentials_identity_immutable';
            END IF;
            IF OLD.status = 'SUPERSEDED' AND NEW.status <> 'SUPERSEDED' THEN
                RAISE EXCEPTION 'superseded QR credentials cannot be reactivated'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_qr_credentials_superseded_terminal';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guest_qr_credentials_lifecycle
        BEFORE UPDATE ON guest_qr_credentials
        FOR EACH ROW
        EXECUTE FUNCTION protect_guest_qr_credential_lifecycle()
        """
    )


def downgrade() -> None:
    """Remove only the M5 credential persistence and guest composite key."""
    op.execute("DROP TRIGGER trg_guest_qr_credentials_lifecycle ON guest_qr_credentials")
    op.execute("DROP FUNCTION protect_guest_qr_credential_lifecycle()")
    op.drop_index(
        "uq_guest_qr_credentials_one_active_guest",
        table_name="guest_qr_credentials",
    )
    op.drop_index(
        "ix_guest_qr_credentials_event_issued_id",
        table_name="guest_qr_credentials",
    )
    op.drop_index(
        "ix_guest_qr_credentials_guest_issued_id",
        table_name="guest_qr_credentials",
    )
    op.drop_table("guest_qr_credentials")
    op.drop_constraint("uq_guests_id_event", "guests", type_="unique")
