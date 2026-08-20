"""Event and guest-field persistence operations."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.event import Event, EventGuestField, EventStatus
from app.models.guest import Guest


class EventRepository:
    """Perform bounded indexed M3 event and policy operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: Event) -> None:
        self._session.add(event)

    def add_guest_fields(self, fields: Sequence[EventGuestField]) -> None:
        self._session.add_all(fields)

    def get(self, event_id: UUID) -> Event | None:
        return self._session.get(Event, event_id)

    def get_for_update(self, event_id: UUID) -> Event | None:
        """Serialize event-scoped mutations that depend on current policy."""
        statement = select(Event).where(Event.id == event_id).with_for_update()
        return self._session.execute(statement).scalar_one_or_none()

    def list(
        self,
        *,
        event_status: EventStatus | None,
        limit: int,
        offset: int,
    ) -> list[Event]:
        statement: Select[tuple[Event]] = select(Event)
        if event_status is not None:
            statement = statement.where(Event.status == event_status)
        statement = statement.order_by(Event.event_datetime, Event.id).limit(limit).offset(offset)
        return list(self._session.execute(statement).scalars())

    def get_guest_fields(self, event_id: UUID) -> list[EventGuestField]:
        statement = (
            select(EventGuestField)
            .where(EventGuestField.event_id == event_id)
            .order_by(EventGuestField.sort_order)
        )
        return list(self._session.execute(statement).scalars())

    def maximum_guest_companions(self, event_id: UUID) -> int | None:
        statement = select(func.max(Guest.allowed_companions)).where(Guest.event_id == event_id)
        return self._session.execute(statement).scalar_one()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
