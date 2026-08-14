"""Validated M3 event and guest-field API contracts."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.event import (
    DEFAULT_GUEST_FIELD_CONFIGURATION,
    MAX_COMPANIONS_PER_INVITATION,
    EventStatus,
    GuestFieldKey,
)

EventName = Annotated[str, Field(min_length=1, max_length=160)]
PersonName = Annotated[str, Field(min_length=1, max_length=120)]
Venue = Annotated[str, Field(min_length=1, max_length=255)]
Address = Annotated[str, Field(min_length=1, max_length=500)]
Description = Annotated[str, Field(min_length=1, max_length=2000)]
CompanionMaximum = Annotated[int, Field(ge=0, le=MAX_COMPANIONS_PER_INVITATION)]


class EventCreateRequest(BaseModel):
    """Create one DRAFT wedding event with a bounded companion policy."""

    model_config = ConfigDict(extra="forbid")

    event_name: EventName
    bride_name: PersonName
    groom_name: PersonName
    event_datetime: AwareDatetime
    venue: Venue
    address: Address | None = None
    description: Description | None = None
    max_companions_per_invitation: CompanionMaximum = MAX_COMPANIONS_PER_INVITATION

    @field_validator(
        "event_name",
        "bride_name",
        "groom_name",
        "venue",
        "address",
        "description",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        """Store meaningful trimmed event text only."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized


class EventUpdateRequest(BaseModel):
    """Apply a non-empty partial event update through lifecycle rules."""

    model_config = ConfigDict(extra="forbid")

    event_name: EventName | None = None
    bride_name: PersonName | None = None
    groom_name: PersonName | None = None
    event_datetime: AwareDatetime | None = None
    venue: Venue | None = None
    address: Address | None = None
    description: Description | None = None
    status: EventStatus | None = None
    max_companions_per_invitation: CompanionMaximum | None = None

    @field_validator(
        "event_name",
        "bride_name",
        "groom_name",
        "venue",
        "address",
        "description",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        """Reject empty patches and nulls for non-nullable event fields."""
        if not self.model_fields_set:
            raise ValueError("at least one event field must be supplied")
        nullable = {"address", "description"}
        null_non_nullable = [
            field_name
            for field_name in self.model_fields_set - nullable
            if getattr(self, field_name) is None
        ]
        if null_non_nullable:
            raise ValueError(f"fields may not be null: {', '.join(sorted(null_non_nullable))}")
        return self


class EventResponse(BaseModel):
    """Persisted event identity, lifecycle, and policy."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_name: str
    bride_name: str
    groom_name: str
    event_datetime: datetime
    venue: str
    address: str | None
    description: str | None
    status: EventStatus
    max_companions_per_invitation: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class EventListResponse(BaseModel):
    """Bounded deterministically ordered event page."""

    items: list[EventResponse]
    limit: int
    offset: int


class GuestFieldConfigurationItem(BaseModel):
    """One canonical field rule in event-defined display order."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    field_key: GuestFieldKey
    enabled: bool
    required: bool

    @model_validator(mode="after")
    def validate_required_field(self) -> Self:
        if self.required and not self.enabled:
            raise ValueError("a required guest field must be enabled")
        if self.field_key in {GuestFieldKey.FULL_NAME, GuestFieldKey.INVITATION_TYPE} and (
            not self.enabled or not self.required
        ):
            raise ValueError(f"{self.field_key.value} must remain enabled and required")
        return self


class GuestFieldConfigurationReplaceRequest(BaseModel):
    """Atomically replace all seven event guest-field rules."""

    model_config = ConfigDict(extra="forbid")

    fields: list[GuestFieldConfigurationItem] = Field(
        min_length=len(DEFAULT_GUEST_FIELD_CONFIGURATION),
        max_length=len(DEFAULT_GUEST_FIELD_CONFIGURATION),
    )

    @model_validator(mode="after")
    def validate_complete_configuration(self) -> Self:
        keys = [item.field_key for item in self.fields]
        expected = {item[0] for item in DEFAULT_GUEST_FIELD_CONFIGURATION}
        if len(set(keys)) != len(keys):
            raise ValueError("guest field keys must be unique")
        if set(keys) != expected:
            raise ValueError("configuration must contain every canonical guest field exactly once")
        return self


class GuestFieldConfigurationResponse(BaseModel):
    """Complete event-scoped canonical guest-field configuration."""

    event_id: UUID
    fields: list[GuestFieldConfigurationItem]
