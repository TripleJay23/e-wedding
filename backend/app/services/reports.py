"""M9 canonical report calculation over M7 evidence and the M8 reducer."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.models.attendance_corrections import (
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
)
from app.models.event import EventStatus
from app.repositories.reports import ReportDatasetEvidence, ReportRepository
from app.services.attendance_corrections import derive_effective_attendance_state


class ReportResourceNotFoundError(LookupError):
    """Raised with one event-scoped response for missing report resources."""


class ReportSizeLimitError(ValueError):
    """Raised when an event exceeds the configured bounded report population."""


class ReportPersistenceError(RuntimeError):
    """Raised with a bounded message when the report snapshot cannot be read."""


class ReportOutputFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"


@dataclass(frozen=True, slots=True)
class ReportFilters:
    effective_status: EffectiveAttendanceStatus | None = None
    effective_source: EffectiveAttendanceSource | None = None
    category: str | None = None
    table_number: str | None = None
    invitation_type: str | None = None


@dataclass(frozen=True, slots=True)
class ReportEvent:
    id: UUID
    event_name: str
    bride_name: str
    groom_name: str
    event_datetime: datetime
    venue: str
    status: EventStatus


@dataclass(frozen=True, slots=True)
class ReportTotals:
    named_holders_invited: int
    named_holders_effectively_attended: int
    named_holders_effectively_not_attended: int
    companion_capacity: int
    effective_admitted_companions: int
    declared_companions: int
    raw_companions_admitted: int
    remaining_declared_companion_slots: int
    released_companion_allowance: int
    uncommitted_companion_allowance: int
    maximum_total_people_capacity: int
    effective_total_people_attended: int
    remaining_people_capacity: int
    named_holder_attendance_percentage: float


@dataclass(frozen=True, slots=True)
class RawGateMetrics:
    real_gate_admissions: int
    duplicate_scan_attempts: int
    unknown_credential_attempts: int
    wrong_event_attempts: int
    inactive_credential_attempts: int
    superseded_credential_attempts: int
    ineligible_guest_attempts: int
    companion_limit_exceeded_attempts: int
    actual_companions_admitted_at_gate: int


@dataclass(frozen=True, slots=True)
class AttendanceRosterRow:
    guest_id: UUID
    full_name: str
    invitation_type: str
    category: str | None
    table_number: str | None
    allowed_companions: int
    declared_companions: int | None
    effective_status: EffectiveAttendanceStatus
    effective_source: EffectiveAttendanceSource
    effective_admitted_companions: int
    raw_companions_admitted: int
    remaining_companion_slots: int | None
    released_companion_allowance: int | None
    effective_party_size: int
    effective_at: datetime | None
    raw_gate_attended: bool
    raw_gate_checked_in_at: datetime | None
    correction_revision: int
    has_correction: bool


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    event: ReportEvent
    totals: ReportTotals
    raw_gate_metrics: RawGateMetrics
    attendance_roster: tuple[AttendanceRosterRow, ...]
    filters: ReportFilters


class ReportService:
    """Build one privacy-bounded snapshot shared by JSON, CSV, and XLSX."""

    def __init__(self, repository: ReportRepository, *, maximum_rows: int) -> None:
        self._repository = repository
        self._maximum_rows = maximum_rows

    def build_snapshot(
        self,
        event_id: UUID,
        *,
        filters: ReportFilters | None = None,
    ) -> ReportSnapshot:
        selected_filters = filters or ReportFilters()
        try:
            evidence = self._repository.fetch_event_dataset(
                event_id,
                maximum_rows=self._maximum_rows,
            )
            self._repository.end_read()
        except SQLAlchemyError as exc:
            self._repository.end_read()
            raise ReportPersistenceError("report data could not be read") from exc
        if evidence is None:
            raise ReportResourceNotFoundError("report resource not found")
        if len(evidence.guests) > self._maximum_rows:
            raise ReportSizeLimitError("report exceeds the configured row limit")

        all_rows = self._effective_rows(evidence)
        totals = self._totals(all_rows)
        filtered_rows = tuple(row for row in all_rows if self._matches(row, selected_filters))
        return ReportSnapshot(
            event=ReportEvent(
                id=evidence.event.id,
                event_name=evidence.event.event_name,
                bride_name=evidence.event.bride_name,
                groom_name=evidence.event.groom_name,
                event_datetime=evidence.event.event_datetime,
                venue=evidence.event.venue,
                status=evidence.event.status,
            ),
            totals=totals,
            raw_gate_metrics=RawGateMetrics(**asdict(evidence.raw_gate)),
            attendance_roster=filtered_rows,
            filters=selected_filters,
        )

    @staticmethod
    def _effective_rows(evidence: ReportDatasetEvidence) -> tuple[AttendanceRosterRow, ...]:
        rows: list[AttendanceRosterRow] = []
        for guest in evidence.guests:
            effective = derive_effective_attendance_state(
                event_id=evidence.event.id,
                snapshot=guest.state_snapshot,
            )
            rows.append(
                AttendanceRosterRow(
                    guest_id=guest.id,
                    full_name=guest.full_name,
                    invitation_type=guest.invitation_type,
                    category=guest.category,
                    table_number=guest.table_number,
                    allowed_companions=guest.allowed_companions,
                    declared_companions=effective.declared_companions,
                    effective_status=effective.effective_status,
                    effective_source=effective.effective_source,
                    effective_admitted_companions=(
                        effective.effective_companions_admitted
                        if effective.companions_admitted is None
                        else effective.companions_admitted
                    ),
                    raw_companions_admitted=guest.raw_companions_admitted,
                    remaining_companion_slots=effective.remaining_companion_slots,
                    released_companion_allowance=(
                        None
                        if effective.declared_companions is None
                        else guest.allowed_companions - effective.declared_companions
                    ),
                    effective_party_size=(
                        int(effective.effective_status is EffectiveAttendanceStatus.ATTENDED)
                        + (
                            effective.effective_companions_admitted
                            if effective.companions_admitted is None
                            else effective.companions_admitted
                        )
                    ),
                    effective_at=effective.effective_at,
                    raw_gate_attended=guest.raw_gate_attended,
                    raw_gate_checked_in_at=guest.raw_gate_checked_in_at,
                    correction_revision=effective.correction_revision,
                    has_correction=effective.correction_revision > 0,
                )
            )
        return tuple(
            sorted(
                rows, key=lambda row: (row.full_name.casefold(), row.full_name, row.guest_id.hex)
            )
        )

    @staticmethod
    def _totals(rows: tuple[AttendanceRosterRow, ...]) -> ReportTotals:
        invited = len(rows)
        attended = sum(row.effective_status is EffectiveAttendanceStatus.ATTENDED for row in rows)
        companions = sum(row.effective_admitted_companions for row in rows)
        declared = sum(row.declared_companions or 0 for row in rows)
        raw_companions = sum(row.raw_companions_admitted for row in rows)
        remaining_declared = sum(row.remaining_companion_slots or 0 for row in rows)
        released = sum(row.released_companion_allowance or 0 for row in rows)
        uncommitted = sum(row.allowed_companions for row in rows if row.declared_companions is None)
        capacity = sum(row.allowed_companions for row in rows)
        maximum_people = invited + capacity
        total_attended = attended + companions
        percentage = 0.0
        if invited:
            percentage = float(
                (Decimal(attended * 100) / Decimal(invited)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )
        return ReportTotals(
            named_holders_invited=invited,
            named_holders_effectively_attended=attended,
            named_holders_effectively_not_attended=invited - attended,
            companion_capacity=capacity,
            effective_admitted_companions=companions,
            declared_companions=declared,
            raw_companions_admitted=raw_companions,
            remaining_declared_companion_slots=remaining_declared,
            released_companion_allowance=released,
            uncommitted_companion_allowance=uncommitted,
            maximum_total_people_capacity=maximum_people,
            effective_total_people_attended=total_attended,
            remaining_people_capacity=maximum_people - total_attended,
            named_holder_attendance_percentage=percentage,
        )

    @staticmethod
    def _matches(row: AttendanceRosterRow, filters: ReportFilters) -> bool:
        return not (
            (
                filters.effective_status is not None
                and row.effective_status is not filters.effective_status
            )
            or (
                filters.effective_source is not None
                and row.effective_source is not filters.effective_source
            )
            or (filters.category is not None and row.category != filters.category)
            or (filters.table_number is not None and row.table_number != filters.table_number)
            or (
                filters.invitation_type is not None
                and row.invitation_type != filters.invitation_type
            )
        )
