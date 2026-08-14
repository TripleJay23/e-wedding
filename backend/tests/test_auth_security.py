"""Authentication security primitive tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.security import (
    AccessTokenManager,
    ExpiredAccessTokenError,
    InvalidAccessTokenError,
    PasswordManager,
)

SYNTHETIC_SIGNING_SECRET = "synthetic-jwt-signing-secret-at-least-32-bytes"


def token_manager(*, issuer: str = "issuer", audience: str = "audience") -> AccessTokenManager:
    return AccessTokenManager(
        signing_secret=SYNTHETIC_SIGNING_SECRET,
        issuer=issuer,
        audience=audience,
        ttl_minutes=5,
    )


def test_passwords_are_argon2id_hashed_and_verified() -> None:
    manager = PasswordManager()
    password_hash = manager.hash_password("synthetic strong password")

    assert password_hash.startswith("$argon2id$")
    assert "synthetic strong password" not in password_hash
    assert manager.verify_password("synthetic strong password", password_hash) is True
    assert manager.verify_password("wrong", password_hash) is False
    assert manager.verify_password("wrong", None) is False
    assert manager.verify_password("wrong", "not-an-argon2-hash") is False


def test_token_requires_expected_issuer_audience_and_signature() -> None:
    administrator_id = uuid4()
    issued = token_manager().issue(administrator_id)

    assert token_manager().decode_subject(issued.value) == administrator_id
    with pytest.raises(InvalidAccessTokenError):
        token_manager(issuer="other").decode_subject(issued.value)
    with pytest.raises(InvalidAccessTokenError):
        token_manager(audience="other").decode_subject(issued.value)
    with pytest.raises(InvalidAccessTokenError):
        AccessTokenManager(
            signing_secret="different-synthetic-signing-secret-at-least-32",
            issuer="issuer",
            audience="audience",
            ttl_minutes=5,
        ).decode_subject(issued.value)


def test_expired_token_is_classified_without_exposing_token() -> None:
    manager = token_manager()
    issued = manager.issue(
        uuid4(),
        now=datetime.now(UTC) - timedelta(minutes=6),
    )

    with pytest.raises(ExpiredAccessTokenError):
        manager.decode_subject(issued.value)
    assert issued.value not in repr(issued)
