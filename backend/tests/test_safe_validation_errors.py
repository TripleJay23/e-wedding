"""Central request-validation privacy and response-shape regressions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any
from uuid import uuid4

import httpx
import pytest
from fastapi import Body
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from app.api.dependencies import (
    get_attendance_service,
    get_authentication_service,
    get_current_administrator,
)
from app.api.errors import serialize_request_validation_errors
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole


class FakeDatabaseRuntime:
    def close(self) -> None:
        pass


class UnexpectedServiceCall:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"validation should fail before service call: {name}")


class NestedItem(BaseModel):
    code: str = Field(max_length=4)


class NestedPayload(BaseModel):
    items: list[NestedItem]


def _administrator() -> Administrator:
    return Administrator(
        id=uuid4(),
        username="synthetic-validation-admin",
        password_hash="$argon2id$synthetic",
        role=AdministratorRole.SUPER_ADMIN,
        is_active=True,
    )


def _build_app(settings):
    app = create_app(settings, database_runtime=FakeDatabaseRuntime())  # type: ignore[arg-type]
    app.dependency_overrides[get_current_administrator] = _administrator
    app.dependency_overrides[get_attendance_service] = UnexpectedServiceCall
    app.dependency_overrides[get_authentication_service] = UnexpectedServiceCall

    @app.post("/synthetic/nested-validation")
    def nested_validation(_: Annotated[NestedPayload, Body()]) -> dict[str, bool]:
        return {"accepted": True}

    return app


async def _request(app, method: str, path: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        return await client.request(method, path, **kwargs)


def _assert_no_input_key(value: Any) -> None:
    if isinstance(value, dict):
        assert "input" not in value
        for nested in value.values():
            _assert_no_input_key(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_input_key(nested)


def _assert_safe_422(
    response: httpx.Response,
    caplog: pytest.LogCaptureFixture,
    *,
    forbidden_values: tuple[str, ...],
    expected_location: list[str | int],
) -> None:
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail"}
    assert body["detail"]
    assert all(set(error) == {"type", "loc", "msg"} for error in body["detail"])
    assert any(error["loc"] == expected_location for error in body["detail"])
    _assert_no_input_key(body)
    for value in forbidden_values:
        assert value not in response.content.decode("utf-8")
        assert value not in caplog.text


@pytest.mark.parametrize(
    ("credential", "expected_type"),
    [
        ("Z" * 33, "string_too_long"),
        ("0" * 32, "string_pattern_mismatch"),
        ("SHORT-SENSITIVE", "string_too_short"),
    ],
)
def test_attendance_credential_validation_never_echoes_rejected_value(
    development_settings,
    caplog: pytest.LogCaptureFixture,
    credential: str,
    expected_type: str,
) -> None:
    app = _build_app(development_settings)
    caplog.set_level(logging.DEBUG)

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/attendance/scan",
            json={"event_id": str(uuid4()), "credential": credential},
        )
    )

    _assert_safe_422(
        response,
        caplog,
        forbidden_values=(credential,),
        expected_location=["body", "credential"],
    )
    assert any(error["type"] == expected_type for error in response.json()["detail"])


def test_empty_attendance_credential_has_safe_actionable_error(
    development_settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _build_app(development_settings)
    caplog.set_level(logging.DEBUG)

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/attendance/scan",
            json={"event_id": str(uuid4()), "credential": ""},
        )
    )

    _assert_safe_422(
        response,
        caplog,
        forbidden_values=(),
        expected_location=["body", "credential"],
    )
    assert response.json()["detail"][0]["msg"] == "String is too short"


def test_extra_field_and_valid_credential_are_absent_from_validation_error(
    development_settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _build_app(development_settings)
    caplog.set_level(logging.DEBUG)
    credential = "C" * 32
    rejected_extra = "sensitive-extra-request-value"

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/attendance/scan",
            json={
                "event_id": str(uuid4()),
                "credential": credential,
                "unexpected": rejected_extra,
            },
        )
    )

    _assert_safe_422(
        response,
        caplog,
        forbidden_values=(credential, rejected_extra),
        expected_location=["body", "[extra]"],
    )


def test_invalid_companion_count_preserves_location_without_echoing_credential(
    development_settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _build_app(development_settings)
    caplog.set_level(logging.DEBUG)
    credential = "D" * 32

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/attendance/scan",
            json={
                "event_id": str(uuid4()),
                "credential": credential,
                "companions_admitted": 3,
            },
        )
    )

    _assert_safe_422(
        response,
        caplog,
        forbidden_values=(credential,),
        expected_location=["body", "companions_admitted"],
    )
    assert response.json()["detail"][0]["msg"] == "Value exceeds the permitted maximum"


def test_login_password_validation_never_echoes_password(
    development_settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _build_app(development_settings)
    caplog.set_level(logging.DEBUG)
    password = "p" * 1025

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/auth/login",
            json={"username": "synthetic-admin", "password": password},
        )
    )

    _assert_safe_422(
        response,
        caplog,
        forbidden_values=(password,),
        expected_location=["body", "password"],
    )


def test_nested_list_validation_preserves_safe_indexed_location(
    development_settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _build_app(development_settings)
    caplog.set_level(logging.DEBUG)
    rejected_value = "nested-sensitive-request-value"

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/synthetic/nested-validation",
            json={"items": [{"code": rejected_value}]},
        )
    )

    _assert_safe_422(
        response,
        caplog,
        forbidden_values=(rejected_value,),
        expected_location=["body", "items", 0, "code"],
    )


def test_generic_serializer_omits_rejected_input_context_and_exception() -> None:
    rejected_value = "generic-sensitive-request-value"
    exc = RequestValidationError(
        [
            {
                "type": "string_too_long",
                "loc": ("body", "credential"),
                "msg": "String should have at most 32 characters",
                "input": rejected_value,
                "ctx": {
                    "max_length": 32,
                    "error": ValueError(rejected_value),
                },
                "url": "https://errors.example.invalid/string_too_long",
            }
        ]
    )

    serialized = serialize_request_validation_errors(exc)

    assert serialized == [
        {
            "type": "string_too_long",
            "loc": ["body", "credential"],
            "msg": "String is too long",
        }
    ]
    encoded = json.dumps(serialized)
    assert rejected_value not in encoded
    assert "input" not in encoded
    assert "ctx" not in encoded
    assert "url" not in encoded


def test_generic_serializer_sanitizes_untrusted_error_metadata() -> None:
    exc = RequestValidationError(
        [
            {
                "type": "credential=metadata-secret",
                "loc": "submitted-location-secret",
                "input": "submitted-input-secret",
            },
            {
                "type": "custom_error",
                "loc": ("body", True, "submitted location secret"),
                "input": "second-input-secret",
            },
            {
                "type": "custom_error",
                "loc": (),
                "input": "third-input-secret",
            },
        ]
    )

    assert serialize_request_validation_errors(exc) == [
        {
            "type": "value_error",
            "loc": ["request"],
            "msg": "Value does not satisfy validation requirements",
        },
        {
            "type": "custom_error",
            "loc": ["body", "value", "value"],
            "msg": "Invalid request value",
        },
        {
            "type": "custom_error",
            "loc": ["request"],
            "msg": "Invalid request value",
        },
    ]
