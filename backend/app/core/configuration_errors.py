"""Bounded, secret-free diagnostics for configuration loading failures."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from typing import Any, TextIO

from pydantic import ValidationError

_ERROR_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_LOCATION_LABEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_MAX_ERRORS = 20

SENSITIVE_CONFIGURATION_FIELDS = frozenset(
    {
        "ADMIN_BOOTSTRAP_PASSWORD",
        "DATABASE_PASSWORD",
        "DATABASE_URL",
        "JWT_SECRET",
        "POSTGRES_PASSWORD",
        "POSTGRES_STAGING_PASSWORD",
        "STAGING_ADMIN_BOOTSTRAP_PASSWORD",
        "STAGING_MIGRATION_AUTHORIZATION",
    }
)

_SAFE_MESSAGES = {
    "bool_parsing": "Input should be a valid boolean",
    "bool_type": "Input should be a valid boolean",
    "enum": "Input is not an accepted value",
    "extra_forbidden": "Extra configuration input is not permitted",
    "greater_than": "Value is below the permitted minimum",
    "greater_than_equal": "Value is below the permitted minimum",
    "int_parsing": "Input should be a valid integer",
    "int_type": "Input should be a valid integer",
    "less_than": "Value exceeds the permitted maximum",
    "less_than_equal": "Value exceeds the permitted maximum",
    "missing": "Field required",
    "path_type": "Input should be a valid path",
    "string_too_long": "String is too long",
    "string_too_short": "String is too short",
    "string_type": "Input should be a valid string",
    "value_error": "Value does not satisfy configuration requirements",
}


class SafeConfigurationError(RuntimeError):
    """Expose only an already-sanitized configuration diagnostic."""


def _safe_error_type(value: Any) -> str:
    if isinstance(value, str) and _ERROR_TYPE_PATTERN.fullmatch(value) is not None:
        return value
    return "value_error"


def _safe_location(value: Any, *, error_type: str) -> str:
    if not isinstance(value, (list, tuple)):
        return "configuration"

    parts: list[str] = []
    for part in value:
        if isinstance(part, bool):
            parts.append("value")
        elif isinstance(part, int):
            parts.append(str(part))
        elif isinstance(part, str) and _LOCATION_LABEL_PATTERN.fullmatch(part) is not None:
            parts.append(part)
        else:
            parts.append("value")
    if error_type == "extra_forbidden" and parts:
        parts[-1] = "[extra]"
    return ".".join(parts) or "configuration"


def serialize_configuration_validation_errors(
    exc: ValidationError,
) -> list[dict[str, str]]:
    """Allowlist bounded metadata and discard inputs, contexts, URLs, and raw reprs."""

    safe_errors: list[dict[str, str]] = []
    errors: Iterable[dict[str, Any]] = exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    for error in list(errors)[:_MAX_ERRORS]:
        error_type = _safe_error_type(error.get("type"))
        safe_errors.append(
            {
                "type": error_type,
                "location": _safe_location(error.get("loc"), error_type=error_type),
                "message": _SAFE_MESSAGES.get(
                    error_type,
                    "Invalid configuration value",
                ),
            }
        )
    return safe_errors


def format_configuration_validation_error(exc: ValidationError) -> str:
    """Render one deterministic operator diagnostic without rejected values."""

    safe_errors = serialize_configuration_validation_errors(exc)
    lines = ["configuration validation failed"]
    lines.extend(
        "configuration error: "
        f"type={error['type']} "
        f"location={error['location']} "
        f"message={error['message']}"
        for error in safe_errors
    )
    if exc.error_count() > len(safe_errors):
        lines.append("configuration error: additional errors omitted")
    return "\n".join(lines)


def print_configuration_validation_error(
    exc: ValidationError,
    *,
    stream: TextIO | None = None,
) -> None:
    """Write a safe configuration diagnostic to the operator error stream."""

    print(format_configuration_validation_error(exc), file=stream or sys.stderr)


def print_operator_refusal(message: str, *, stream: TextIO | None = None) -> None:
    """Write a bounded refusal whose message is controlled by application code."""

    print(format_operator_refusal(message), file=stream or sys.stderr)


def format_operator_refusal(message: str) -> str:
    """Format a controlled refusal without inspecting exception representations."""

    return f"operation refused: {message}"
