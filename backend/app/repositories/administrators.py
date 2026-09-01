"""Administrator persistence operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.administrator import Administrator


class AdministratorRepository:
    """Perform narrow indexed administrator queries without relationship loading."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_username(self, username: str) -> Administrator | None:
        """Load the sole administrator by its canonical indexed identifier."""
        statement = select(Administrator).where(Administrator.username == username)
        return self._session.execute(statement).scalar_one_or_none()

    def get_by_id(self, administrator_id: UUID) -> Administrator | None:
        """Reload the persisted principal for bearer authorization."""
        return self._session.get(Administrator, administrator_id)

    def get_singleton(self) -> Administrator | None:
        """Return the existing administrator, if provisioned."""
        statement = select(Administrator).limit(1)
        return self._session.execute(statement).scalar_one_or_none()

    def list_for_update(self) -> list[Administrator]:
        """Lock and return every administrator row for a guarded singleton mutation."""
        statement = select(Administrator).order_by(Administrator.id).with_for_update()
        return list(self._session.execute(statement).scalars())

    def add(self, administrator: Administrator) -> None:
        """Stage a new administrator for persistence."""
        self._session.add(administrator)

    def commit(self) -> None:
        """Commit the current provisioning transaction."""
        self._session.commit()

    def rollback(self) -> None:
        """Rollback a failed provisioning transaction."""
        self._session.rollback()
