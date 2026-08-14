"""Validated manual-guest API contracts."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.event import MAX_COMPANIONS_PER_INVITATION
from app.models.guest import GuestStatus

GuestName = Annotated[str, Field(min_length=1, max_length=160)]
PhoneNumber = Annotated[str, Field(min_length=1, max_length=32)]
EmailAddress = Annotated[str, Field(min_length=3, max_length=320)]
ShortGuestValue = Annotated[str, Field(min_length=1, max_length=64)]
AllowedCompanions = Annotated[int, Field(ge=0, le=MAX_COMPANIONS_PER_INVITATION)]


class GuestCreateRequest(BaseModel):
    """Create one manually entered invitation holder."""

    model_config = ConfigDict(extra="forbid")

    full_name: GuestName
    phone_number: PhoneNumber | None = None
    email: EmailAddress | None = None
    invitation_type: ShortGuestValue
    allowed_companions: AllowedCompanions = 0
    table_number: ShortGuestValue | None = None
    category: ShortGuestValue | None = None

    @field_validator(
        "full_name",
        "phone_number",
        "email",
        "invitation_type",
        "table_number",
        "category",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.count("@") != 1 or any(character.isspace() for character in value):
            raise ValueError("email address is invalid")
        local, domain = value.rsplit("@", 1)
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("email address is invalid")
        return value.casefold()


class GuestUpdateRequest(BaseModel):
    """Apply a non-empty partial manual guest update."""

    model_config = ConfigDict(extra="forbid")

    full_name: GuestName | None = None
    phone_number: PhoneNumber | None = None
    email: EmailAddress | None = None
    invitation_type: ShortGuestValue | None = None
    allowed_companions: AllowedCompanions | None = None
    table_number: ShortGuestValue | None = None
    category: ShortGuestValue | None = None
    status: GuestStatus | None = None

    @field_validator(
        "full_name",
        "phone_number",
        "email",
        "invitation_type",
        "table_number",
        "category",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.count("@") != 1 or any(character.isspace() for character in value):
            raise ValueError("email address is invalid")
        local, domain = value.rsplit("@", 1)
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("email address is invalid")
        return value.casefold()

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one guest field must be supplied")
        non_nullable = {"full_name", "invitation_type", "allowed_companions", "status"}
        null_non_nullable = [
            field_name
            for field_name in self.model_fields_set & non_nullable
            if getattr(self, field_name) is None
        ]
        if null_non_nullable:
            raise ValueError(f"fields may not be null: {', '.join(sorted(null_non_nullable))}")
        return self


class GuestResponse(BaseModel):
    """Persisted manual invitation holder without unrelated private data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    full_name: str
    phone_number: str | None
    email: str | None
    invitation_type: str
    allowed_companions: int
    table_number: str | None
    category: str | None
    status: GuestStatus
    created_at: datetime
    updated_at: datetime


class GuestListResponse(BaseModel):
    """Bounded deterministically ordered manual guest page."""

    items: list[GuestResponse]
    limit: int
    offset: int
