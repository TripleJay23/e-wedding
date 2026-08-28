"""Thin authenticated M9 summary, roster, and export routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import get_current_administrator, get_report_service
from app.models.administrator import Administrator
from app.models.attendance_corrections import (
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
)
from app.reports.exports import encode_csv, encode_xlsx
from app.schemas.reports import (
    AttendanceRosterResponse,
    JsonReportResponse,
    ReportFiltersResponse,
    ReportSummaryResponse,
)
from app.services.reports import (
    ReportFilters,
    ReportOutputFormat,
    ReportPersistenceError,
    ReportResourceNotFoundError,
    ReportService,
    ReportSizeLimitError,
)

router = APIRouter(prefix="/events/{event_id}/reports", tags=["reports"])

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
FilterText = Annotated[
    str | None,
    Query(min_length=1, max_length=64, pattern=r".*\S.*"),
]


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, ReportResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ReportSizeLimitError):
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    if isinstance(exc, ReportPersistenceError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="report service is unavailable",
        ) from exc
    raise exc


def _filters(
    effective_status: EffectiveAttendanceStatus | None,
    effective_source: EffectiveAttendanceSource | None,
    category: str | None,
    table_number: str | None,
    invitation_type: str | None,
) -> ReportFilters:
    return ReportFilters(
        effective_status=effective_status,
        effective_source=effective_source,
        category=None if category is None else category.strip(),
        table_number=None if table_number is None else table_number.strip(),
        invitation_type=None if invitation_type is None else invitation_type.strip(),
    )


def _summary(snapshot) -> ReportSummaryResponse:
    return ReportSummaryResponse.model_validate(snapshot)


def _json_report(snapshot) -> JsonReportResponse:
    return JsonReportResponse(
        summary=_summary(snapshot),
        attendance_roster=list(snapshot.attendance_roster),
        filters=ReportFiltersResponse.model_validate(snapshot.filters),
    )


@router.get("/summary", response_model=ReportSummaryResponse)
def get_event_report_summary(
    event_id: UUID,
    response: Response,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ReportService, Depends(get_report_service)],
) -> ReportSummaryResponse:
    try:
        snapshot = service.build_snapshot(event_id)
    except (ReportResourceNotFoundError, ReportSizeLimitError, ReportPersistenceError) as exc:
        _translate_domain_error(exc)
    response.headers.update(_PRIVATE_HEADERS)
    return _summary(snapshot)


@router.get("/attendance", response_model=AttendanceRosterResponse)
def get_event_attendance_roster(
    event_id: UUID,
    response: Response,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ReportService, Depends(get_report_service)],
    effective_status: Annotated[
        EffectiveAttendanceStatus | None,
        Query(alias="status"),
    ] = None,
    effective_source: Annotated[
        EffectiveAttendanceSource | None,
        Query(alias="source"),
    ] = None,
    category: FilterText = None,
    table_number: FilterText = None,
    invitation_type: FilterText = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AttendanceRosterResponse:
    selected_filters = _filters(
        effective_status,
        effective_source,
        category,
        table_number,
        invitation_type,
    )
    try:
        snapshot = service.build_snapshot(event_id, filters=selected_filters)
    except (ReportResourceNotFoundError, ReportSizeLimitError, ReportPersistenceError) as exc:
        _translate_domain_error(exc)
    response.headers.update(_PRIVATE_HEADERS)
    return AttendanceRosterResponse(
        summary=_summary(snapshot),
        items=list(snapshot.attendance_roster[offset : offset + limit]),
        filters=ReportFiltersResponse.model_validate(snapshot.filters),
        total=len(snapshot.attendance_roster),
        limit=limit,
        offset=offset,
    )


@router.get("/export/{output_format}")
def export_event_report(
    event_id: UUID,
    output_format: ReportOutputFormat,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ReportService, Depends(get_report_service)],
    effective_status: Annotated[
        EffectiveAttendanceStatus | None,
        Query(alias="status"),
    ] = None,
    effective_source: Annotated[
        EffectiveAttendanceSource | None,
        Query(alias="source"),
    ] = None,
    category: FilterText = None,
    table_number: FilterText = None,
    invitation_type: FilterText = None,
) -> Response:
    selected_filters = _filters(
        effective_status,
        effective_source,
        category,
        table_number,
        invitation_type,
    )
    try:
        snapshot = service.build_snapshot(event_id, filters=selected_filters)
        if output_format is ReportOutputFormat.JSON:
            content = _json_report(snapshot).model_dump_json().encode("utf-8")
            media_type = "application/json"
        elif output_format is ReportOutputFormat.CSV:
            content = encode_csv(snapshot)
            media_type = "text/csv; charset=utf-8"
        else:
            content = encode_xlsx(snapshot)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except (ReportResourceNotFoundError, ReportSizeLimitError, ReportPersistenceError) as exc:
        _translate_domain_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="report generation failed",
        ) from exc

    headers = {
        **_PRIVATE_HEADERS,
        "Content-Disposition": (
            f'attachment; filename="attendance-report-{event_id}.{output_format.value}"'
        ),
    }
    return Response(content=content, media_type=media_type, headers=headers)
