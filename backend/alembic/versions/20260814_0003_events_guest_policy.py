"""Add M3 events, guest policy, configured fields, and manual guests.

Revision ID: 20260814_0003
Revises: 20260814_0002
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0003"
down_revision: str | None = "20260814_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the complete PostgreSQL-backed M3 domain foundation."""
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_name", sa.String(length=160), nullable=False),
        sa.Column("bride_name", sa.String(length=120), nullable=False),
        sa.Column("groom_name", sa.String(length=120), nullable=False),
        sa.Column("event_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(length=255), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "ACTIVE",
                "COMPLETED",
                "CANCELLED",
                name="event_status",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "max_companions_per_invitation",
            sa.SmallInteger(),
            server_default="2",
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
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
            "char_length(btrim(event_name)) BETWEEN 1 AND 160",
            name="ck_events_event_name_bounded",
        ),
        sa.CheckConstraint(
            "char_length(btrim(bride_name)) BETWEEN 1 AND 120",
            name="ck_events_bride_name_bounded",
        ),
        sa.CheckConstraint(
            "char_length(btrim(groom_name)) BETWEEN 1 AND 120",
            name="ck_events_groom_name_bounded",
        ),
        sa.CheckConstraint(
            "char_length(btrim(venue)) BETWEEN 1 AND 255",
            name="ck_events_venue_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'COMPLETED', 'CANCELLED')",
            name="ck_events_status",
        ),
        sa.CheckConstraint(
            "max_companions_per_invitation BETWEEN 0 AND 2",
            name="ck_events_max_companions_bounded",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_event_datetime_id", "events", ["event_datetime", "id"])
    op.create_index(
        "ix_events_status_event_datetime_id",
        "events",
        ["status", "event_datetime", "id"],
    )

    op.create_table(
        "event_guest_fields",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "field_key",
            sa.Enum(
                "full_name",
                "phone_number",
                "email",
                "invitation_type",
                "allowed_companions",
                "table_number",
                "category",
                name="guest_field_key",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "field_key IN "
            "('full_name', 'phone_number', 'email', 'invitation_type', "
            "'allowed_companions', 'table_number', 'category')",
            name="ck_event_guest_fields_key",
        ),
        sa.CheckConstraint(
            "NOT required OR enabled",
            name="ck_event_guest_fields_required_enabled",
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN 0 AND 6",
            name="ck_event_guest_fields_sort_order",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "field_key", name="uq_event_guest_fields_event_key"),
        sa.UniqueConstraint("event_id", "sort_order", name="uq_event_guest_fields_event_order"),
    )
    op.create_index(
        "ix_event_guest_fields_event_order",
        "event_guest_fields",
        ["event_id", "sort_order"],
    )

    op.create_table(
        "guests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("invitation_type", sa.String(length=64), nullable=False),
        sa.Column("allowed_companions", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("table_number", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "INACTIVE",
                name="guest_status",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="ACTIVE",
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
            "char_length(btrim(full_name)) BETWEEN 1 AND 160",
            name="ck_guests_full_name_bounded",
        ),
        sa.CheckConstraint(
            "char_length(btrim(invitation_type)) BETWEEN 1 AND 64",
            name="ck_guests_invitation_type_bounded",
        ),
        sa.CheckConstraint(
            "allowed_companions BETWEEN 0 AND 2",
            name="ck_guests_allowed_companions_bounded",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="ck_guests_status",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_guests_event_full_name_id",
        "guests",
        ["event_id", "full_name", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION enforce_guest_companion_policy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            event_maximum smallint;
        BEGIN
            SELECT max_companions_per_invitation
            INTO event_maximum
            FROM events
            WHERE id = NEW.event_id;

            IF event_maximum IS NOT NULL
               AND NEW.allowed_companions > event_maximum THEN
                RAISE EXCEPTION 'guest companion allowance exceeds event policy'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guests_allowed_companions_event_policy';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guests_companion_policy
        BEFORE INSERT OR UPDATE OF event_id, allowed_companions ON guests
        FOR EACH ROW
        EXECUTE FUNCTION enforce_guest_companion_policy()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_event_companion_policy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM guests
                WHERE event_id = NEW.id
                  AND allowed_companions > NEW.max_companions_per_invitation
            ) THEN
                RAISE EXCEPTION 'event companion policy is below an existing guest allowance'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_events_max_companions_existing_guests';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_events_companion_policy
        BEFORE UPDATE OF max_companions_per_invitation ON events
        FOR EACH ROW
        EXECUTE FUNCTION enforce_event_companion_policy()
        """
    )


def downgrade() -> None:
    """Remove the M3 domain foundation and policy triggers."""
    op.execute("DROP TRIGGER trg_events_companion_policy ON events")
    op.execute("DROP FUNCTION enforce_event_companion_policy()")
    op.execute("DROP TRIGGER trg_guests_companion_policy ON guests")
    op.execute("DROP FUNCTION enforce_guest_companion_policy()")
    op.drop_index("ix_guests_event_full_name_id", table_name="guests")
    op.drop_table("guests")
    op.drop_index("ix_event_guest_fields_event_order", table_name="event_guest_fields")
    op.drop_table("event_guest_fields")
    op.drop_index("ix_events_status_event_datetime_id", table_name="events")
    op.drop_index("ix_events_event_datetime_id", table_name="events")
    op.drop_table("events")
