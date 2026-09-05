"""C2.2A model-level gate outcome persistence contracts."""

from sqlalchemy import CheckConstraint

from app.models.attendance import GateScanAttempt, GateScanOutcome

LEGACY_OUTCOMES = {
    "ADMITTED",
    "ALREADY_CHECKED_IN",
    "UNKNOWN_CREDENTIAL",
    "WRONG_EVENT",
    "INACTIVE_CREDENTIAL",
    "SUPERSEDED_CREDENTIAL",
    "INELIGIBLE_GUEST",
    "COMPANION_LIMIT_EXCEEDED",
}
NEW_OUTCOMES = {
    "PRIMARY_REQUIRED",
    "COMPANION_ENTITLEMENT_EXHAUSTED",
    "INSUFFICIENT_COMPANION_SLOTS",
    "EVENT_NOT_ACTIVE",
}


def _checks() -> dict[str | None, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in GateScanAttempt.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_gate_outcome_enum_preserves_legacy_and_adds_only_approved_values() -> None:
    assert {outcome.value for outcome in GateScanOutcome} == LEGACY_OUTCOMES | NEW_OUTCOMES


def test_outcome_constraint_matches_the_complete_approved_set() -> None:
    outcome_check = _checks()["ck_gate_scan_attempts_outcome"]

    for outcome in LEGACY_OUTCOMES | NEW_OUTCOMES:
        assert f"'{outcome}'" in outcome_check
    assert "EXPIRED" not in outcome_check
    assert "CORRECTION" not in outcome_check


def test_reference_constraint_keeps_privacy_and_invitation_shapes_distinct() -> None:
    reference_check = _checks()["ck_gate_scan_attempts_outcome_references"]

    assert "'UNKNOWN_CREDENTIAL', 'WRONG_EVENT'" in reference_check
    assert "guest_id IS NULL AND qr_credential_id IS NULL AND attendance_id IS NULL" in (
        reference_check
    )
    assert "'COMPANION_LIMIT_EXCEEDED', 'PRIMARY_REQUIRED'" in reference_check
    assert "guest_id IS NOT NULL AND qr_credential_id IS NOT NULL" in reference_check
    assert "'COMPANION_ENTITLEMENT_EXHAUSTED'" in reference_check
    assert "'INSUFFICIENT_COMPANION_SLOTS', 'EVENT_NOT_ACTIVE'" in reference_check
