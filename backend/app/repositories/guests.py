"""Manual guest persistence operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attendance import GuestCompanionEntitlement
from app.models.guest import Guest


class GuestRepository:
    """Perform event-scoped, bounded, relationship-free guest queries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, guest: Guest) -> None:
        self._session.add(guest)

    def get_for_event(self, event_id: UUID, guest_id: UUID) -> Guest | None:
        statement = select(Guest).where(Guest.id == guest_id, Guest.event_id == event_id)
        return self._session.execute(statement).scalar_one_or_none()

    def get_for_event_for_update(self, event_id: UUID, guest_id: UUID) -> Guest | None:
        statement = (
            select(Guest)
            .where(Guest.id == guest_id, Guest.event_id == event_id)
            .with_for_update(of=Guest)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def list_for_event(self, event_id: UUID, *, limit: int, offset: int) -> list[Guest]:
        statement = (
            select(Guest)
            .where(Guest.event_id == event_id)
            .order_by(Guest.full_name, Guest.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def has_companion_entitlement(self, event_id: UUID, guest_id: UUID) -> bool:
        statement = select(GuestCompanionEntitlement.id).where(
            GuestCompanionEntitlement.event_id == event_id,
            GuestCompanionEntitlement.guest_id == guest_id,
        )
        return self._session.execute(statement).scalar_one_or_none() is not None

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
