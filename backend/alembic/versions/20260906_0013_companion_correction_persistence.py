"""Add structured companion snapshots to the append-only correction chain.

Revision ID: 20260906_0013
Revises: 20260906_0012
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0013"
down_revision: str | None = "20260906_0012"
branch_labels: str | None = None
depends_on: str | None = None


_INSERT_GUARD_0013 = """
CREATE OR REPLACE FUNCTION enforce_guest_attendance_correction_insert()
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
    latest_resulting_status text;
    latest_resulting_source text;
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

    SELECT id, revision_number, decision,
           resulting_effective_status, resulting_effective_source
      INTO latest_correction_id, latest_revision, latest_decision,
           latest_resulting_status, latest_resulting_source
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
    ELSIF latest_decision = 'SET_COMPANION_STATE' THEN
        current_effective_status := latest_resulting_status;
        current_effective_source := latest_resulting_source;
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
    ELSIF NEW.decision = 'SET_COMPANION_STATE' THEN
        IF NEW.companions_admitted IS NOT NULL
           OR NEW.resulting_effective_status IS DISTINCT FROM current_effective_status
           OR NEW.resulting_effective_source IS DISTINCT FROM current_effective_source THEN
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


_INSERT_GUARD_0012 = (
    _INSERT_GUARD_0013.replace(
        "    latest_resulting_status text;\n    latest_resulting_source text;\n",
        "",
    )
    .replace(
        "    SELECT id, revision_number, decision,\n"
        "           resulting_effective_status, resulting_effective_source\n"
        "      INTO latest_correction_id, latest_revision, latest_decision,\n"
        "           latest_resulting_status, latest_resulting_source\n",
        "    SELECT id, revision_number, decision\n"
        "      INTO latest_correction_id, latest_revision, latest_decision\n",
    )
    .replace(
        "    ELSIF latest_decision = 'SET_COMPANION_STATE' THEN\n"
        "        current_effective_status := latest_resulting_status;\n"
        "        current_effective_source := latest_resulting_source;\n",
        "",
    )
    .replace(
        "    ELSIF NEW.decision = 'SET_COMPANION_STATE' THEN\n"
        "        IF NEW.companions_admitted IS NOT NULL\n"
        "           OR NEW.resulting_effective_status IS DISTINCT FROM current_effective_status\n"
        "           OR NEW.resulting_effective_source IS DISTINCT FROM "
        "current_effective_source THEN\n"
        "            RAISE EXCEPTION 'attendance correction transition is invalid'\n"
        "                USING ERRCODE = '23514',\n"
        "                      CONSTRAINT = 'ck_guest_attendance_corrections_transition_guard';\n"
        "        END IF;\n",
        "",
    )
)


def upgrade() -> None:
    """Add only the structured correction snapshot persistence contract."""

    for name in (
        "previous_declared_companions",
        "resulting_declared_companions",
        "previous_companions_admitted",
        "resulting_companions_admitted",
    ):
        op.add_column(
            "guest_attendance_corrections",
            sa.Column(name, sa.SmallInteger(), nullable=True),
        )

    for name in (
        "ck_guest_attendance_corrections_decision",
        "ck_guest_attendance_corrections_decision_companions",
        "ck_guest_attendance_corrections_decision_result",
    ):
        op.drop_constraint(name, "guest_attendance_corrections", type_="check")

    op.create_check_constraint(
        "ck_guest_attendance_corrections_decision",
        "guest_attendance_corrections",
        "decision IN ('SET_ATTENDED', 'SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED', "
        "'SET_COMPANION_STATE')",
    )
    op.create_check_constraint(
        "ck_guest_attendance_corrections_decision_companions",
        "guest_attendance_corrections",
        "(decision = 'SET_ATTENDED' AND companions_admitted IS NOT NULL) OR "
        "(decision IN ('SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED', "
        "'SET_COMPANION_STATE') AND companions_admitted IS NULL)",
    )
    op.create_check_constraint(
        "ck_guest_attendance_corrections_companion_snapshot_shape",
        "guest_attendance_corrections",
        "(decision = 'SET_COMPANION_STATE' "
        "AND previous_declared_companions IS NOT NULL "
        "AND previous_companions_admitted IS NOT NULL "
        "AND resulting_declared_companions IS NOT NULL "
        "AND resulting_companions_admitted IS NOT NULL) OR "
        "(decision IN ('SET_ATTENDED', 'SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED') "
        "AND previous_declared_companions IS NULL "
        "AND previous_companions_admitted IS NULL "
        "AND resulting_declared_companions IS NULL "
        "AND resulting_companions_admitted IS NULL)",
    )
    op.create_check_constraint(
        "ck_guest_attendance_corrections_previous_companion_state",
        "guest_attendance_corrections",
        "previous_declared_companions IS NULL OR "
        "(previous_companions_admitted >= 0 "
        "AND previous_companions_admitted <= previous_declared_companions "
        "AND previous_declared_companions <= 2)",
    )
    op.create_check_constraint(
        "ck_guest_attendance_corrections_resulting_companion_state",
        "guest_attendance_corrections",
        "resulting_declared_companions IS NULL OR "
        "(resulting_companions_admitted >= 0 "
        "AND resulting_companions_admitted <= resulting_declared_companions "
        "AND resulting_declared_companions <= 2)",
    )
    op.create_check_constraint(
        "ck_guest_attendance_corrections_decision_result",
        "guest_attendance_corrections",
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
        "AND resulting_effective_source = 'NONE'))) OR "
        "(decision = 'SET_COMPANION_STATE' "
        "AND resulting_effective_status = previous_effective_status)",
    )
    op.execute(_INSERT_GUARD_0013)


def downgrade() -> None:
    """Restore 0012 only when no structured correction evidence would be lost."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM guest_attendance_corrections
                 WHERE decision = 'SET_COMPANION_STATE'
                    OR previous_declared_companions IS NOT NULL
                    OR resulting_declared_companions IS NOT NULL
                    OR previous_companions_admitted IS NOT NULL
                    OR resulting_companions_admitted IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'C2.5A downgrade refused: companion correction evidence is not '
                    '0012-representable'
                    USING ERRCODE = '23514';
            END IF;
        END
        $$
        """
    )
    op.execute(_INSERT_GUARD_0012)

    for name in (
        "ck_guest_attendance_corrections_decision_result",
        "ck_guest_attendance_corrections_resulting_companion_state",
        "ck_guest_attendance_corrections_previous_companion_state",
        "ck_guest_attendance_corrections_companion_snapshot_shape",
        "ck_guest_attendance_corrections_decision_companions",
        "ck_guest_attendance_corrections_decision",
    ):
        op.drop_constraint(name, "guest_attendance_corrections", type_="check")

    op.create_check_constraint(
        "ck_guest_attendance_corrections_decision",
        "guest_attendance_corrections",
        "decision IN ('SET_ATTENDED', 'SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED')",
    )
    op.create_check_constraint(
        "ck_guest_attendance_corrections_decision_companions",
        "guest_attendance_corrections",
        "(decision = 'SET_ATTENDED' AND companions_admitted IS NOT NULL) OR "
        "(decision IN ('SET_NOT_ATTENDED', 'RESTORE_GATE_DERIVED') "
        "AND companions_admitted IS NULL)",
    )
    op.create_check_constraint(
        "ck_guest_attendance_corrections_decision_result",
        "guest_attendance_corrections",
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
    )

    for name in (
        "resulting_companions_admitted",
        "previous_companions_admitted",
        "resulting_declared_companions",
        "previous_declared_companions",
    ):
        op.drop_column("guest_attendance_corrections", name)
