"""C2.2B model-level companion admission reference contracts."""

from sqlalchemy import CheckConstraint

from app.models.attendance import GateScanAttempt


def _reference_check() -> str:
    return next(
        str(constraint.sqltext)
        for constraint in GateScanAttempt.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_gate_scan_attempts_outcome_references"
    )


def test_admitted_references_are_arrival_mode_aware() -> None:
    reference_check = _reference_check()

    assert "outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY'" in reference_check
    assert "outcome = 'ADMITTED' AND arrival_mode = 'COMPANION'" in reference_check
    assert reference_check.count("guest_id IS NOT NULL AND qr_credential_id IS NOT NULL") == 5


def test_primary_and_already_checked_in_still_require_attendance() -> None:
    reference_check = _reference_check()

    assert (
        "outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NOT NULL"
    ) in reference_check
    assert (
        "outcome = 'ALREADY_CHECKED_IN' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NOT NULL"
    ) in reference_check


def test_companion_admission_requires_invitation_but_not_attendance() -> None:
    reference_check = _reference_check()

    companion_branch = (
        "outcome = 'ADMITTED' AND arrival_mode = 'COMPANION' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL"
    )
    assert companion_branch in reference_check
    assert f"{companion_branch} AND attendance_id IS NOT NULL" not in reference_check


def test_all_non_admitted_reference_rules_are_preserved() -> None:
    reference_check = _reference_check()

    assert "'UNKNOWN_CREDENTIAL', 'WRONG_EVENT'" in reference_check
    assert "guest_id IS NULL AND qr_credential_id IS NULL AND attendance_id IS NULL" in (
        reference_check
    )
    assert "'COMPANION_LIMIT_EXCEEDED', 'PRIMARY_REQUIRED'" in reference_check
    assert "'COMPANION_ENTITLEMENT_EXHAUSTED'" in reference_check
    assert "'INSUFFICIENT_COMPANION_SLOTS', 'EVENT_NOT_ACTIVE'" in reference_check
