"""Central privacy-safe API error serialization."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_ERROR_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_LOCATION_LABEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")

_SAFE_MESSAGES = {
    "bool_parsing": "Input should be a valid boolean",
    "bool_type": "Input should be a valid boolean",
    "bytes_too_long": "Byte content is too long",
    "bytes_too_short": "Byte content is too short",
    "datetime_parsing": "Input should be a valid datetime",
    "datetime_type": "Input should be a valid datetime",
    "decimal_max_places": "Number has too many decimal places",
    "decimal_parsing": "Input should be a valid decimal",
    "decimal_type": "Input should be a valid decimal",
    "enum": "Input is not an accepted value",
    "extra_forbidden": "Extra inputs are not permitted",
    "finite_number": "Input should be a finite number",
    "greater_than": "Value is below the permitted minimum",
    "greater_than_equal": "Value is below the permitted minimum",
    "int_parsing": "Input should be a valid integer",
    "int_type": "Input should be a valid integer",
    "json_invalid": "Request body should contain valid JSON",
    "less_than": "Value exceeds the permitted maximum",
    "less_than_equal": "Value exceeds the permitted maximum",
    "list_type": "Input should be a valid list",
    "literal_error": "Input is not an accepted value",
    "missing": "Field required",
    "string_pattern_mismatch": "String does not match the required format",
    "string_too_long": "String is too long",
    "string_too_short": "String is too short",
    "string_type": "Input should be a valid string",
    "too_long": "Collection is too long",
    "too_short": "Collection is too short",
    "url_parsing": "Input should be a valid URL",
    "uuid_parsing": "Input should be a valid UUID",
    "uuid_type": "Input should be a valid UUID",
    "value_error": "Value does not satisfy validation requirements",
}


def _safe_error_type(value: Any) -> str:
    if isinstance(value, str) and _ERROR_TYPE_PATTERN.fullmatch(value) is not None:
        return value
    return "value_error"


def _safe_location(value: Any, *, error_type: str) -> list[str | int]:
    if not isinstance(value, (list, tuple)):
        return ["request"]

    location: list[str | int] = []
    for part in value:
        if isinstance(part, bool):
            location.append("value")
        elif isinstance(part, int) or (
            isinstance(part, str) and _LOCATION_LABEL_PATTERN.fullmatch(part) is not None
        ):
            location.append(part)
        else:
            location.append("value")

    if error_type == "extra_forbidden" and location:
        location[-1] = "[extra]"
    return location or ["request"]


def serialize_request_validation_errors(
    exc: RequestValidationError,
) -> list[dict[str, object]]:
    """Allowlist actionable error metadata without serializing submitted input."""

    safe_errors: list[dict[str, object]] = []
    for error in exc.errors():
        error_type = _safe_error_type(error.get("type"))
        safe_errors.append(
            {
                "type": error_type,
                "loc": _safe_location(error.get("loc"), error_type=error_type),
                "msg": _SAFE_MESSAGES.get(error_type, "Invalid request value"),
            }
        )
    return safe_errors


async def request_validation_error_handler(
    _: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return deterministic 422 details without logging or echoing request values."""

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": serialize_request_validation_errors(exc)},
    )
