"""Password hashing and bearer access-token primitives."""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.settings import Settings

JWT_ALGORITHM = "HS256"


class InvalidAccessTokenError(ValueError):
    """Raised when an access token cannot be trusted."""


class ExpiredAccessTokenError(InvalidAccessTokenError):
    """Raised when an otherwise valid access token has expired."""


@dataclass(frozen=True, repr=False)
class IssuedAccessToken:
    """A newly issued bearer token and its bounded lifetime."""

    value: str = field(repr=False)
    expires_in_seconds: int


class PasswordManager:
    """Central Argon2id password hashing policy."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher(type=Type.ID)
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    def hash_password(self, password: str) -> str:
        """Hash one administrator password with Argon2id."""
        return self._hasher.hash(password)

    def verify_password(self, password: str, password_hash: str | None) -> bool:
        """Verify without exposing whether an administrator record exists."""
        candidate_hash = password_hash or self._dummy_hash
        try:
            return self._hasher.verify(candidate_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False


class AccessTokenManager:
    """Issue and strictly validate minimal short-lived JWT access tokens."""

    def __init__(
        self,
        *,
        signing_secret: str,
        issuer: str,
        audience: str,
        ttl_minutes: int,
    ) -> None:
        self._signing_secret = signing_secret
        self._issuer = issuer
        self._audience = audience
        self._ttl = timedelta(minutes=ttl_minutes)

    def issue(self, administrator_id: UUID, *, now: datetime | None = None) -> IssuedAccessToken:
        """Issue a minimal token whose authorization is reloaded from PostgreSQL."""
        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + self._ttl
        value = jwt.encode(
            {
                "sub": str(administrator_id),
                "iss": self._issuer,
                "aud": self._audience,
                "iat": issued_at,
                "exp": expires_at,
            },
            self._signing_secret,
            algorithm=JWT_ALGORITHM,
        )
        return IssuedAccessToken(
            value=value,
            expires_in_seconds=int(self._ttl.total_seconds()),
        )

    def decode_subject(self, token: str) -> UUID:
        """Validate signature, algorithm, issuer, audience, time, and subject."""
        try:
            payload = jwt.decode(
                token,
                self._signing_secret,
                algorithms=[JWT_ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["sub", "iss", "aud", "iat", "exp"]},
            )
            subject = payload["sub"]
            if not isinstance(subject, str):
                raise InvalidAccessTokenError("access token subject is invalid")
            return UUID(subject)
        except jwt.ExpiredSignatureError as exc:
            raise ExpiredAccessTokenError("access token has expired") from exc
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessTokenError("access token is invalid") from exc


@dataclass(frozen=True)
class AuthenticationSecurity:
    """Process-scoped authentication security components."""

    passwords: PasswordManager
    access_tokens: AccessTokenManager

    @classmethod
    def from_settings(cls, settings: Settings) -> "AuthenticationSecurity":
        """Build security components only from validated environment settings."""
        return cls(
            passwords=PasswordManager(),
            access_tokens=AccessTokenManager(
                signing_secret=settings.JWT_SECRET.get_secret_value(),
                issuer=settings.JWT_ISSUER,
                audience=settings.JWT_AUDIENCE,
                ttl_minutes=settings.ACCESS_TOKEN_TTL_MINUTES,
            ),
        )
