"""Privacy-safe authenticated M6 API contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.cards import (
    DesignApprovalStatus,
    GeneratedCardStatus,
    PhotoFit,
    TemplateVersionStatus,
)


class TemplateVersionMetadata(BaseModel):
    """Public technical identity of one verified immutable template version."""

    id: UUID
    template_key: str
    display_name: str
    version: int
    raw_package_sha256: str
    logical_package_sha256: str
    manifest_sha256: str
    approved_preview_sha256: str
    canvas_width: int
    canvas_height: int
    asset_count: int
    font_count: int
    status: TemplateVersionStatus
    verified_at: datetime


class TemplateVersionListResponse(BaseModel):
    items: list[TemplateVersionMetadata]
    limit: int
    offset: int


class EventPhotoMetadata(BaseModel):
    """Private-photo technical metadata with no internal path."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    sha256: str
    media_type: str
    byte_size: int
    pixel_width: int
    pixel_height: int
    is_primary: bool
    uploaded_by: UUID
    created_at: datetime
    updated_at: datetime


class EventPhotoListResponse(BaseModel):
    items: list[EventPhotoMetadata]
    limit: int
    offset: int


class CardDesignRevisionCreate(BaseModel):
    """Constrained generic design controls; coordinates remain package-owned."""

    model_config = ConfigDict(extra="forbid")

    template_key: Literal["wedding_floral"] = "wedding_floral"
    template_version: Literal[10] = 10
    event_photo_id: UUID
    photo_fit: PhotoFit = PhotoFit.COVER
    photo_focus_x: Decimal = Field(default=Decimal("0.5"), ge=0, le=1, decimal_places=3)
    photo_focus_y: Decimal = Field(default=Decimal("0.5"), ge=0, le=1, decimal_places=3)
    photo_zoom: Decimal = Field(default=Decimal("1.0"), ge=1, le=3, decimal_places=3)

    @model_validator(mode="after")
    def validate_fit_zoom(self) -> Self:
        if self.photo_fit is PhotoFit.CONTAIN and self.photo_zoom != Decimal("1"):
            raise ValueError("CONTAIN photo fit requires zoom 1.0")
        return self


class CardDesignRevisionMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    design_id: UUID
    event_id: UUID
    revision_number: int
    template_version_id: UUID
    event_photo_id: UUID
    photo_fit: PhotoFit
    photo_focus_x: Decimal
    photo_focus_y: Decimal
    photo_zoom: Decimal
    created_by: UUID
    created_at: datetime


class CardDesignRevisionListResponse(BaseModel):
    items: list[CardDesignRevisionMetadata]
    limit: int
    offset: int


class CardDesignApprovalMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    design_revision_id: UUID
    status: DesignApprovalStatus
    approved_by: UUID
    approved_at: datetime
    revoked_by: UUID | None
    revoked_at: datetime | None


class GeneratedCardMetadata(BaseModel):
    """Traceability metadata without storage keys, internal paths, or guest PII."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    guest_id: UUID
    design_revision_id: UUID
    approval_id: UUID
    template_version_id: UUID
    qr_credential_id: UUID
    render_input_sha256: str
    artifact_sha256: str
    media_type: str
    byte_size: int
    pixel_width: int
    pixel_height: int
    status: GeneratedCardStatus
    generated_by: UUID
    generated_at: datetime
    stale_at: datetime | None


class GeneratedCardListResponse(BaseModel):
    items: list[GeneratedCardMetadata]
    limit: int
    offset: int


class BulkCardGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guest_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    limit: int = Field(default=100, ge=1, le=100)

    @field_validator("guest_ids")
    @classmethod
    def require_unique_guests(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("guest_ids must be unique")
        return value


class BulkCardGenerationResponse(BaseModel):
    items: list[GeneratedCardMetadata]
    generated_or_current_count: int


CardIdList = Annotated[list[UUID], Field(min_length=1, max_length=100)]


class CardExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_ids: CardIdList

    @field_validator("card_ids")
    @classmethod
    def require_unique_cards(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("card_ids must be unique")
        return value
