"""Add M8 append-only attendance corrections and deterministic chain guards.

Revision ID: 20260821_0008
Revises: 20260821_0007
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0008"
down_revision: str | None = "20260821_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the correction chain without changing any M7 evidence table."""

    op.create_table(
        "guest_attendance_corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("previous_correction_id", sa.Uuid(), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "SET_ATTENDED",
                "SET_NOT_ATTENDED",
                "RESTORE_GATE_DERIVED",
                name="attendance_correction_decision",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "reason_code",
            sa.Enum(
                "MISSED_GATE_SCAN",
                "ERRONEOUS_GATE_ADMISSION",
                "OPERATOR_ERROR",
                "POST_EVENT_RECONCILIATION",
                "OTHER",
                name="attendance_correction_reason",
                native_enum=False,
                create_constraint=False,
                length=25,
            ),
            nullable=False,
        ),
        sa.Column("reason_note", sa.String(length=500), nullable=True),
        sa.Column("companions_admitted", sa.SmallInteger(), nullable=True),
        sa.Column("gate_attendance_record_id", sa.Uuid(), nullable=True),
        sa.Column(
            "previous_effective_status",
            sa.Enum(
                "ATTENDED",
                "NOT_ATTENDED",
                name="effective_attendance_status",
                native_enum=False,
                create_constraint=False,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column(
            "resulting_effective_status",
            sa.Enum(
                "ATTENDED",
                "NOT_ATTENDED",
                name="effective_attendance_status",
                native_enum=False,
                create_constraint=False,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column(
            "resulting_effective_source",
            sa.Enum(
                "GATE",
                "MANUAL",
                "NONE",
                name="effective_attendance_source",
                native_enum=False,
                create_constraint=False,
                length=6,
            ),
            nullable=False,
        ),
        sa.Column("corrected_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number >= 1",
            name="ck_guest_attendance_corrections_revision_positive",
        ),
        sa.CheckConstraint(
            "decision IN ('SET_ATTENDED', 'SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED')",
            name="ck_guest_attendance_corrections_decision",
        ),
        sa.CheckConstraint(
            "reason_code IN ('MISSED_GATE_SCAN', 'ERRONEOUS_GATE_ADMISSION', "
            "'OPERATOR_ERROR', 'POST_EVENT_RECONCILIATION', 'OTHER')",
            name="ck_guest_attendance_corrections_reason",
        ),
        sa.CheckConstraint(
            "previous_effective_status IN ('ATTENDED', 'NOT_ATTENDED')",
            name="ck_guest_attendance_corrections_previous_status",
        ),
        sa.CheckConstraint(
            "resulting_effective_status IN ('ATTENDED', 'NOT_ATTENDED')",
            name="ck_guest_attendance_corrections_resulting_status",
        ),
        sa.CheckConstraint(
            "resulting_effective_source IN ('GATE', 'MANUAL', 'NONE')",
            name="ck_guest_attendance_corrections_resulting_source",
        ),
        sa.CheckConstraint(
            "previous_correction_id IS NULL OR previous_correction_id <> id",
            name="ck_guest_attendance_corrections_previous_not_self",
        ),
        sa.CheckConstraint(
            "(revision_number = 1 AND previous_correction_id IS NULL) OR "
            "(revision_number > 1 AND previous_correction_id IS NOT NULL)",
            name="ck_guest_attendance_corrections_revision_link",
        ),
        sa.CheckConstraint(
            "reason_note IS NULL OR (reason_note = btrim(reason_note) "
            "AND char_length(reason_note) BETWEEN 1 AND 500 "
            "AND reason_note !~ '[[:cntrl:]]')",
            name="ck_guest_attendance_corrections_reason_note",
        ),
        sa.CheckConstraint(
            "reason_code <> 'OTHER' OR reason_note IS NOT NULL",
            name="ck_guest_attendance_corrections_other_note",
        ),
        sa.CheckConstraint(
            "companions_admitted IS NULL OR companions_admitted BETWEEN 0 AND 2",
            name="ck_guest_attendance_corrections_companions_bounded",
        ),
        sa.CheckConstraint(
            "(decision = 'SET_ATTENDED' AND companions_admitted IS NOT NULL) OR "
            "(decision IN ('SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED') "
            "AND companions_admitted IS NULL)",
            name="ck_guest_attendance_corrections_decision_companions",
        ),
        sa.CheckConstraint(
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
            "AND resulting_effective_source = 'NONE')))",
            name="ck_guest_attendance_corrections_decision_result",
        ),
        sa.ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_guest_attendance_corrections_guest_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["gate_attendance_record_id", "event_id", "guest_id"],
            [
                "guest_attendance_records.id",
                "guest_attendance_records.event_id",
                "guest_attendance_records.guest_id",
            ],
            name="fk_guest_attendance_corrections_attendance_event_guest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_correction_id", "event_id", "guest_id"],
            [
                "guest_attendance_corrections.id",
                "guest_attendance_corrections.event_id",
                "guest_attendance_corrections.guest_id",
            ],
            name="fk_guest_attendance_corrections_previous_event_guest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["corrected_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "guest_id",
            "revision_number",
            name="uq_guest_attendance_corrections_event_guest_revision",
        ),
        sa.UniqueConstraint(
            "previous_correction_id",
            name="uq_guest_attendance_corrections_previous",
        ),
        sa.UniqueConstraint(
            "id",
            "event_id",
            "guest_id",
            name="uq_guest_attendance_corrections_id_event_guest",
        ),
    )
    op.create_index(
        "ix_guest_attendance_corrections_guest_revision_id",
        "guest_attendance_corrections",
        ["event_id", "guest_id", "revision_number", "id"],
    )
    op.create_index(
        "ix_guest_attendance_corrections_event_revision_id",
        "guest_attendance_corrections",
        ["event_id", "revision_number", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION enforce_guest_attendance_correction_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            selected_event_status text;
            selected_event_limit smallint;
            selected_guest_status text;
            selected_guest_limit smallint;
            current_attendance_id uuid;
            latest_correction_id uuid;
            latest_revision integer;
            latest_decision text;
            current_effective_status text;
            current_effective_source text;
        BEGIN
            NEW.created_at := transaction_timestamp();

            SELECT status, max_companions_per_invitation
              INTO selected_event_status, selected_event_limit
              FROM events
             WHERE id = NEW.event_id
             FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'attendance correction event is unavailable'
                    USING ERRCODE = '23503';
            END IF;
            IF selected_event_status NOT IN ('ACTIVE', 'COMPLETED') THEN
                RAISE EXCEPTION 'attendance correction event is ineligible'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_attendance_corrections_event_eligible';
            END IF;

            SELECT status, allowed_companions
              INTO selected_guest_status, selected_guest_limit
              FROM guests
             WHERE id = NEW.guest_id AND event_id = NEW.event_id
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'attendance correction guest is unavailable'
                    USING ERRCODE = '23503';
            END IF;

            SELECT id
              INTO current_attendance_id
              FROM guest_attendance_records
             WHERE event_id = NEW.event_id AND guest_id = NEW.guest_id;

            SELECT id, revision_number, decision
              INTO latest_correction_id, latest_revision, latest_decision
              FROM guest_attendance_corrections
             WHERE event_id = NEW.event_id AND guest_id = NEW.guest_id
             ORDER BY revision_number DESC, id DESC
             LIMIT 1;

            IF latest_correction_id IS NULL THEN
                IF NEW.revision_number IS DISTINCT FROM 1
                   OR NEW.previous_correction_id IS NOT NULL THEN
                    RAISE EXCEPTION 'attendance correction revision is invalid'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_guest_attendance_corrections_chain_guard';
                END IF;
            ELSIF NEW.revision_number IS DISTINCT FROM latest_revision + 1
                  OR NEW.previous_correction_id IS DISTINCT FROM latest_correction_id THEN
                RAISE EXCEPTION 'attendance correction revision is invalid'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_attendance_corrections_chain_guard';
            END IF;

            IF latest_decision = 'SET_ATTENDED' THEN
                current_effective_status := 'ATTENDED';
                current_effective_source := 'MANUAL';
            ELSIF latest_decision = 'SET_NOT_ATTENDED' THEN
                current_effective_status := 'NOT_ATTENDED';
                current_effective_source := 'MANUAL';
            ELSIF current_attendance_id IS NOT NULL THEN
                current_effective_status := 'ATTENDED';
                current_effective_source := 'GATE';
            ELSE
                current_effective_status := 'NOT_ATTENDED';
                current_effective_source := 'NONE';
            END IF;

            IF NEW.previous_effective_status IS DISTINCT FROM current_effective_status
               OR NEW.gate_attendance_record_id IS DISTINCT FROM current_attendance_id THEN
                RAISE EXCEPTION 'attendance correction snapshot is invalid'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_attendance_corrections_snapshot_guard';
            END IF;

            IF NEW.decision = 'SET_ATTENDED' THEN
                IF current_effective_status <> 'NOT_ATTENDED'
                   OR selected_guest_status <> 'ACTIVE'
                   OR NEW.companions_admitted IS NULL
                   OR NEW.companions_admitted > selected_guest_limit
                   OR NEW.companions_admitted > selected_event_limit
                   OR NEW.companions_admitted > 2
                   OR NEW.resulting_effective_status <> 'ATTENDED'
                   OR NEW.resulting_effective_source <> 'MANUAL' THEN
                    RAISE EXCEPTION 'attendance correction transition is invalid'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_guest_attendance_corrections_transition_guard';
                END IF;
            ELSIF NEW.decision = 'SET_NOT_ATTENDED' THEN
                IF current_effective_status <> 'ATTENDED'
                   OR NEW.companions_admitted IS NOT NULL
                   OR NEW.resulting_effective_status <> 'NOT_ATTENDED'
                   OR NEW.resulting_effective_source <> 'MANUAL' THEN
                    RAISE EXCEPTION 'attendance correction transition is invalid'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_guest_attendance_corrections_transition_guard';
                END IF;
            ELSIF NEW.decision = 'RESTORE_GATE_DERIVED' THEN
                IF current_effective_source <> 'MANUAL'
                   OR NEW.companions_admitted IS NOT NULL
                   OR (current_attendance_id IS NOT NULL AND
                       (NEW.resulting_effective_status <> 'ATTENDED'
                        OR NEW.resulting_effective_source <> 'GATE'))
                   OR (current_attendance_id IS NULL AND
                       (NEW.resulting_effective_status <> 'NOT_ATTENDED'
                        OR NEW.resulting_effective_source <> 'NONE')) THEN
                    RAISE EXCEPTION 'attendance correction transition is invalid'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_guest_attendance_corrections_transition_guard';
                END IF;
            ELSE
                RAISE EXCEPTION 'attendance correction decision is invalid'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_guest_attendance_corrections_transition_guard';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guest_attendance_corrections_insert_guard
        BEFORE INSERT ON guest_attendance_corrections
        FOR EACH ROW
        EXECUTE FUNCTION enforce_guest_attendance_correction_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_guest_attendance_correction_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'attendance corrections are append-only'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_guest_attendance_corrections_append_only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guest_attendance_corrections_append_only
        BEFORE UPDATE OR DELETE ON guest_attendance_corrections
        FOR EACH ROW
        EXECUTE FUNCTION reject_guest_attendance_correction_mutation()
        """
    )


def downgrade() -> None:
    """Remove only M8 correction decisions and their direct guards."""

    op.execute(
        "DROP TRIGGER trg_guest_attendance_corrections_append_only ON guest_attendance_corrections"
    )
    op.execute(
        "DROP TRIGGER trg_guest_attendance_corrections_insert_guard ON guest_attendance_corrections"
    )
    op.execute("DROP FUNCTION reject_guest_attendance_correction_mutation()")
    op.execute("DROP FUNCTION enforce_guest_attendance_correction_insert()")
    op.drop_index(
        "ix_guest_attendance_corrections_event_revision_id",
        table_name="guest_attendance_corrections",
    )
    op.drop_index(
        "ix_guest_attendance_corrections_guest_revision_id",
        table_name="guest_attendance_corrections",
    )
    op.drop_table("guest_attendance_corrections")
