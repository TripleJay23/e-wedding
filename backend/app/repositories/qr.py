"""Indexed persistence operations for M5 QR credentials."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.guest import Guest
from app.models.qr import GuestQrCredential, QrCredentialStatus


class QrCredentialRepository:
    """Keep credential queries bounded, event-scoped, and relationship-free."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_guest_context_for_update(self, guest_id: UUID) -> tuple[Guest, Event] | None:
        """Lock the guest/event mutation scope so concurrent issuance serializes."""
        statement = (
            select(Guest, Event)
            .join(Event, Event.id == Guest.event_id)
            .where(Guest.id == guest_id)
            .with_for_update(of=Guest)
        )
        row = self._session.execute(statement).one_or_none()
        return None if row is None else (row[0], row[1])

    def get_latest_for_guest(self, guest_id: UUID) -> GuestQrCredential | None:
        statement = (
            select(GuestQrCredential)
            .where(GuestQrCredential.guest_id == guest_id)
            .order_by(GuestQrCredential.issued_at.desc(), GuestQrCredential.id.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get_active_by_hash(self, token_hash: str) -> GuestQrCredential | None:
        """Resolve only a currently usable verifier through its unique indexed hash."""
        statement = select(GuestQrCredential).where(
            GuestQrCredential.token_hash == token_hash,
            GuestQrCredential.status == QrCredentialStatus.ACTIVE,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get_event(self, event_id: UUID) -> Event | None:
        return self._session.get(Event, event_id)

    def list_for_event(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[GuestQrCredential]:
        statement = (
            select(GuestQrCredential)
            .where(GuestQrCredential.event_id == event_id)
            .order_by(GuestQrCredential.issued_at.desc(), GuestQrCredential.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def add(self, credential: GuestQrCredential) -> None:
        self._session.add(credential)

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
