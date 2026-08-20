"""Deterministic display-only name policy for personalized cards."""

from __future__ import annotations

from collections.abc import Sequence

HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof"})
FIRST_NAME_EACH = "FIRST_NAME_EACH"
FIRST_AND_LAST = "FIRST_AND_LAST"


class CardNamePolicyError(ValueError):
    """Raised when a safe display name cannot be derived."""


def couple_display_name(full_name: str) -> str:
    """Return the first non-honorific token without changing stored data."""
    return _name_tokens(full_name)[0]


def guest_display_name(full_name: str) -> str:
    """Return one token or the first and final non-honorific tokens."""
    tokens = _name_tokens(full_name)
    if len(tokens) == 1:
        return tokens[0]
    return f"{tokens[0]} {tokens[-1]}"


def apply_display_name_transform(value: object, transform: str) -> object:
    """Apply a configured generic display-name transform to a placeholder."""
    try:
        if transform == FIRST_NAME_EACH:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise CardNamePolicyError("couple display names require an ordered name list")
            names = [couple_display_name(name) for name in value if isinstance(name, str)]
            if len(names) != len(value) or not names:
                raise CardNamePolicyError("couple display names contain an invalid name")
            return names
        if transform == FIRST_AND_LAST:
            if not isinstance(value, str):
                raise CardNamePolicyError("guest display name requires text")
            return guest_display_name(value)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CardNamePolicyError):
            raise
        raise CardNamePolicyError("display name could not be derived") from exc
    raise CardNamePolicyError("display name transform is unsupported")


def _name_tokens(full_name: str) -> tuple[str, ...]:
    if not isinstance(full_name, str):
        raise CardNamePolicyError("display name requires text")
    tokens = full_name.split()
    while tokens and _honorific_key(tokens[0]) in HONORIFICS:
        tokens.pop(0)
    if not tokens:
        raise CardNamePolicyError("display name has no name tokens")
    return tuple(tokens)


def _honorific_key(token: str) -> str:
    candidate = token[:-1] if token.endswith(".") else token
    return candidate.casefold()
