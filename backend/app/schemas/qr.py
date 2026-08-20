"""Metadata-only M5 QR API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.qr import QrCredentialStatus


class QrCredentialMetadata(BaseModel):
    """Safe lifecycle metadata with no bearer, verifier, path, or guest PII."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    guest_id: UUID
    status: QrCredentialStatus
    replaces_credential_id: UUID | None
    issued_at: datetime
    deactivated_at: datetime | None
    superseded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class QrCredentialListResponse(BaseModel):
    """Bounded event-scoped credential metadata page."""

    items: list[QrCredentialMetadata]
    limit: int
    offset: int
