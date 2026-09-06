"""Strict M7 gate validation and attendance API contracts."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.attendance import (
    GateArrivalMode,
    GateInspectionState,
    GatePermittedAction,
    GateScanOutcome,
)
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


class GateInspectRequest(BaseModel):
    """Read-only credential lookup with no admission intent or idempotency metadata."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    credential: PresentedCredential


class GateInspectResponse(BaseModel):
    """Authoritative scanner state without credential, contact, or audit details."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    inspection_state: GateInspectionState
    permitted_actions: list[GatePermittedAction]
    guest_id: UUID | None
    guest_display_name: str | None
    invitation_type: str | None
    table_number: str | None
    primary_attended: bool | None
    primary_admission_locked: bool | None
    allowed_companions: int | None
    declared_companions: int | None
    companions_admitted: int | None
    remaining_companion_slots: int | None


class GateAdmitRequest(BaseModel):
    """Explicit, idempotent PRIMARY or COMPANION admission command."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    event_id: UUID
    credential: PresentedCredential
    arrival_mode: GateArrivalMode
    declared_companions: AdmittedCompanions | None = Field(
        default=None,
        description="Required for PRIMARY and forbidden for COMPANION, including explicit zero.",
    )
    companions_entering_now: AdmittedCompanions = Field(
        description="Zero or more for PRIMARY; at least one for COMPANION.",
    )

    @model_validator(mode="after")
    def validate_arrival_contract(self) -> "GateAdmitRequest":
        if self.arrival_mode is GateArrivalMode.PRIMARY:
            if self.declared_companions is None:
                raise ValueError("declared_companions is required for PRIMARY admission")
        elif self.declared_companions is not None:
            raise ValueError("declared_companions must be absent for COMPANION admission")
        elif self.companions_entering_now == 0:
            raise ValueError("companions_entering_now must be at least 1 for COMPANION admission")
        return self


class GateAdmitResponse(BaseModel):
    """Authoritative admission result for the mobile gate workflow."""

    model_config = ConfigDict(from_attributes=True)

    request_id: UUID
    scan_attempt_id: UUID
    event_id: UUID
    outcome: GateScanOutcome
    arrival_mode: GateArrivalMode
    admitted: bool
    guest_id: UUID | None
    guest_display_name: str | None
    invitation_type: str | None
    table_number: str | None
    primary_attended: bool | None
    primary_admitted_this_scan: bool
    allowed_companions: int | None
    declared_companions: int | None
    companions_admitted: int | None
    companions_admitted_this_scan: int
    remaining_companion_slots: int | None
    people_admitted_this_scan: int
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
