"""Support attendance-optional companion admission references.

Revision ID: 20260905_0011
Revises: 20260905_0010
"""

from alembic import op

revision: str = "20260905_0011"
down_revision: str | None = "20260905_0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Make admitted references arrival-mode aware."""

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


def downgrade() -> None:
    """Restore 0010 references only when immutable evidence is representable."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM gate_scan_attempts
                 WHERE outcome = 'ADMITTED'
                   AND arrival_mode = 'COMPANION'
                   AND attendance_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'companion admission references are not legacy-representable; '
                    'downgrade refused';
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
