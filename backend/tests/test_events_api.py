"""Authenticated M3 event, guest-field, and manual-guest API tests."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.dependencies import (
    get_authentication_service,
    get_current_administrator,
    get_event_service,
)
from app.core.settings import Settings
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.models.event import (
    DEFAULT_GUEST_FIELD_CONFIGURATION,
    Event,
    EventGuestField,
    EventStatus,
    GuestFieldKey,
)
from app.models.guest import Guest, GuestStatus
from app.services.events import M3ConflictError, M3ResourceNotFoundError

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
EVENT_TIME = datetime(2027, 6, 12, 14, 30, tzinfo=UTC)


class FakeDatabaseRuntime:
    """Connection-free runtime for M3 API tests."""

    closed = False

    def close(self) -> None:
        self.closed = True


class RejectIfCalledAuthenticationService:
    def current_administrator(self, _: str) -> Administrator:
        raise AssertionError("missing authentication must not reach token decoding")


class FakeEventService:
    """Observable M3 service used to prove route contracts remain thin."""

    def __init__(self, event: Event, guest: Guest, fields: list[EventGuestField]) -> None:
        self.event = event
        self.guest = guest
        self.fields = fields
        self.calls: list[tuple[str, Any]] = []
        self.next_error: Exception | None = None

    def _raise_if_requested(self) -> None:
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error

    def create_event(self, *, administrator_id: UUID, values: dict[str, Any]) -> Event:
        self._raise_if_requested()
        self.calls.append(("create_event", (administrator_id, values)))
        self.event.max_companions_per_invitation = values["max_companions_per_invitation"]
        return self.event

    def list_events(
        self,
        *,
        event_status: EventStatus | None,
        limit: int,
        offset: int,
    ) -> list[Event]:
        self.calls.append(("list_events", (event_status, limit, offset)))
        return [self.event]

    def get_event(self, _: UUID) -> Event:
        self._raise_if_requested()
        return self.event

    def update_event(self, _: UUID, values: dict[str, Any]) -> Event:
        self._raise_if_requested()
        self.calls.append(("update_event", values))
        for key, value in values.items():
            setattr(self.event, key, value)
        return self.event

    def get_guest_fields(self, _: UUID) -> list[EventGuestField]:
        self._raise_if_requested()
        return self.fields

    def replace_guest_fields(
        self,
        _: UUID,
        configuration: list[tuple[GuestFieldKey, bool, bool]],
    ) -> list[EventGuestField]:
        self._raise_if_requested()
        self.calls.append(("replace_guest_fields", configuration))
        return self.fields

    def create_guest(self, _: UUID, values: dict[str, Any]) -> Guest:
        self._raise_if_requested()
        self.calls.append(("create_guest", values))
        self.guest.allowed_companions = values["allowed_companions"]
        return self.guest

    def list_guests(self, _: UUID, *, limit: int, offset: int) -> list[Guest]:
        self._raise_if_requested()
        self.calls.append(("list_guests", (limit, offset)))
        return [self.guest]

    def get_guest(self, _: UUID, __: UUID) -> Guest:
        self._raise_if_requested()
        return self.guest

    def update_guest(self, _: UUID, __: UUID, values: dict[str, Any]) -> Guest:
        self._raise_if_requested()
        self.calls.append(("update_guest", values))
        for key, value in values.items():
            setattr(self.guest, key, value)
        return self.guest


def make_domain() -> tuple[Administrator, Event, Guest, list[EventGuestField]]:
    administrator = Administrator(
        id=uuid4(),
        username="admin",
        password_hash="$argon2id$synthetic",
        role=AdministratorRole.SUPER_ADMIN,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    event = Event(
        id=uuid4(),
        event_name="Synthetic Wedding",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=EVENT_TIME,
        venue="Synthetic Hall",
        address="Synthetic Address",
        description=None,
        status=EventStatus.DRAFT,
        max_companions_per_invitation=2,
        created_by=administrator.id,
        created_at=NOW,
        updated_at=NOW,
    )
    guest = Guest(
        id=uuid4(),
        event_id=event.id,
        full_name="Synthetic Guest",
        phone_number=None,
        email=None,
        invitation_type="Family",
        allowed_companions=0,
        table_number=None,
        category=None,
        status=GuestStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    fields = [
        EventGuestField(
            id=uuid4(),
            event_id=event.id,
            field_key=field_key,
            enabled=enabled,
            required=required,
            sort_order=sort_order,
        )
        for sort_order, (field_key, enabled, required) in enumerate(
            DEFAULT_GUEST_FIELD_CONFIGURATION
        )
    ]
    return administrator, event, guest, fields


def build_app(settings: Settings, *, authenticated: bool = True) -> tuple[object, FakeEventService]:
    administrator, event, guest, fields = make_domain()
    service = FakeEventService(event, guest, fields)
    app = create_app(settings, database_runtime=FakeDatabaseRuntime())  # type: ignore[arg-type]
    app.dependency_overrides[get_event_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_administrator] = lambda: administrator
    else:
        app.dependency_overrides[get_authentication_service] = lambda: (
            RejectIfCalledAuthenticationService()
        )
    return app, service


async def make_requests(
    app: object,
    requests: list[tuple[str, str, dict[str, Any]]],
) -> list[httpx.Response]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    responses = []
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        for method, path, kwargs in requests:
            responses.append(await client.request(method, path, **kwargs))
    return responses


def event_payload(maximum: int = 2) -> dict[str, Any]:
    return {
        "event_name": "  Synthetic Wedding  ",
        "bride_name": " Amina ",
        "groom_name": " Baraka ",
        "event_datetime": EVENT_TIME.isoformat(),
        "venue": " Synthetic Hall ",
        "max_companions_per_invitation": maximum,
    }


def guest_payload(allowed_companions: int = 0) -> dict[str, Any]:
    return {
        "full_name": " Synthetic Guest ",
        "invitation_type": " Family ",
        "allowed_companions": allowed_companions,
    }


def test_authenticated_event_crud_listing_and_lifecycle_contract(
    development_settings: Settings,
) -> None:
    app, service = build_app(development_settings)
    event_id = service.event.id

    responses = asyncio.run(
        make_requests(
            app,
            [
                ("POST", "/api/v1/events", {"json": event_payload()}),
                ("GET", "/api/v1/events?status=DRAFT&limit=10&offset=2", {}),
                ("GET", f"/api/v1/events/{event_id}", {}),
                ("PATCH", f"/api/v1/events/{event_id}", {"json": {"status": "ACTIVE"}}),
            ],
        )
    )

    assert [response.status_code for response in responses] == [201, 200, 200, 200]
    created = responses[0].json()
    assert created["created_by"] == str(service.event.created_by)
    assert created["max_companions_per_invitation"] == 2
    assert responses[1].json()["limit"] == 10
    assert responses[1].json()["offset"] == 2
    assert ("list_events", (EventStatus.DRAFT, 10, 2)) in service.calls
    assert responses[3].json()["status"] == "ACTIVE"


@pytest.mark.parametrize("maximum", [0, 1, 2])
def test_event_companion_policy_accepts_only_locked_boundaries(
    development_settings: Settings,
    maximum: int,
) -> None:
    app, _ = build_app(development_settings)
    [response] = asyncio.run(
        make_requests(app, [("POST", "/api/v1/events", {"json": event_payload(maximum)})])
    )
    assert response.status_code == 201
    assert response.json()["max_companions_per_invitation"] == maximum


@pytest.mark.parametrize("maximum", [-1, 3])
def test_event_companion_policy_rejects_values_outside_zero_to_two(
    development_settings: Settings,
    maximum: int,
) -> None:
    app, service = build_app(development_settings)
    [response] = asyncio.run(
        make_requests(app, [("POST", "/api/v1/events", {"json": event_payload(maximum)})])
    )
    assert response.status_code == 422
    assert service.calls == []


def test_guest_field_get_and_complete_replace_contract(development_settings: Settings) -> None:
    app, service = build_app(development_settings)
    event_id = service.event.id
    replacement = [
        {"field_key": field_key.value, "enabled": enabled, "required": required}
        for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
    ]
    replacement[1]["enabled"] = False

    get_response, replace_response = asyncio.run(
        make_requests(
            app,
            [
                ("GET", f"/api/v1/events/{event_id}/guest-fields", {}),
                (
                    "PUT",
                    f"/api/v1/events/{event_id}/guest-fields",
                    {"json": {"fields": replacement}},
                ),
            ],
        )
    )

    assert get_response.status_code == 200
    assert [item["field_key"] for item in get_response.json()["fields"]] == [
        item[0].value for item in DEFAULT_GUEST_FIELD_CONFIGURATION
    ]
    assert replace_response.status_code == 200
    assert any(call[0] == "replace_guest_fields" for call in service.calls)


@pytest.mark.parametrize("allowed_companions", [0, 1, 2])
def test_manual_guest_crud_accepts_companion_boundaries(
    development_settings: Settings,
    allowed_companions: int,
) -> None:
    app, service = build_app(development_settings)
    event_id = service.event.id
    guest_id = service.guest.id

    responses = asyncio.run(
        make_requests(
            app,
            [
                (
                    "POST",
                    f"/api/v1/events/{event_id}/guests",
                    {"json": guest_payload(allowed_companions)},
                ),
                ("GET", f"/api/v1/events/{event_id}/guests?limit=20&offset=0", {}),
                ("GET", f"/api/v1/events/{event_id}/guests/{guest_id}", {}),
                (
                    "PATCH",
                    f"/api/v1/events/{event_id}/guests/{guest_id}",
                    {"json": {"status": "INACTIVE"}},
                ),
            ],
        )
    )

    assert [response.status_code for response in responses] == [201, 200, 200, 200]
    assert responses[0].json()["allowed_companions"] == allowed_companions
    assert responses[1].json()["items"][0]["event_id"] == str(event_id)
    assert responses[3].json()["status"] == "INACTIVE"


@pytest.mark.parametrize("allowed_companions", [-1, 3])
def test_manual_guest_api_rejects_out_of_range_companions(
    development_settings: Settings,
    allowed_companions: int,
) -> None:
    app, service = build_app(development_settings)
    [response] = asyncio.run(
        make_requests(
            app,
            [
                (
                    "POST",
                    f"/api/v1/events/{service.event.id}/guests",
                    {"json": guest_payload(allowed_companions)},
                )
            ],
        )
    )
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event_name": "only a name"},
        {**event_payload(), "unexpected": "rejected"},
        {**event_payload(), "event_datetime": "not-a-date"},
    ],
)
def test_invalid_event_data_is_rejected_before_service(
    development_settings: Settings,
    payload: dict[str, Any],
) -> None:
    app, service = build_app(development_settings)
    [response] = asyncio.run(make_requests(app, [("POST", "/api/v1/events", {"json": payload})]))
    assert response.status_code == 422
    assert service.calls == []


def test_missing_authentication_rejects_m3_routes_before_domain_work(
    development_settings: Settings,
) -> None:
    app, service = build_app(development_settings, authenticated=False)
    [response] = asyncio.run(make_requests(app, [("GET", "/api/v1/events", {})]))

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (M3ResourceNotFoundError("event not found"), 404),
        (M3ConflictError("event cannot transition"), 409),
    ],
)
def test_domain_errors_are_bounded_http_responses(
    development_settings: Settings,
    error: Exception,
    expected_status: int,
) -> None:
    app, service = build_app(development_settings)
    service.next_error = error
    [response] = asyncio.run(
        make_requests(app, [("GET", f"/api/v1/events/{service.event.id}", {})])
    )

    assert response.status_code == expected_status
    assert "SQL" not in response.text
    assert "traceback" not in response.text.casefold()


def test_guest_field_schema_rejects_duplicates_and_core_field_disable(
    development_settings: Settings,
) -> None:
    app, service = build_app(development_settings)
    fields = [
        {"field_key": field_key.value, "enabled": enabled, "required": required}
        for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
    ]
    fields[-1] = fields[0]
    duplicate_response, disabled_core_response = asyncio.run(
        make_requests(
            app,
            [
                (
                    "PUT",
                    f"/api/v1/events/{service.event.id}/guest-fields",
                    {"json": {"fields": fields}},
                ),
                (
                    "PUT",
                    f"/api/v1/events/{service.event.id}/guest-fields",
                    {
                        "json": {
                            "fields": [
                                {
                                    "field_key": field_key.value,
                                    "enabled": False
                                    if field_key is GuestFieldKey.FULL_NAME
                                    else enabled,
                                    "required": required,
                                }
                                for field_key, enabled, required in (
                                    DEFAULT_GUEST_FIELD_CONFIGURATION
                                )
                            ]
                        }
                    },
                ),
            ],
        )
    )
    assert duplicate_response.status_code == 422
    assert disabled_core_response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path_template", "kwargs"),
    [
        ("POST", "/api/v1/events", {"json": event_payload()}),
        ("PATCH", "/api/v1/events/{event_id}", {"json": {"venue": "Changed Hall"}}),
        ("GET", "/api/v1/events/{event_id}/guest-fields", {}),
        (
            "PUT",
            "/api/v1/events/{event_id}/guest-fields",
            {
                "json": {
                    "fields": [
                        {
                            "field_key": field_key.value,
                            "enabled": enabled,
                            "required": required,
                        }
                        for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
                    ]
                }
            },
        ),
        ("POST", "/api/v1/events/{event_id}/guests", {"json": guest_payload()}),
        ("GET", "/api/v1/events/{event_id}/guests", {}),
        ("GET", "/api/v1/events/{event_id}/guests/{guest_id}", {}),
        (
            "PATCH",
            "/api/v1/events/{event_id}/guests/{guest_id}",
            {"json": {"category": "Updated"}},
        ),
    ],
)
def test_each_mutating_route_translates_domain_conflicts(
    development_settings: Settings,
    method: str,
    path_template: str,
    kwargs: dict[str, Any],
) -> None:
    app, service = build_app(development_settings)
    service.next_error = M3ConflictError("synthetic policy conflict")
    path = path_template.format(event_id=service.event.id, guest_id=service.guest.id)

    [response] = asyncio.run(make_requests(app, [(method, path, kwargs)]))

    assert response.status_code == 409
    assert response.json() == {"detail": "synthetic policy conflict"}


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("PATCH", "/api/v1/events/{event_id}", {}),
        ("PATCH", "/api/v1/events/{event_id}", {"venue": None}),
        ("PATCH", "/api/v1/events/{event_id}", {"event_name": "   "}),
        ("POST", "/api/v1/events/{event_id}/guests", {"full_name": "Guest"}),
        (
            "POST",
            "/api/v1/events/{event_id}/guests",
            {
                "full_name": "Guest",
                "invitation_type": "Family",
                "email": "invalid email",
            },
        ),
        ("PATCH", "/api/v1/events/{event_id}/guests/{guest_id}", {}),
        (
            "PATCH",
            "/api/v1/events/{event_id}/guests/{guest_id}",
            {"allowed_companions": None},
        ),
    ],
)
def test_partial_update_and_guest_schemas_fail_closed(
    development_settings: Settings,
    method: str,
    path: str,
    payload: dict[str, Any],
) -> None:
    app, service = build_app(development_settings)
    resolved_path = path.format(event_id=service.event.id, guest_id=service.guest.id)
    [response] = asyncio.run(make_requests(app, [(method, resolved_path, {"json": payload})]))
    assert response.status_code == 422
    assert service.calls == []
