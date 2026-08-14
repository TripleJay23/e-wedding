"""Canonical administrator identity rules."""

import re

ADMINISTRATOR_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def normalize_administrator_username(value: str) -> str:
    """Normalize and validate the sole administrator login identifier."""
    normalized = value.strip().casefold()
    if not ADMINISTRATOR_USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("administrator username is invalid")
    return normalized
