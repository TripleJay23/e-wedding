"""M3 repository query-shape and transaction delegation tests."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models.event import Event, EventGuestField, EventStatus, GuestFieldKey
from app.models.guest import Guest
from app.repositories.events import EventRepository
from app.repositories.guests import GuestRepository


class FakeScalarResult:
    def __init__(self, values: list[Any], scalar: Any = None) -> None:
        self._values = values
        self._scalar = scalar

    def scalars(self) -> list[Any]:
        return self._values

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.statements: list[Any] = []
        self.get_result: Any = None
        self.execute_result = FakeScalarResult([])
        self.commits = 0
        self.rollbacks = 0

    def add(self, value: Any) -> None:
        self.added.append(value)

    def add_all(self, values: list[Any]) -> None:
        self.added.extend(values)

    def get(self, _: type[Any], __: Any) -> Any:
        return self.get_result

    def execute(self, statement: Any) -> FakeScalarResult:
        self.statements.append(statement)
        return self.execute_result

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def make_event() -> Event:
    return Event(
        id=uuid4(),
        event_name="Synthetic Wedding",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=datetime(2027, 6, 12, tzinfo=UTC),
        venue="Synthetic Hall",
        max_companions_per_invitation=2,
        created_by=uuid4(),
    )


def test_event_repository_uses_bounded_deterministic_index_shapes() -> None:
    session = FakeSession()
    repository = EventRepository(session)  # type: ignore[arg-type]
    event = make_event()
    field = EventGuestField(
        id=uuid4(),
        event_id=event.id,
        field_key=GuestFieldKey.FULL_NAME,
        enabled=True,
        required=True,
        sort_order=0,
    )

    repository.add(event)
    repository.add_guest_fields([field])
    session.get_result = event
    assert repository.get(event.id) is event
    assert session.added == [event, field]

    session.execute_result = FakeScalarResult([], scalar=event)
    assert repository.get_for_update(event.id) is event
    assert "FOR UPDATE" in str(session.statements[-1])

    session.execute_result = FakeScalarResult([event])
    assert repository.list(event_status=None, limit=50, offset=5) == [event]
    unfiltered_sql = str(session.statements[-1])
    assert "ORDER BY events.event_datetime, events.id" in unfiltered_sql
    assert "LIMIT" in unfiltered_sql
    assert "OFFSET" in unfiltered_sql

    assert repository.list(event_status=EventStatus.DRAFT, limit=10, offset=0) == [event]
    filtered_sql = str(session.statements[-1])
    assert "events.status =" in filtered_sql
    assert "ORDER BY events.event_datetime, events.id" in filtered_sql

    session.execute_result = FakeScalarResult([field])
    assert repository.get_guest_fields(event.id) == [field]
    assert "ORDER BY event_guest_fields.sort_order" in str(session.statements[-1])

    session.execute_result = FakeScalarResult([], scalar=2)
    assert repository.maximum_guest_companions(event.id) == 2
    assert "max(guests.allowed_companions)" in str(session.statements[-1])

    repository.commit()
    repository.rollback()
    assert (session.commits, session.rollbacks) == (1, 1)


def test_guest_repository_keeps_identity_event_scoped_and_lists_bounded() -> None:
    session = FakeSession()
    repository = GuestRepository(session)  # type: ignore[arg-type]
    event = make_event()
    guest = Guest(
        id=uuid4(),
        event_id=event.id,
        full_name="Synthetic Guest",
        invitation_type="Family",
        allowed_companions=0,
    )

    repository.add(guest)
    session.execute_result = FakeScalarResult([], scalar=guest)
    assert repository.get_for_event(event.id, guest.id) is guest
    detail_sql = str(session.statements[-1])
    assert "guests.id =" in detail_sql
    assert "guests.event_id =" in detail_sql

    session.execute_result = FakeScalarResult([guest])
    assert repository.list_for_event(event.id, limit=25, offset=3) == [guest]
    list_sql = str(session.statements[-1])
    assert "ORDER BY guests.full_name, guests.id" in list_sql
    assert "LIMIT" in list_sql
    assert "OFFSET" in list_sql

    repository.commit()
    repository.rollback()
    assert session.added == [guest]
    assert (session.commits, session.rollbacks) == (1, 1)
