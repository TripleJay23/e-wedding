"""Authentication environment configuration failure tests."""

import pytest
from pydantic import ValidationError

from app.core.settings import AppEnvironment, Settings

BASE_VALUES = {
    "APP_ENV": AppEnvironment.DEVELOPMENT,
    "DATABASE_URL": "postgresql+psycopg://wedding_app:synthetic@localhost/wedding_ecard_dev",
    "JWT_SECRET": "synthetic-jwt-signing-secret-at-least-32-bytes",
    "JWT_ISSUER": "synthetic-issuer",
    "JWT_AUDIENCE": "synthetic-audience",
}


@pytest.mark.parametrize(
    "secret",
    ["short", "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"],
)
def test_jwt_secret_rejects_short_or_placeholder_values(secret: str) -> None:
    values = {**BASE_VALUES, "JWT_SECRET": secret}

    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(**values)


def test_jwt_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    values = dict(BASE_VALUES)
    del values["JWT_SECRET"]
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(**values)


@pytest.mark.parametrize("ttl", [0, 61])
def test_access_token_lifetime_is_short_and_bounded(ttl: int) -> None:
    with pytest.raises(ValidationError, match="ACCESS_TOKEN_TTL_MINUTES"):
        Settings(**BASE_VALUES, ACCESS_TOKEN_TTL_MINUTES=ttl)


@pytest.mark.parametrize("field", ["JWT_ISSUER", "JWT_AUDIENCE"])
def test_token_trust_identity_cannot_contain_whitespace(field: str) -> None:
    values = {**BASE_VALUES, field: "invalid identity"}

    with pytest.raises(ValidationError, match=field):
        Settings(**values)
