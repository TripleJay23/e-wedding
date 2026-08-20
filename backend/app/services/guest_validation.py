"""Shared canonical guest validation used by manual and Excel workflows."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, TypedDict

from app.models.event import GuestFieldKey

_PHONE_ALLOWED = re.compile(r"^\+?[0-9 ()\-.]+$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


class GuestFieldError(TypedDict):
    """Bounded actionable validation detail without the rejected value."""

    field: str | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class GuestFieldPolicy:
    """One field rule captured from an event or immutable batch snapshot."""

    field_key: GuestFieldKey
    enabled: bool
    required: bool


def error(field: GuestFieldKey | str | None, code: str, message: str) -> GuestFieldError:
    """Build one consistently shaped validation error."""
    return {
        "field": field.value if isinstance(field, GuestFieldKey) else field,
        "code": code,
        "message": message,
    }


def validate_guest_values(
    values: dict[str, Any],
    policies: list[GuestFieldPolicy],
    event_companion_maximum: int,
    *,
    prior_errors: list[GuestFieldError] | None = None,
) -> list[GuestFieldError]:
    """Return deterministic field errors for one normalized guest candidate."""
    errors = list(prior_errors or ())
    policies_by_key = {policy.field_key: policy for policy in policies}

    for policy in policies:
        value = values.get(policy.field_key.value)
        has_value = value is not None and value != ""
        if policy.required and not has_value:
            errors.append(error(policy.field_key, "REQUIRED", "field is required for this event"))
        elif not policy.enabled and has_value:
            if policy.field_key is GuestFieldKey.ALLOWED_COMPANIONS and value == 0:
                continue
            errors.append(
                error(policy.field_key, "FIELD_DISABLED", "field is disabled for this event")
            )

    _validate_text(
        errors,
        values,
        GuestFieldKey.FULL_NAME,
        160,
        policies_by_key,
    )
    _validate_text(
        errors,
        values,
        GuestFieldKey.INVITATION_TYPE,
        64,
        policies_by_key,
        invalid_code="INVALID_INVITATION_TYPE",
    )
    _validate_text(errors, values, GuestFieldKey.TABLE_NUMBER, 64, policies_by_key)
    _validate_text(errors, values, GuestFieldKey.CATEGORY, 64, policies_by_key)

    phone = values.get(GuestFieldKey.PHONE_NUMBER.value)
    if _is_enabled_with_value(GuestFieldKey.PHONE_NUMBER, phone, policies_by_key):
        if not isinstance(phone, str):
            errors.append(error(GuestFieldKey.PHONE_NUMBER, "INVALID_PHONE", "phone is invalid"))
        else:
            digits = "".join(character for character in phone if character.isdigit())
            if not _PHONE_ALLOWED.fullmatch(phone) or not 7 <= len(digits) <= 15:
                errors.append(
                    error(GuestFieldKey.PHONE_NUMBER, "INVALID_PHONE", "phone is invalid")
                )

    email = values.get(GuestFieldKey.EMAIL.value)
    if _is_enabled_with_value(GuestFieldKey.EMAIL, email, policies_by_key) and (
        not isinstance(email, str) or not _valid_email(email)
    ):
        errors.append(error(GuestFieldKey.EMAIL, "INVALID_EMAIL", "email is invalid"))

    companions = values.get(GuestFieldKey.ALLOWED_COMPANIONS.value, 0)
    if not isinstance(companions, int) or isinstance(companions, bool):
        errors.append(
            error(
                GuestFieldKey.ALLOWED_COMPANIONS,
                "INVALID_COMPANION_COUNT",
                "allowed companions must be an integer",
            )
        )
    elif companions < 0:
        errors.append(
            error(
                GuestFieldKey.ALLOWED_COMPANIONS,
                "INVALID_COMPANION_COUNT",
                "allowed companions cannot be negative",
            )
        )
    elif companions > event_companion_maximum:
        errors.append(
            error(
                GuestFieldKey.ALLOWED_COMPANIONS,
                "COMPANION_LIMIT_EXCEEDED",
                "allowed companions exceed the event policy",
            )
        )

    return _deduplicate_errors(errors)


def guest_identity_fingerprints(values: dict[str, Any]) -> tuple[str, ...]:
    """Build privacy-minimal deterministic duplicate identities.

    Email and phone are independent strong identities. When neither is present,
    the normalized full-name and invitation-type pair is the deterministic fallback.
    """
    identities: list[str] = []
    email = values.get(GuestFieldKey.EMAIL.value)
    if isinstance(email, str) and email.strip():
        identities.append(f"email\0{email.strip().casefold()}")

    phone = values.get(GuestFieldKey.PHONE_NUMBER.value)
    if isinstance(phone, str) and phone.strip():
        digits = "".join(character for character in phone if character.isdigit())
        if digits:
            identities.append(f"phone\0{digits}")

    if not identities:
        full_name = values.get(GuestFieldKey.FULL_NAME.value)
        invitation_type = values.get(GuestFieldKey.INVITATION_TYPE.value)
        if isinstance(full_name, str) and isinstance(invitation_type, str):
            identities.append(
                "name-type\0"
                f"{_collapse_whitespace(full_name).casefold()}\0"
                f"{_collapse_whitespace(invitation_type).casefold()}"
            )

    return tuple(hashlib.sha256(identity.encode("utf-8")).hexdigest() for identity in identities)


def _validate_text(
    errors: list[GuestFieldError],
    values: dict[str, Any],
    field_key: GuestFieldKey,
    maximum_length: int,
    policies_by_key: dict[GuestFieldKey, GuestFieldPolicy],
    *,
    invalid_code: str = "INVALID_TEXT",
) -> None:
    value = values.get(field_key.value)
    if not _is_enabled_with_value(field_key, value, policies_by_key):
        return
    if (
        not isinstance(value, str)
        or len(value) > maximum_length
        or _CONTROL_CHARACTER.search(value) is not None
    ):
        errors.append(error(field_key, invalid_code, "field contains invalid text"))


def _is_enabled_with_value(
    field_key: GuestFieldKey,
    value: object,
    policies_by_key: dict[GuestFieldKey, GuestFieldPolicy],
) -> bool:
    policy = policies_by_key.get(field_key)
    return bool(policy and policy.enabled and value is not None and value != "")


def _valid_email(value: str) -> bool:
    if value.count("@") != 1 or any(character.isspace() for character in value):
        return False
    local, domain = value.rsplit("@", 1)
    return bool(local and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _deduplicate_errors(errors: list[GuestFieldError]) -> list[GuestFieldError]:
    seen: set[tuple[str | None, str]] = set()
    result: list[GuestFieldError] = []
    for item in errors:
        key = (item["field"], item["code"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
