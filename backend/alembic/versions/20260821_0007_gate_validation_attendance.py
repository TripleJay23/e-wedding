"""Add M7 immutable gate validation and attendance evidence.

Revision ID: 20260821_0007
Revises: 20260820_0006
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0007"
down_revision: str | None = "20260820_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one successful attendance row plus append-only evidence per scan."""

    op.create_table(
        "guest_attendance_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("first_qr_credential_id", sa.Uuid(), nullable=False),
        sa.Column("companions_admitted", sa.SmallInteger(), nullable=False),
        sa.Column("checked_in_by", sa.Uuid(), nullable=False),
        sa.Column(
            "checked_in_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "companions_admitted BETWEEN 0 AND 2",
            name="ck_guest_attendance_records_companions_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_guest_attendance_records_guest_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_qr_credential_id", "event_id", "guest_id"],
            [
                "guest_qr_credentials.id",
                "guest_qr_credentials.event_id",
                "guest_qr_credentials.guest_id",
            ],
            name="fk_guest_attendance_records_credential_event_guest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["checked_in_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "guest_id",
            name="uq_guest_attendance_records_event_guest",
        ),
        sa.UniqueConstraint(
            "id",
            "event_id",
            "guest_id",
            name="uq_guest_attendance_records_id_event_guest",
        ),
    )
    op.create_index(
        "ix_guest_attendance_records_event_checked_id",
        "guest_attendance_records",
        ["event_id", "checked_in_at", "id"],
    )

    op.create_table(
        "gate_scan_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=True),
        sa.Column("qr_credential_id", sa.Uuid(), nullable=True),
        sa.Column("attendance_id", sa.Uuid(), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(
                "ADMITTED",
                "ALREADY_CHECKED_IN",
                "UNKNOWN_CREDENTIAL",
                "WRONG_EVENT",
                "INACTIVE_CREDENTIAL",
                "SUPERSEDED_CREDENTIAL",
                "INELIGIBLE_GUEST",
                "COMPANION_LIMIT_EXCEEDED",
                name="gate_scan_outcome",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("requested_companions", sa.SmallInteger(), nullable=False),
        sa.Column("scanned_by", sa.Uuid(), nullable=False),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "credential_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_gate_scan_attempts_credential_fingerprint",
        ),
        sa.CheckConstraint(
            "outcome IN ('ADMITTED', 'ALREADY_CHECKED_IN', 'UNKNOWN_CREDENTIAL', "
            "'WRONG_EVENT', 'INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL', "
            "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED')",
            name="ck_gate_scan_attempts_outcome",
        ),
        sa.CheckConstraint(
            "requested_companions BETWEEN 0 AND 2",
            name="ck_gate_scan_attempts_requested_companions_bounded",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_gate_scan_attempts_guest_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qr_credential_id", "event_id", "guest_id"],
            [
                "guest_qr_credentials.id",
                "guest_qr_credentials.event_id",
                "guest_qr_credentials.guest_id",
            ],
            name="fk_gate_scan_attempts_credential_event_guest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attendance_id", "event_id", "guest_id"],
            [
                "guest_attendance_records.id",
                "guest_attendance_records.event_id",
                "guest_attendance_records.guest_id",
            ],
            name="fk_gate_scan_attempts_attendance_event_guest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["scanned_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gate_scan_attempts_event_scanned_id",
        "gate_scan_attempts",
        ["event_id", "scanned_at", "id"],
    )
    op.create_index(
        "ix_gate_scan_attempts_fingerprint_scanned_id",
        "gate_scan_attempts",
        ["credential_fingerprint", "scanned_at", "id"],
    )
    op.create_index(
        "uq_gate_scan_attempts_one_admitted_attendance",
        "gate_scan_attempts",
        ["attendance_id"],
        unique=True,
        postgresql_where=sa.text("outcome = 'ADMITTED'"),
    )

    op.execute(
        """
        CREATE FUNCTION set_m7_evidence_timestamps()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'guest_attendance_records' THEN
                NEW.checked_in_at := transaction_timestamp();
                NEW.created_at := NEW.checked_in_at;
            ELSE
                NEW.scanned_at := transaction_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guest_attendance_records_server_timestamp
        BEFORE INSERT ON guest_attendance_records
        FOR EACH ROW
        EXECUTE FUNCTION set_m7_evidence_timestamps()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_gate_scan_attempts_server_timestamp
        BEFORE INSERT ON gate_scan_attempts
        FOR EACH ROW
        EXECUTE FUNCTION set_m7_evidence_timestamps()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_guest_attendance_companion_policy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            guest_limit smallint;
            event_limit smallint;
        BEGIN
            SELECT guests.allowed_companions, events.max_companions_per_invitation
              INTO guest_limit, event_limit
              FROM guests
              JOIN events ON events.id = guests.event_id
             WHERE guests.id = NEW.guest_id AND guests.event_id = NEW.event_id;
            IF guest_limit IS NULL OR event_limit IS NULL THEN
                RAISE EXCEPTION 'attendance guest/event policy is unavailable'
                    USING ERRCODE = '23503';
            END IF;
            IF NEW.companions_admitted > guest_limit
               OR NEW.companions_admitted > event_limit
               OR NEW.companions_admitted > 2 THEN
                RAISE EXCEPTION 'attendance companion count exceeds policy'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_attendance_records_companion_policy';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guest_attendance_companion_policy
        BEFORE INSERT ON guest_attendance_records
        FOR EACH ROW
        EXECUTE FUNCTION enforce_guest_attendance_companion_policy()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_m7_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'M7 gate evidence is append-only'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_m7_gate_evidence_immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION require_matching_admitted_scan_attempt()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM gate_scan_attempts
                 WHERE attendance_id = NEW.id
                   AND event_id = NEW.event_id
                   AND guest_id = NEW.guest_id
                   AND qr_credential_id = NEW.first_qr_credential_id
                   AND outcome = 'ADMITTED'
                   AND requested_companions = NEW.companions_admitted
                   AND scanned_by = NEW.checked_in_by
                   AND scanned_at = NEW.checked_in_at
            ) THEN
                RAISE EXCEPTION 'attendance requires exactly one matching admitted scan attempt'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_attendance_records_admitted_attempt';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_guest_attendance_requires_admitted_attempt
        AFTER INSERT ON guest_attendance_records
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION require_matching_admitted_scan_attempt()
        """
    )
    for table_name in ("guest_attendance_records", "gate_scan_attempts"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION reject_m7_evidence_mutation()
            """
        )


def downgrade() -> None:
    """Remove only M7 gate evidence and its protection functions."""

    op.execute("DROP TRIGGER trg_gate_scan_attempts_immutable ON gate_scan_attempts")
    op.execute("DROP TRIGGER trg_gate_scan_attempts_server_timestamp ON gate_scan_attempts")
    op.execute("DROP TRIGGER trg_guest_attendance_records_immutable ON guest_attendance_records")
    op.execute(
        "DROP TRIGGER trg_guest_attendance_records_server_timestamp ON guest_attendance_records"
    )
    op.execute(
        "DROP TRIGGER trg_guest_attendance_requires_admitted_attempt ON guest_attendance_records"
    )
    op.execute("DROP TRIGGER trg_guest_attendance_companion_policy ON guest_attendance_records")
    op.execute("DROP FUNCTION reject_m7_evidence_mutation()")
    op.execute("DROP FUNCTION require_matching_admitted_scan_attempt()")
    op.execute("DROP FUNCTION enforce_guest_attendance_companion_policy()")
    op.execute("DROP FUNCTION set_m7_evidence_timestamps()")
    op.drop_index(
        "ix_gate_scan_attempts_fingerprint_scanned_id",
        table_name="gate_scan_attempts",
    )
    op.drop_index(
        "uq_gate_scan_attempts_one_admitted_attendance",
        table_name="gate_scan_attempts",
    )
    op.drop_index("ix_gate_scan_attempts_event_scanned_id", table_name="gate_scan_attempts")
    op.drop_table("gate_scan_attempts")
    op.drop_index(
        "ix_guest_attendance_records_event_checked_id",
        table_name="guest_attendance_records",
    )
    op.drop_table("guest_attendance_records")
