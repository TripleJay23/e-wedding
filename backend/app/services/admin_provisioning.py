"""One-time administrator provisioning service."""

from typing import Protocol

from sqlalchemy.exc import IntegrityError

from app.core.identity import normalize_administrator_username
from app.core.security import PasswordManager
from app.models.administrator import Administrator, AdministratorRole


class AdministratorProvisioningStore(Protocol):
    """Minimum persistence contract for one-time provisioning."""

    def get_singleton(self) -> Administrator | None: ...

    def add(self, administrator: Administrator) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class AdministratorAlreadyProvisionedError(RuntimeError):
    """Raised instead of overwriting the existing administrator."""


class AdministratorProvisioningConflictError(RuntimeError):
    """Raised when concurrent provisioning loses the singleton race."""


class AdministratorProvisioningService:
    """Create exactly one active SUPER_ADMIN without update/reset behavior."""

    def __init__(
        self,
        repository: AdministratorProvisioningStore,
        password_manager: PasswordManager,
    ) -> None:
        self._repository = repository
        self._password_manager = password_manager

    def provision(self, username: str, password: str) -> Administrator:
        """Create the sole administrator or fail closed if one already exists."""
        if self._repository.get_singleton() is not None:
            raise AdministratorAlreadyProvisionedError("administrator is already provisioned")

        administrator = Administrator(
            username=normalize_administrator_username(username),
            password_hash=self._password_manager.hash_password(password),
            role=AdministratorRole.SUPER_ADMIN,
            is_active=True,
        )
        self._repository.add(administrator)
        try:
            self._repository.commit()
        except IntegrityError as exc:
            self._repository.rollback()
            raise AdministratorProvisioningConflictError(
                "administrator provisioning conflicted with an existing account"
            ) from exc
        return administrator
