"""Strict privacy-bounded M8 attendance-correction API contracts."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
)
from app.models.event import MAX_COMPANIONS_PER_INVITATION

ExpectedCorrectionRevision = Annotated[int, Field(strict=True, ge=0)]
CorrectionCompanions = Annotated[
    int,
    Field(strict=True, ge=0, le=MAX_COMPANIONS_PER_INVITATION),
]
ReasonNote = Annotated[str, Field(strict=True, max_length=2000)]


class AttendanceCorrectionRequest(BaseModel):
    """Only caller-owned optimistic state, decision, and bounded reason data."""

    model_config = ConfigDict(extra="forbid")

    expected_correction_revision: ExpectedCorrectionRevision
    expected_effective_status: EffectiveAttendanceStatus
    decision: AttendanceCorrectionDecision
    reason_code: AttendanceCorrectionReason
    reason_note: ReasonNote | None = None
    companions_admitted: CorrectionCompanions | None = None

    @model_validator(mode="after")
    def validate_decision_shape(self) -> Self:
        companions_supplied = "companions_admitted" in self.model_fields_set
        if self.decision is AttendanceCorrectionDecision.SET_ATTENDED:
            if not companions_supplied or self.companions_admitted is None:
                raise ValueError("SET_ATTENDED requires companions_admitted")
        elif companions_supplied:
            raise ValueError("decision does not accept companions_admitted")
        if self.reason_code is AttendanceCorrectionReason.OTHER and (
            self.reason_note is None or not self.reason_note.strip()
        ):
            raise ValueError("OTHER correction reason requires a note")
        return self


class EffectiveAttendanceStateResponse(BaseModel):
    """Derived state without guest PII, QR/card data, or reason details."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    guest_id: UUID
    effective_status: EffectiveAttendanceStatus
    effective_source: EffectiveAttendanceSource
    effective_companions_admitted: int = Field(ge=0, le=MAX_COMPANIONS_PER_INVITATION)
    effective_party_size: int = Field(ge=0, le=MAX_COMPANIONS_PER_INVITATION + 1)
    gate_attendance_record_id: UUID | None
    current_correction_id: UUID | None
    correction_revision: int = Field(ge=0)
    effective_at: datetime | None


class EffectiveAttendanceListResponse(BaseModel):
    items: list[EffectiveAttendanceStateResponse]
    limit: int
    offset: int


class AttendanceCorrectionResponse(BaseModel):
    """Routine append-only history metadata; sensitive note and actor are absent."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    guest_id: UUID
    revision_number: int = Field(ge=1)
    previous_correction_id: UUID | None
    decision: AttendanceCorrectionDecision
    reason_code: AttendanceCorrectionReason
    companions_admitted: int | None = Field(default=None, ge=0, le=2)
    gate_attendance_record_id: UUID | None
    previous_effective_status: EffectiveAttendanceStatus
    resulting_effective_status: EffectiveAttendanceStatus
    resulting_effective_source: EffectiveAttendanceSource
    created_at: datetime


class AttendanceCorrectionAuditDetailResponse(AttendanceCorrectionResponse):
    """The sole M8 response allowed to disclose the sensitive reason note."""

    reason_note: str | None


class AttendanceCorrectionResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    correction: AttendanceCorrectionResponse
    effective_state: EffectiveAttendanceStateResponse


class AttendanceCorrectionListResponse(BaseModel):
    items: list[AttendanceCorrectionResponse]
    limit: int
    offset: int
