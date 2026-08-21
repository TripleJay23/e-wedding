"""Strict M7 gate validation and attendance API contracts."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.attendance import GateScanOutcome
from app.models.event import MAX_COMPANIONS_PER_INVITATION

PresentedCredential = Annotated[
    str,
    Field(min_length=32, max_length=32, pattern=r"^[A-Z2-7]{32}$"),
]
AdmittedCompanions = Annotated[
    int,
    Field(ge=0, le=MAX_COMPANIONS_PER_INVITATION),
]


class GateScanRequest(BaseModel):
    """Only selected event, opaque scanner output, and actual companion count."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    credential: PresentedCredential
    companions_admitted: AdmittedCompanions = 0


class GateScanResponse(BaseModel):
    """Immediate gate result with no credential, verifier, contact data, or paths."""

    model_config = ConfigDict(from_attributes=True)

    scan_attempt_id: UUID
    event_id: UUID
    outcome: GateScanOutcome
    admitted: bool
    already_checked_in: bool
    guest_id: UUID | None
    attendance_id: UUID | None
    guest_display_name: str | None
    invitation_type: str | None
    table_number: str | None
    allowed_companions: int | None
    companions_admitted: int | None
    admitted_party_size: int | None
    checked_in_at: datetime | None
    scanned_at: datetime


class AttendanceRecordResponse(BaseModel):
    """Safe M7 attendance metadata and minimum gate display context."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    guest_id: UUID
    guest_display_name: str
    invitation_type: str
    table_number: str | None
    allowed_companions: int
    companions_admitted: int
    admitted_party_size: int
    checked_in_at: datetime


class AttendanceListResponse(BaseModel):
    """Bounded deterministic event attendance page."""

    items: list[AttendanceRecordResponse]
    limit: int
    offset: int


class GateScanAttemptResponse(BaseModel):
    """Privacy-safe append-only scan evidence view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    guest_id: UUID | None
    attendance_id: UUID | None
    outcome: GateScanOutcome
    requested_companions: int
    scanned_at: datetime


class GateScanAttemptListResponse(BaseModel):
    """Bounded deterministic event scan-history page."""

    items: list[GateScanAttemptResponse]
    limit: int
    offset: int
