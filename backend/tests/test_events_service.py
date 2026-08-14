"""M3 event lifecycle, guest policy, and manual guest service tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.event import (
    DEFAULT_GUEST_FIELD_CONFIGURATION,
    Event,
    EventGuestField,
    EventStatus,
    GuestFieldKey,
)
from app.models.guest import Guest, GuestStatus
from app.services.events import (
    EventLifecycle,
    EventService,
    M3ConflictError,
    M3ResourceNotFoundError,
)

EVENT_TIME = datetime(2027, 6, 12, 14, 30, tzinfo=UTC)


def make_event(*, event_status: EventStatus = EventStatus.DRAFT, maximum: int = 2) -> Event:
    now = datetime.now(UTC)
    return Event(
        id=uuid4(),
        event_name="Synthetic Wedding",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=EVENT_TIME,
        venue="Synthetic Hall",
        address=None,
        description=None,
        status=event_status,
        max_companions_per_invitation=maximum,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )


def make_fields(event_id: UUID) -> list[EventGuestField]:
    return [
        EventGuestField(
            id=uuid4(),
            event_id=event_id,
            field_key=field_key,
            enabled=enabled,
            required=required,
            sort_order=sort_order,
        )
        for sort_order, (field_key, enabled, required) in enumerate(
            DEFAULT_GUEST_FIELD_CONFIGURATION
        )
    ]


def guest_values(*, allowed_companions: int = 0, phone_number: str | None = None) -> dict[str, Any]:
    return {
        "full_name": "Synthetic Guest",
        "phone_number": phone_number,
        "email": None,
        "invitation_type": "Family",
        "allowed_companions": allowed_companions,
        "table_number": None,
        "category": None,
    }


class FakeEventRepository:
    """In-memory event repository with observable transaction behavior."""

    def __init__(self, event: Event | None = None) -> None:
        self.events = {} if event is None else {event.id: event}
        self.fields = {} if event is None else {event.id: make_fields(event.id)}
        self.added_event: Event | None = None
        self.added_fields: list[EventGuestField] = []
        self.list_result: list[Event] = []
        self.list_arguments: tuple[EventStatus | None, int, int] | None = None
        self.maximum_guest_allowance: int | None = None
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False

    def add(self, event: Event) -> None:
        self.added_event = event
        self.events[event.id] = event

    def add_guest_fields(self, fields: list[EventGuestField]) -> None:
        self.added_fields = list(fields)
        if fields:
            self.fields[fields[0].event_id] = list(fields)

    def get(self, event_id: UUID) -> Event | None:
        return self.events.get(event_id)

    def list(
        self,
        *,
        event_status: EventStatus | None,
        limit: int,
        offset: int,
    ) -> list[Event]:
        self.list_arguments = (event_status, limit, offset)
        return self.list_result

    def get_guest_fields(self, event_id: UUID) -> list[EventGuestField]:
        return self.fields.get(event_id, [])

    def maximum_guest_companions(self, _: UUID) -> int | None:
        return self.maximum_guest_allowance

    def commit(self) -> None:
        if self.fail_commit:
            raise IntegrityError("synthetic", {}, Exception("constraint"))
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeGuestRepository:
    """In-memory manual guest repository."""

    def __init__(self) -> None:
        self.guests: dict[tuple[UUID, UUID], Guest] = {}
        self.added_guest: Guest | None = None
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False

    def add(self, guest: Guest) -> None:
        self.added_guest = guest
        self.guests[(guest.event_id, guest.id)] = guest

    def get_for_event(self, event_id: UUID, guest_id: UUID) -> Guest | None:
        return self.guests.get((event_id, guest_id))

    def list_for_event(self, event_id: UUID, *, limit: int, offset: int) -> list[Guest]:
        return list(self.guests.values())[offset : offset + limit]

    def commit(self) -> None:
        if self.fail_commit:
            raise IntegrityError("synthetic", {}, Exception("constraint"))
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def make_service(
    event: Event | None = None,
) -> tuple[EventService, FakeEventRepository, FakeGuestRepository]:
    events = FakeEventRepository(event)
    guests = FakeGuestRepository()
    return EventService(events, guests), events, guests  # type: ignore[arg-type]


def test_event_creation_persists_creator_policy_and_seven_default_fields() -> None:
    service, events, _ = make_service()
    administrator_id = uuid4()

    event = service.create_event(
        administrator_id=administrator_id,
        values={
            "event_name": "Synthetic Wedding",
            "bride_name": "Amina",
            "groom_name": "Baraka",
            "event_datetime": EVENT_TIME,
            "venue": "Synthetic Hall",
            "address": None,
            "description": None,
            "max_companions_per_invitation": 2,
        },
    )

    assert event.status is EventStatus.DRAFT
    assert event.created_by == administrator_id
    assert event.max_companions_per_invitation == 2
    assert events.added_event is event
    assert [(field.field_key, field.enabled, field.required) for field in events.added_fields] == [
        (field_key, enabled, required)
        for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
    ]
    assert [field.sort_order for field in events.added_fields] == list(range(7))
    assert events.commits == 1


def test_event_list_and_detail_are_bounded_repository_delegations() -> None:
    event = make_event()
    service, events, _ = make_service(event)
    events.list_result = [event]

    assert service.list_events(event_status=EventStatus.DRAFT, limit=25, offset=5) == [event]
    assert events.list_arguments == (EventStatus.DRAFT, 25, 5)
    assert service.get_event(event.id) is event
    with pytest.raises(M3ResourceNotFoundError, match="event not found"):
        service.get_event(uuid4())


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (EventStatus.DRAFT, EventStatus.DRAFT),
        (EventStatus.DRAFT, EventStatus.ACTIVE),
        (EventStatus.DRAFT, EventStatus.CANCELLED),
        (EventStatus.ACTIVE, EventStatus.ACTIVE),
        (EventStatus.ACTIVE, EventStatus.COMPLETED),
        (EventStatus.ACTIVE, EventStatus.CANCELLED),
        (EventStatus.COMPLETED, EventStatus.COMPLETED),
        (EventStatus.CANCELLED, EventStatus.CANCELLED),
    ],
)
def test_allowed_lifecycle_transitions(current: EventStatus, target: EventStatus) -> None:
    EventLifecycle.require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (EventStatus.DRAFT, EventStatus.COMPLETED),
        (EventStatus.ACTIVE, EventStatus.DRAFT),
        (EventStatus.COMPLETED, EventStatus.ACTIVE),
        (EventStatus.COMPLETED, EventStatus.CANCELLED),
        (EventStatus.CANCELLED, EventStatus.DRAFT),
        (EventStatus.CANCELLED, EventStatus.ACTIVE),
    ],
)
def test_invalid_lifecycle_transitions_are_rejected(
    current: EventStatus,
    target: EventStatus,
) -> None:
    with pytest.raises(M3ConflictError, match="cannot transition"):
        EventLifecycle.require_transition(current, target)


def test_event_updates_transition_and_enforce_terminal_immutability() -> None:
    event = make_event()
    service, events, _ = make_service(event)

    assert (
        service.update_event(event.id, {"status": EventStatus.ACTIVE}).status is EventStatus.ACTIVE
    )
    assert events.commits == 1
    service.update_event(event.id, {"status": EventStatus.ACTIVE})
    assert events.commits == 1
    service.update_event(event.id, {"status": EventStatus.COMPLETED})

    with pytest.raises(M3ConflictError, match="immutable"):
        service.update_event(event.id, {"venue": "Changed Hall"})


def test_event_policy_cannot_drop_below_existing_guest_allowance() -> None:
    event = make_event()
    service, events, _ = make_service(event)
    events.maximum_guest_allowance = 2

    with pytest.raises(M3ConflictError, match="existing guest allowance"):
        service.update_event(event.id, {"max_companions_per_invitation": 1})
    assert event.max_companions_per_invitation == 2
    assert events.commits == 0


def test_guest_field_configuration_is_complete_replaceable_and_terminal_safe() -> None:
    event = make_event()
    service, events, _ = make_service(event)
    replacement = [
        (field_key, enabled, required)
        for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
    ]
    replacement[1] = (GuestFieldKey.PHONE_NUMBER, False, False)

    fields = service.replace_guest_fields(event.id, replacement)

    assert fields[1].enabled is False
    assert len(service.get_guest_fields(event.id)) == 7
    assert events.commits == 1
    event.status = EventStatus.CANCELLED
    with pytest.raises(M3ConflictError, match="immutable"):
        service.replace_guest_fields(event.id, replacement)


@pytest.mark.parametrize(
    "configuration",
    [
        [(GuestFieldKey.FULL_NAME, True, True)],
        [
            (field_key, enabled, required)
            if field_key is not GuestFieldKey.PHONE_NUMBER
            else (field_key, False, True)
            for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
        ],
        [
            (field_key, enabled, required)
            if field_key is not GuestFieldKey.FULL_NAME
            else (field_key, False, False)
            for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
        ],
    ],
)
def test_invalid_guest_field_configuration_is_rejected(
    configuration: list[tuple[GuestFieldKey, bool, bool]],
) -> None:
    event = make_event()
    service, _, _ = make_service(event)

    with pytest.raises(M3ConflictError):
        service.replace_guest_fields(event.id, configuration)


@pytest.mark.parametrize("allowed_companions", [0, 1, 2])
def test_manual_guest_creation_accepts_all_policy_boundary_values(
    allowed_companions: int,
) -> None:
    event = make_event()
    service, _, guests = make_service(event)

    guest = service.create_guest(
        event.id,
        guest_values(allowed_companions=allowed_companions),
    )

    assert guest.event_id == event.id
    assert guest.allowed_companions == allowed_companions
    assert guest.status is None or guest.status is GuestStatus.ACTIVE
    assert guests.commits == 1


def test_manual_guest_creation_enforces_event_maximum_and_field_rules() -> None:
    event = make_event(maximum=1)
    service, events, _ = make_service(event)

    with pytest.raises(M3ConflictError, match="event companion policy"):
        service.create_guest(event.id, guest_values(allowed_companions=2))

    events.fields[event.id][1].enabled = False
    with pytest.raises(M3ConflictError, match="phone_number is disabled"):
        service.create_guest(event.id, guest_values(phone_number="+255700000000"))

    events.fields[event.id][1].enabled = True
    events.fields[event.id][1].required = True
    with pytest.raises(M3ConflictError, match="phone_number is required"):
        service.create_guest(event.id, guest_values())


def test_manual_guest_list_detail_and_update_remain_event_scoped() -> None:
    event = make_event()
    service, _, _ = make_service(event)
    guest = service.create_guest(event.id, guest_values())

    assert service.list_guests(event.id, limit=50, offset=0) == [guest]
    assert service.get_guest(event.id, guest.id) is guest
    updated = service.update_guest(event.id, guest.id, {"allowed_companions": 2})
    assert updated.allowed_companions == 2
    with pytest.raises(M3ResourceNotFoundError, match="guest not found"):
        service.get_guest(uuid4(), guest.id)


def test_manual_guest_mutation_rejects_terminal_or_incomplete_event_configuration() -> None:
    event = make_event(event_status=EventStatus.COMPLETED)
    service, events, _ = make_service(event)
    with pytest.raises(M3ConflictError, match="immutable"):
        service.create_guest(event.id, guest_values())

    event.status = EventStatus.DRAFT
    events.fields[event.id] = events.fields[event.id][:-1]
    with pytest.raises(M3ConflictError, match="incomplete"):
        service.create_guest(event.id, guest_values())
    with pytest.raises(M3ConflictError, match="incomplete"):
        service.get_guest_fields(event.id)


def test_database_integrity_failure_is_rolled_back_and_bounded() -> None:
    event = make_event()
    service, _, guests = make_service(event)
    guests.fail_commit = True

    with pytest.raises(M3ConflictError, match="persistence constraint"):
        service.create_guest(event.id, guest_values())

    assert guests.rollbacks == 1
