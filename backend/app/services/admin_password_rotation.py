"""Guarded password rotation for the existing singleton administrator."""

from typing import Protocol
from uuid import UUID

from app.core.security import PasswordManager
from app.models.administrator import Administrator, AdministratorRole


class AdministratorPasswordRotationStore(Protocol):
    """Persistence contract for one locked singleton-password mutation."""

    def list_for_update(self) -> list[Administrator]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class AdministratorPasswordRotationError(RuntimeError):
    """Raised when a password rotation cannot be proven safe."""


class AdministratorPasswordRotationService:
    """Rotate only the password hash of one expected active SUPER_ADMIN."""

    def __init__(
        self,
        repository: AdministratorPasswordRotationStore,
        password_manager: PasswordManager,
    ) -> None:
        self._repository = repository
        self._password_manager = password_manager

    def rotate(self, expected_administrator_id: UUID, replacement_password: str) -> Administrator:
        """Lock, validate, and atomically rotate the expected singleton administrator."""
        administrators = self._repository.list_for_update()
        if not administrators:
            raise AdministratorPasswordRotationError("staging administrator does not exist")
        if len(administrators) != 1:
            raise AdministratorPasswordRotationError(
                "staging administrator singleton state is invalid"
            )

        administrator = administrators[0]
        if administrator.id != expected_administrator_id:
            raise AdministratorPasswordRotationError(
                "staging administrator identity does not match the expected UUID"
            )
        if (
            administrator.singleton_key != 1
            or administrator.role is not AdministratorRole.SUPER_ADMIN
            or administrator.is_active is not True
        ):
            raise AdministratorPasswordRotationError("staging administrator state is invalid")
        if self._password_manager.verify_password(
            replacement_password,
            administrator.password_hash,
        ):
            raise AdministratorPasswordRotationError(
                "replacement password must differ from the current password"
            )

        administrator.password_hash = self._password_manager.hash_password(replacement_password)
        try:
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return administrator
