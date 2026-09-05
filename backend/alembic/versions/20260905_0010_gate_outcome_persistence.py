"""Expand gate outcome persistence for companion lifecycle denials.

Revision ID: 20260905_0010
Revises: 20260905_0009
"""

from alembic import op

revision: str = "20260905_0010"
down_revision: str | None = "20260905_0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Permit the four approved C2 companion denial outcomes."""

    op.drop_constraint(
        "ck_gate_scan_attempts_outcome_references",
        "gate_scan_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_gate_scan_attempts_outcome",
        "gate_scan_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_outcome",
        "gate_scan_attempts",
        "outcome IN ('ADMITTED', 'ALREADY_CHECKED_IN', 'UNKNOWN_CREDENTIAL', "
        "'WRONG_EVENT', 'INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL', "
        "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED', 'PRIMARY_REQUIRED', "
        "'COMPANION_ENTITLEMENT_EXHAUSTED', 'INSUFFICIENT_COMPANION_SLOTS', "
        "'EVENT_NOT_ACTIVE')",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_outcome_references",
        "gate_scan_attempts",
        "(outcome IN ('ADMITTED', 'ALREADY_CHECKED_IN') "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NOT NULL) OR "
        "(outcome IN ('UNKNOWN_CREDENTIAL', 'WRONG_EVENT') "
        "AND guest_id IS NULL AND qr_credential_id IS NULL AND attendance_id IS NULL) OR "
        "(outcome IN ('INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL', "
        "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED', 'PRIMARY_REQUIRED') "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NULL) OR "
        "(outcome IN ('COMPANION_ENTITLEMENT_EXHAUSTED', "
        "'INSUFFICIENT_COMPANION_SLOTS', 'EVENT_NOT_ACTIVE') "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL)",
    )


def downgrade() -> None:
    """Restore legacy constraints only when no new immutable evidence exists."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM gate_scan_attempts
                 WHERE outcome IN (
                    'PRIMARY_REQUIRED',
                    'COMPANION_ENTITLEMENT_EXHAUSTED',
                    'INSUFFICIENT_COMPANION_SLOTS',
                    'EVENT_NOT_ACTIVE'
                 )
            ) THEN
                RAISE EXCEPTION
                    'gate scan outcomes are not legacy-representable; downgrade refused';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "ck_gate_scan_attempts_outcome_references",
        "gate_scan_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_gate_scan_attempts_outcome",
        "gate_scan_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_outcome",
        "gate_scan_attempts",
        "outcome IN ('ADMITTED', 'ALREADY_CHECKED_IN', 'UNKNOWN_CREDENTIAL', "
        "'WRONG_EVENT', 'INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL', "
        "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED')",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_outcome_references",
        "gate_scan_attempts",
        "(outcome IN ('ADMITTED', 'ALREADY_CHECKED_IN') "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NOT NULL) OR "
        "(outcome IN ('UNKNOWN_CREDENTIAL', 'WRONG_EVENT') "
        "AND guest_id IS NULL AND qr_credential_id IS NULL AND attendance_id IS NULL) OR "
        "(outcome IN ('INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL', "
        "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED') "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NULL)",
    )
