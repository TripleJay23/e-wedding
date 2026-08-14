"""Administrator authentication API schemas."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models.administrator import AdministratorRole


class LoginRequest(BaseModel):
    """Bounded administrator credentials."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024, repr=False)


class AccessTokenResponse(BaseModel):
    """Short-lived bearer token response."""

    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)


class CurrentAdministratorResponse(BaseModel):
    """Minimal current-administrator representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    role: AdministratorRole
