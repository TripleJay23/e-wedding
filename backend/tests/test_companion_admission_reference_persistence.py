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


def test_primary_requires_attendance_and_already_checked_in_allows_both_origins() -> None:
    reference_check = _reference_check()

    assert (
        "outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY' "
        "AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL "
        "AND attendance_id IS NOT NULL"
    ) in reference_check
    assert (
        "outcome = 'ALREADY_CHECKED_IN' AND guest_id IS NOT NULL AND qr_credential_id IS NOT NULL"
    ) in reference_check
    already_branch = reference_check.split("(outcome = 'ALREADY_CHECKED_IN'", maxsplit=1)[1]
    already_branch = already_branch.split(") OR", maxsplit=1)[0]
    assert "attendance_id IS NOT NULL" not in already_branch


def test_complete_c23_attempt_reference_matrix_is_representable() -> None:
    reference_check = _reference_check()

    required_branches = {
        "admitted_primary_gate": (
            "outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY'",
            "attendance_id IS NOT NULL",
        ),
        "admitted_companion_both_origins": (
            "outcome = 'ADMITTED' AND arrival_mode = 'COMPANION'",
            "guest_id IS NOT NULL AND qr_credential_id IS NOT NULL",
        ),
        "already_primary_both_origins": (
            "outcome = 'ALREADY_CHECKED_IN'",
            "guest_id IS NOT NULL AND qr_credential_id IS NOT NULL",
        ),
        "primary_required": ("'COMPANION_LIMIT_EXCEEDED', 'PRIMARY_REQUIRED'",),
        "exhausted": ("'COMPANION_ENTITLEMENT_EXHAUSTED'",),
        "insufficient_and_event_status": ("'INSUFFICIENT_COMPANION_SLOTS', 'EVENT_NOT_ACTIVE'",),
        "privacy": (
            "'UNKNOWN_CREDENTIAL', 'WRONG_EVENT'",
            "guest_id IS NULL AND qr_credential_id IS NULL AND attendance_id IS NULL",
        ),
        "credential_and_guest_lifecycle": (
            "'INACTIVE_CREDENTIAL', 'SUPERSEDED_CREDENTIAL'",
            "'INELIGIBLE_GUEST', 'COMPANION_LIMIT_EXCEEDED', 'PRIMARY_REQUIRED'",
        ),
    }
    for required_fragments in required_branches.values():
        assert all(fragment in reference_check for fragment in required_fragments)


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
