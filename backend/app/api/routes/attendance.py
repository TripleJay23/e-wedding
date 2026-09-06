"""Thin authenticated M7 gate-validation and attendance routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_attendance_service, get_current_administrator
from app.models.administrator import Administrator
from app.schemas.attendance import (
    AttendanceListResponse,
    AttendanceRecordResponse,
    GateAdmitRequest,
    GateAdmitResponse,
    GateInspectRequest,
    GateInspectResponse,
    GateScanAttemptListResponse,
    GateScanRequest,
    GateScanResponse,
)
from app.services.attendance import (
    AttendanceIdempotencyConflictError,
    AttendancePersistenceError,
    AttendanceRequestError,
    AttendanceResourceNotFoundError,
    AttendanceService,
    GateAdmissionCommand,
)

router = APIRouter(tags=["attendance"])


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, AttendanceResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, AttendanceRequestError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, AttendancePersistenceError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="attendance service is unavailable",
        ) from exc
    raise exc


@router.post("/attendance/scan", response_model=GateScanResponse, deprecated=True)
def scan_attendance(
    payload: GateScanRequest,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceService, Depends(get_attendance_service)],
) -> GateScanResponse:
    """Validate one decoded opaque credential and atomically record its outcome."""

    try:
        result = service.scan(
            event_id=payload.event_id,
            presented_credential=payload.credential,
            companions_admitted=payload.companions_admitted,
            administrator_id=administrator.id,
        )
    except (
        AttendanceResourceNotFoundError,
        AttendanceRequestError,
        AttendancePersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return GateScanResponse.model_validate(result)


@router.post("/attendance/inspect", response_model=GateInspectResponse)
def inspect_attendance(
    payload: GateInspectRequest,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceService, Depends(get_attendance_service)],
) -> GateInspectResponse:
    """Inspect current gate state without creating attendance or scan evidence."""

    try:
        result = service.inspect(
            event_id=payload.event_id,
            presented_credential=payload.credential,
        )
    except (
        AttendanceResourceNotFoundError,
        AttendanceRequestError,
        AttendancePersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return GateInspectResponse.model_validate(result)


@router.post("/attendance/admit", response_model=GateAdmitResponse)
def admit_attendance(
    payload: GateAdmitRequest,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceService, Depends(get_attendance_service)],
) -> GateAdmitResponse:
    """Execute or replay one explicit authoritative gate admission command."""

    try:
        result = service.admit(
            GateAdmissionCommand(
                request_id=payload.request_id,
                event_id=payload.event_id,
                presented_credential=payload.credential,
                arrival_mode=payload.arrival_mode,
                declared_companions=payload.declared_companions,
                companions_entering_now=payload.companions_entering_now,
                administrator_id=administrator.id,
            )
        )
    except AttendanceIdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from exc
    except (
        AttendanceResourceNotFoundError,
        AttendanceRequestError,
        AttendancePersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return GateAdmitResponse.model_validate(result)


@router.get(
    "/events/{event_id}/attendance",
    response_model=AttendanceListResponse,
)
def list_event_attendance(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceService, Depends(get_attendance_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AttendanceListResponse:
    try:
        items = service.list_event_attendance(event_id, limit=limit, offset=offset)
    except (AttendanceResourceNotFoundError, AttendancePersistenceError) as exc:
        _translate_domain_error(exc)
    return AttendanceListResponse(items=items, limit=limit, offset=offset)


@router.get(
    "/guests/{guest_id}/attendance",
    response_model=AttendanceRecordResponse,
)
def get_guest_attendance(
    guest_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceService, Depends(get_attendance_service)],
) -> AttendanceRecordResponse:
    try:
        attendance = service.get_guest_attendance(guest_id)
    except (AttendanceResourceNotFoundError, AttendancePersistenceError) as exc:
        _translate_domain_error(exc)
    return AttendanceRecordResponse.model_validate(attendance)


@router.get(
    "/events/{event_id}/scan-attempts",
    response_model=GateScanAttemptListResponse,
)
def list_event_scan_attempts(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceService, Depends(get_attendance_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> GateScanAttemptListResponse:
    try:
        items = service.list_event_scan_attempts(event_id, limit=limit, offset=offset)
    except (AttendanceResourceNotFoundError, AttendancePersistenceError) as exc:
        _translate_domain_error(exc)
    return GateScanAttemptListResponse(items=items, limit=limit, offset=offset)
