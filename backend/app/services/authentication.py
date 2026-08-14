"""Administrator authentication and bearer authorization service."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.identity import normalize_administrator_username
from app.core.security import (
    AccessTokenManager,
    InvalidAccessTokenError,
    IssuedAccessToken,
    PasswordManager,
)
from app.models.administrator import Administrator, AdministratorRole


class AdministratorReader(Protocol):
    """Minimum persistence contract required for authentication."""

    def get_by_username(self, username: str) -> Administrator | None: ...

    def get_by_id(self, administrator_id: UUID) -> Administrator | None: ...


class AuthenticationRejectedError(ValueError):
    """Raised for all externally indistinguishable authentication failures."""


@dataclass(frozen=True)
class LoginResult:
    """Successful login result without password state."""

    administrator: Administrator
    access_token: IssuedAccessToken


class AuthenticationService:
    """Authenticate and authorize the sole active SUPER_ADMIN."""

    def __init__(
        self,
        repository: AdministratorReader,
        password_manager: PasswordManager,
        access_token_manager: AccessTokenManager,
    ) -> None:
        self._repository = repository
        self._password_manager = password_manager
        self._access_token_manager = access_token_manager

    def login(self, username: str, password: str) -> LoginResult:
        """Authenticate with one indexed lookup and issue a short-lived token."""
        try:
            canonical_username = normalize_administrator_username(username)
        except ValueError:
            canonical_username = ""

        administrator = (
            self._repository.get_by_username(canonical_username) if canonical_username else None
        )
        password_is_valid = self._password_manager.verify_password(
            password,
            administrator.password_hash if administrator is not None else None,
        )
        if not password_is_valid or not self._is_permitted(administrator):
            raise AuthenticationRejectedError("administrator authentication rejected")

        assert administrator is not None
        return LoginResult(
            administrator=administrator,
            access_token=self._access_token_manager.issue(administrator.id),
        )

    def current_administrator(self, token: str) -> Administrator:
        """Validate a token and reload the active persisted principal once."""
        try:
            administrator_id = self._access_token_manager.decode_subject(token)
        except InvalidAccessTokenError as exc:
            raise AuthenticationRejectedError("administrator authentication rejected") from exc

        administrator = self._repository.get_by_id(administrator_id)
        if not self._is_permitted(administrator):
            raise AuthenticationRejectedError("administrator authentication rejected")
        assert administrator is not None
        return administrator

    @staticmethod
    def _is_permitted(administrator: Administrator | None) -> bool:
        return (
            administrator is not None
            and administrator.is_active is True
            and administrator.role is AdministratorRole.SUPER_ADMIN
        )
