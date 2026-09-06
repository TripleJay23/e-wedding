"""Future-Flutter-friendly M9 report and attendance-roster contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.attendance_corrections import (
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
)
from app.models.event import EventStatus


class ReportEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_name: str
    bride_name: str
    groom_name: str
    event_datetime: datetime
    venue: str
    status: EventStatus


class ReportTotalsResponse(BaseModel):
    """Active invitation-holder totals; percentage denominator is invited holders."""

    model_config = ConfigDict(from_attributes=True)

    named_holders_invited: int = Field(ge=0)
    named_holders_effectively_attended: int = Field(ge=0)
    named_holders_effectively_not_attended: int = Field(ge=0)
    companion_capacity: int = Field(ge=0)
    effective_admitted_companions: int = Field(ge=0)
    declared_companions: int = Field(ge=0)
    raw_companions_admitted: int = Field(ge=0)
    remaining_declared_companion_slots: int = Field(ge=0)
    released_companion_allowance: int = Field(ge=0)
    uncommitted_companion_allowance: int = Field(ge=0)
    maximum_total_people_capacity: int = Field(ge=0)
    effective_total_people_attended: int = Field(ge=0)
    remaining_people_capacity: int = Field(ge=0)
    named_holder_attendance_percentage: float = Field(
        ge=0,
        le=100,
        description=(
            "Effectively attended named holders divided by invited active named holders, "
            "rounded half-up to two decimal places; zero when no holders are invited."
        ),
    )


class RawGateMetricsResponse(BaseModel):
    """Immutable M7 operational history, intentionally separate from effective totals."""

    model_config = ConfigDict(from_attributes=True)

    real_gate_admissions: int = Field(ge=0)
    duplicate_scan_attempts: int = Field(ge=0)
    unknown_credential_attempts: int = Field(ge=0)
    wrong_event_attempts: int = Field(ge=0)
    inactive_credential_attempts: int = Field(ge=0)
    superseded_credential_attempts: int = Field(ge=0)
    ineligible_guest_attempts: int = Field(ge=0)
    companion_limit_exceeded_attempts: int = Field(ge=0)
    actual_companions_admitted_at_gate: int = Field(ge=0)


class ReportSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: ReportEventResponse
    totals: ReportTotalsResponse
    raw_gate_metrics: RawGateMetricsResponse


class AttendanceRosterRowResponse(BaseModel):
    """Report-admin fields only; phone, email, QR, paths, and reason notes are absent."""

    model_config = ConfigDict(from_attributes=True)

    guest_id: UUID
    full_name: str
    invitation_type: str
    category: str | None
    table_number: str | None
    allowed_companions: int = Field(ge=0, le=2)
    declared_companions: int | None = Field(default=None, ge=0, le=2)
    effective_status: EffectiveAttendanceStatus
    effective_source: EffectiveAttendanceSource
    effective_admitted_companions: int = Field(ge=0, le=2)
    raw_companions_admitted: int = Field(ge=0)
    remaining_companion_slots: int | None = Field(default=None, ge=0, le=2)
    released_companion_allowance: int | None = Field(default=None, ge=0, le=2)
    effective_party_size: int = Field(ge=0, le=3)
    effective_at: datetime | None
    raw_gate_attended: bool
    raw_gate_checked_in_at: datetime | None
    correction_revision: int = Field(ge=0)
    has_correction: bool


class ReportFiltersResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    effective_status: EffectiveAttendanceStatus | None
    effective_source: EffectiveAttendanceSource | None
    category: str | None
    table_number: str | None
    invitation_type: str | None


class AttendanceRosterResponse(BaseModel):
    summary: ReportSummaryResponse
    items: list[AttendanceRosterRowResponse]
    filters: ReportFiltersResponse
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class JsonReportResponse(BaseModel):
    summary: ReportSummaryResponse
    attendance_roster: list[AttendanceRosterRowResponse]
    filters: ReportFiltersResponse
