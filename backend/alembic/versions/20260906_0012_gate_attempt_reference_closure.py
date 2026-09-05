"""Permit truthful correction-origin repeated-primary evidence.

Revision ID: 20260906_0012
Revises: 20260905_0011
"""

from alembic import op

revision: str = "20260906_0012"
down_revision: str | None = "20260905_0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Make ALREADY_CHECKED_IN attendance references origin-aware."""

    op.drop_constraint(
        "ck_gate_scan_attempts_outcome_references",
        "gate_scan_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_gate_scan_attempts_outcome_references",
        "gate_scan_attempts",
        "(outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NOT NULL) OR "
        "(outcome = 'ADMITTED' AND arrival_mode = 'COMPANION' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL) OR "
        "(outcome = 'ALREADY_CHECKED_IN' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL) OR "
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
    """Restore 0011 only when immutable evidence remains representable."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM gate_scan_attempts
                 WHERE outcome = 'ALREADY_CHECKED_IN'
                   AND attendance_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'C2.2C downgrade refused: immutable correction-origin '
                    'repeated-primary evidence is not 0011-representable'
                    USING ERRCODE = '23514';
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
    op.create_check_constraint(
        "ck_gate_scan_attempts_outcome_references",
        "gate_scan_attempts",
        "(outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NOT NULL) OR "
        "(outcome = 'ADMITTED' AND arrival_mode = 'COMPANION' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL) OR "
        "(outcome = 'ALREADY_CHECKED_IN' "
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
