"""Shared manual/import guest validation and duplicate identity tests."""

from app.models.event import DEFAULT_GUEST_FIELD_CONFIGURATION, GuestFieldKey
from app.services.guest_validation import (
    GuestFieldPolicy,
    guest_identity_fingerprints,
    validate_guest_values,
)


def policies() -> list[GuestFieldPolicy]:
    return [
        GuestFieldPolicy(field_key, enabled, required)
        for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
    ]


def values() -> dict[str, object]:
    return {
        "full_name": "Synthetic Guest",
        "phone_number": "+255 700 000 001",
        "email": "synthetic@example.test",
        "invitation_type": "Family",
        "allowed_companions": 2,
        "table_number": "T-1",
        "category": "Friends",
    }


def test_shared_validator_accepts_one_canonical_valid_guest() -> None:
    assert validate_guest_values(values(), policies(), 2) == []


def test_shared_validator_returns_multiple_bounded_field_errors() -> None:
    candidate = values()
    candidate.update(
        {
            "full_name": "",
            "phone_number": "123",
            "email": "invalid email",
            "invitation_type": "X" * 65,
            "allowed_companions": 3,
            "category": "bad\x00value",
        }
    )
    errors = validate_guest_values(candidate, policies(), 2)
    codes = {item["code"] for item in errors}
    assert codes >= {
        "REQUIRED",
        "INVALID_PHONE",
        "INVALID_EMAIL",
        "INVALID_INVITATION_TYPE",
        "COMPANION_LIMIT_EXCEEDED",
        "INVALID_TEXT",
    }
    assert all("invalid email" not in item["message"] for item in errors)


def test_disabled_and_required_policy_rules_are_shared() -> None:
    configured = policies()
    configured[1] = GuestFieldPolicy(GuestFieldKey.PHONE_NUMBER, False, False)
    configured[2] = GuestFieldPolicy(GuestFieldKey.EMAIL, True, True)
    candidate = values()
    candidate["email"] = None
    errors = validate_guest_values(candidate, configured, 2)
    assert {(item["field"], item["code"]) for item in errors} >= {
        ("phone_number", "FIELD_DISABLED"),
        ("email", "REQUIRED"),
    }


def test_companion_type_negative_and_prior_error_deduplication() -> None:
    candidate = values()
    candidate["allowed_companions"] = True
    boolean_errors = validate_guest_values(candidate, policies(), 2)
    assert any(item["code"] == "INVALID_COMPANION_COUNT" for item in boolean_errors)

    candidate["allowed_companions"] = -1
    prior = [{"field": "email", "code": "INVALID_EMAIL", "message": "email is invalid"}]
    errors = validate_guest_values(candidate, policies(), 2, prior_errors=prior)  # type: ignore[arg-type]
    assert sum(item["code"] == "INVALID_EMAIL" for item in errors) == 1
    assert any(item["code"] == "INVALID_COMPANION_COUNT" for item in errors)


def test_duplicate_fingerprints_use_email_and_phone_or_name_type_fallback() -> None:
    first = values()
    second = values()
    second["full_name"] = "Different Display Name"
    second["email"] = "SYNTHETIC@EXAMPLE.TEST"
    assert set(guest_identity_fingerprints(first)) & set(guest_identity_fingerprints(second))

    without_contacts = values()
    without_contacts["phone_number"] = None
    without_contacts["email"] = None
    same_normalized = dict(without_contacts)
    same_normalized["full_name"] = "  Synthetic   Guest "
    same_normalized["invitation_type"] = "FAMILY"
    fingerprints = guest_identity_fingerprints(without_contacts)
    assert len(fingerprints) == 1
    assert fingerprints == guest_identity_fingerprints(same_normalized)
    assert all("Synthetic" not in fingerprint for fingerprint in fingerprints)
