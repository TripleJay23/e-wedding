"""Add current companion entitlement and gate-transition persistence.

Revision ID: 20260905_0009
Revises: 20260821_0008
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0009"
down_revision: str | None = "20260821_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add bounded current state and conservatively backfill legacy evidence."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM guest_attendance_records AS attendance
                  LEFT JOIN guests AS guest
                    ON guest.id = attendance.guest_id
                   AND guest.event_id = attendance.event_id
                 WHERE attendance.companions_admitted NOT BETWEEN 0 AND 2
                    OR guest.id IS NULL
            ) THEN
                RAISE EXCEPTION 'legacy attendance cannot be represented as companion entitlement'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                  FROM guest_attendance_records
                 GROUP BY event_id, guest_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'duplicate legacy primary attendance cannot be backfilled'
                    USING ERRCODE = '23505';
            END IF;
        END;
        $$
        """
    )

    op.create_table(
        "guest_companion_entitlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("primary_attendance_id", sa.Uuid(), nullable=True),
        sa.Column("establishing_correction_id", sa.Uuid(), nullable=True),
        sa.Column("declared_companions", sa.SmallInteger(), nullable=False),
        sa.Column(
            "companions_admitted",
            sa.SmallInteger(),
            server_default=sa.text("0"),
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
            "declared_companions BETWEEN 0 AND 2",
            name="ck_guest_companion_entitlements_declared_bounded",
        ),
        sa.CheckConstraint(
            "companions_admitted >= 0 AND companions_admitted <= declared_companions",
            name="ck_guest_companion_entitlements_admitted_bounded",
        ),
        sa.CheckConstraint(
            "(primary_attendance_id IS NOT NULL AND establishing_correction_id IS NULL) "
            "OR (primary_attendance_id IS NULL AND establishing_correction_id IS NOT NULL)",
            name="ck_guest_companion_entitlements_origin_xor",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_guest_companion_entitlements_guest_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["primary_attendance_id", "event_id", "guest_id"],
            [
                "guest_attendance_records.id",
                "guest_attendance_records.event_id",
                "guest_attendance_records.guest_id",
            ],
            name="fk_guest_companion_entitlements_attendance_event_guest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["establishing_correction_id", "event_id", "guest_id"],
            [
                "guest_attendance_corrections.id",
                "guest_attendance_corrections.event_id",
                "guest_attendance_corrections.guest_id",
            ],
            name="fk_guest_companion_entitlements_correction_event_guest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "guest_id",
            name="uq_guest_companion_entitlements_event_guest",
        ),
    )

    op.execute(
        """
        INSERT INTO guest_companion_entitlements (
            id,
            event_id,
            guest_id,
            primary_attendance_id,
            establishing_correction_id,
            declared_companions,
            companions_admitted
        )
        SELECT
            gen_random_uuid(),
            attendance.event_id,
            attendance.guest_id,
            attendance.id,
            NULL,
            attendance.companions_admitted,
            attendance.companions_admitted
          FROM guest_attendance_records AS attendance
        """
    )
    op.execute(
        """
        WITH latest_correction AS (
            SELECT DISTINCT ON (event_id, guest_id)
                   id,
                   event_id,
                   guest_id,
                   decision,
                   companions_admitted,
                   resulting_effective_status,
                   resulting_effective_source
              FROM guest_attendance_corrections
             ORDER BY event_id, guest_id, revision_number DESC, id DESC
        )
        INSERT INTO guest_companion_entitlements (
            id,
            event_id,
            guest_id,
            primary_attendance_id,
            establishing_correction_id,
            declared_companions,
            companions_admitted
        )
        SELECT
            gen_random_uuid(),
            correction.event_id,
            correction.guest_id,
            NULL,
            correction.id,
            correction.companions_admitted,
            correction.companions_admitted
          FROM latest_correction AS correction
          LEFT JOIN guest_attendance_records AS attendance
            ON attendance.event_id = correction.event_id
           AND attendance.guest_id = correction.guest_id
         WHERE attendance.id IS NULL
           AND correction.decision = 'SET_ATTENDED'
           AND correction.resulting_effective_status = 'ATTENDED'
           AND correction.resulting_effective_source = 'MANUAL'
        """
    )

    op.add_column("gate_scan_attempts", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.add_column(
        "gate_scan_attempts",
        sa.Column(
            "arrival_mode",
            sa.Enum(
                "PRIMARY",
                "COMPANION",
                name="gate_arrival_mode",
                native_enum=False,
                create_constraint=False,
                length=9,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "gate_scan_attempts",
        sa.Column("requested_declared_companions", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "gate_scan_attempts",
        sa.Column("primary_admitted_this_scan", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "gate_scan_attempts",
        sa.Column("companions_admitted_this_scan", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "gate_scan_attempts",
        sa.Column("declared_companions_after", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "gate_scan_attempts",
        sa.Column("companions_admitted_after", sa.SmallInteger(), nullable=True),
    )

    op.execute("ALTER TABLE gate_scan_attempts DISABLE TRIGGER trg_gate_scan_attempts_immutable")
    op.execute(
        """
        UPDATE gate_scan_attempts
           SET request_id = id,
               arrival_mode = 'PRIMARY',
               primary_admitted_this_scan = (outcome = 'ADMITTED')
        """
    )
    op.execute(
        """
        UPDATE gate_scan_attempts AS attempt
           SET companions_admitted_this_scan = attendance.companions_admitted,
               declared_companions_after = attendance.companions_admitted,
               companions_admitted_after = attendance.companions_admitted
          FROM guest_attendance_records AS attendance
         WHERE attempt.outcome = 'ADMITTED'
           AND attempt.attendance_id = attendance.id
           AND attempt.event_id = attendance.event_id
           AND attempt.guest_id = attendance.guest_id
        """
    )
    op.execute("ALTER TABLE gate_scan_attempts ENABLE TRIGGER trg_gate_scan_attempts_immutable")
    op.alter_column(
        "gate_scan_attempts",
        "request_id",
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )
    op.alter_column(
        "gate_scan_attempts",
        "arrival_mode",
        nullable=False,
        server_default=sa.text("'PRIMARY'"),
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_arrival_mode",
        "gate_scan_attempts",
        "arrival_mode IN ('PRIMARY', 'COMPANION')",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_requested_declaration_bounded",
        "gate_scan_attempts",
        "requested_declared_companions IS NULL OR requested_declared_companions BETWEEN 0 AND 2",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_companion_delta_bounded",
        "gate_scan_attempts",
        "companions_admitted_this_scan IS NULL OR companions_admitted_this_scan BETWEEN 0 AND 2",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_post_entitlement_state",
        "gate_scan_attempts",
        "(declared_companions_after IS NULL AND companions_admitted_after IS NULL) OR "
        "(declared_companions_after IS NOT NULL "
        "AND companions_admitted_after IS NOT NULL "
        "AND companions_admitted_after >= 0 "
        "AND companions_admitted_after <= declared_companions_after "
        "AND declared_companions_after <= 2)",
    )
    op.create_unique_constraint(
        "uq_gate_scan_attempts_event_request",
        "gate_scan_attempts",
        ["event_id", "request_id"],
    )
    op.drop_index(
        "uq_gate_scan_attempts_one_admitted_attendance",
        table_name="gate_scan_attempts",
    )
    op.create_index(
        "uq_gate_scan_attempts_one_primary_admitted_attendance",
        "gate_scan_attempts",
        ["attendance_id"],
        unique=True,
        postgresql_where=sa.text("outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY'"),
    )


def downgrade() -> None:
    """Refuse lossy rollback, then remove only C2.2 persistence."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM guest_companion_entitlements AS entitlement
                  LEFT JOIN guest_attendance_records AS attendance
                    ON attendance.id = entitlement.primary_attendance_id
                   AND attendance.event_id = entitlement.event_id
                   AND attendance.guest_id = entitlement.guest_id
                  LEFT JOIN guest_attendance_corrections AS correction
                    ON correction.id = entitlement.establishing_correction_id
                   AND correction.event_id = entitlement.event_id
                   AND correction.guest_id = entitlement.guest_id
                 WHERE NOT (
                    (
                        entitlement.primary_attendance_id IS NOT NULL
                        AND entitlement.establishing_correction_id IS NULL
                        AND attendance.id IS NOT NULL
                        AND entitlement.declared_companions = attendance.companions_admitted
                        AND entitlement.companions_admitted = attendance.companions_admitted
                    )
                    OR
                    (
                        entitlement.primary_attendance_id IS NULL
                        AND entitlement.establishing_correction_id IS NOT NULL
                        AND correction.id IS NOT NULL
                        AND correction.decision = 'SET_ATTENDED'
                        AND correction.companions_admitted = entitlement.declared_companions
                        AND entitlement.companions_admitted = entitlement.declared_companions
                        AND NOT EXISTS (
                            SELECT 1
                              FROM guest_attendance_corrections AS later_correction
                             WHERE later_correction.event_id = entitlement.event_id
                               AND later_correction.guest_id = entitlement.guest_id
                               AND later_correction.revision_number > correction.revision_number
                        )
                    )
                 )
            ) THEN
                RAISE EXCEPTION
                    'C2.2 downgrade refused: entitlement state is not legacy-representable'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                  FROM gate_scan_attempts AS attempt
                  LEFT JOIN guest_attendance_records AS attendance
                    ON attendance.id = attempt.attendance_id
                   AND attendance.event_id = attempt.event_id
                   AND attendance.guest_id = attempt.guest_id
                 WHERE attempt.arrival_mode <> 'PRIMARY'
                    OR attempt.requested_declared_companions IS NOT NULL
                    OR (
                        attempt.outcome = 'ADMITTED'
                        AND NOT (
                            (
                                attempt.primary_admitted_this_scan IS NULL
                                AND attempt.companions_admitted_this_scan IS NULL
                                AND attempt.declared_companions_after IS NULL
                                AND attempt.companions_admitted_after IS NULL
                            )
                            OR (
                                attendance.id IS NOT NULL
                                AND attempt.primary_admitted_this_scan IS TRUE
                                AND attempt.companions_admitted_this_scan
                                   = attendance.companions_admitted
                                AND attempt.declared_companions_after
                                   = attendance.companions_admitted
                                AND attempt.companions_admitted_after
                                   = attendance.companions_admitted
                            )
                        )
                    )
                    OR (
                        attempt.outcome <> 'ADMITTED'
                        AND NOT (
                            attempt.primary_admitted_this_scan IS NULL
                            AND attempt.companions_admitted_this_scan IS NULL
                            AND attempt.declared_companions_after IS NULL
                            AND attempt.companions_admitted_after IS NULL
                            OR
                            attempt.primary_admitted_this_scan IS FALSE
                            AND attempt.companions_admitted_this_scan IS NULL
                            AND attempt.declared_companions_after IS NULL
                            AND attempt.companions_admitted_after IS NULL
                        )
                    )
            ) THEN
                RAISE EXCEPTION
                    'C2.2 downgrade refused: scan state is not legacy-representable'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )

    op.drop_index(
        "uq_gate_scan_attempts_one_primary_admitted_attendance",
        table_name="gate_scan_attempts",
    )
    op.create_index(
        "uq_gate_scan_attempts_one_admitted_attendance",
        "gate_scan_attempts",
        ["attendance_id"],
        unique=True,
        postgresql_where=sa.text("outcome = 'ADMITTED'"),
    )
    op.drop_constraint(
        "uq_gate_scan_attempts_event_request",
        "gate_scan_attempts",
        type_="unique",
    )
    op.drop_column("gate_scan_attempts", "companions_admitted_after")
    op.drop_column("gate_scan_attempts", "declared_companions_after")
    op.drop_column("gate_scan_attempts", "companions_admitted_this_scan")
    op.drop_column("gate_scan_attempts", "primary_admitted_this_scan")
    op.drop_column("gate_scan_attempts", "requested_declared_companions")
    op.drop_column("gate_scan_attempts", "arrival_mode")
    op.drop_column("gate_scan_attempts", "request_id")
    op.drop_table("guest_companion_entitlements")
