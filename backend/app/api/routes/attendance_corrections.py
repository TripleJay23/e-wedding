"""Thin authenticated M8 attendance-correction and effective-state routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import get_attendance_correction_service, get_current_administrator
from app.models.administrator import Administrator
from app.schemas.attendance_corrections import (
    AttendanceCorrectionAuditDetailResponse,
    AttendanceCorrectionListResponse,
    AttendanceCorrectionRequest,
    AttendanceCorrectionResponse,
    AttendanceCorrectionResultResponse,
    EffectiveAttendanceListResponse,
    EffectiveAttendanceStateResponse,
)
from app.services.attendance_corrections import (
    AttendanceCorrectionConflictError,
    AttendanceCorrectionPersistenceError,
    AttendanceCorrectionRequestError,
    AttendanceCorrectionResourceNotFoundError,
    AttendanceCorrectionService,
)

router = APIRouter(tags=["attendance-corrections"])

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, AttendanceCorrectionResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, AttendanceCorrectionConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, AttendanceCorrectionRequestError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, AttendanceCorrectionPersistenceError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="attendance correction service is unavailable",
        ) from exc
    raise exc


def _protect(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


@router.get(
    "/events/{event_id}/attendance/effective",
    response_model=EffectiveAttendanceListResponse,
)
def list_effective_attendance_states(
    event_id: UUID,
    response: Response,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceCorrectionService, Depends(get_attendance_correction_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> EffectiveAttendanceListResponse:
    """Return one deterministic current state per event guest."""

    try:
        items = service.list_effective_states(event_id, limit=limit, offset=offset)
    except (
        AttendanceCorrectionResourceNotFoundError,
        AttendanceCorrectionRequestError,
        AttendanceCorrectionPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    _protect(response)
    return EffectiveAttendanceListResponse(items=items, limit=limit, offset=offset)


@router.get(
    "/events/{event_id}/attendance/guests/{guest_id}/effective",
    response_model=EffectiveAttendanceStateResponse,
)
def get_effective_attendance_state(
    event_id: UUID,
    guest_id: UUID,
    response: Response,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceCorrectionService, Depends(get_attendance_correction_service)],
) -> EffectiveAttendanceStateResponse:
    try:
        effective = service.get_effective_state(event_id, guest_id)
    except (
        AttendanceCorrectionResourceNotFoundError,
        AttendanceCorrectionPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    _protect(response)
    return EffectiveAttendanceStateResponse.model_validate(effective)


@router.get(
    "/events/{event_id}/attendance/corrections/{correction_id}/audit-detail",
    response_model=AttendanceCorrectionAuditDetailResponse,
)
def get_attendance_correction_audit_detail(
    event_id: UUID,
    correction_id: UUID,
    response: Response,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceCorrectionService, Depends(get_attendance_correction_service)],
) -> AttendanceCorrectionAuditDetailResponse:
    try:
        correction = service.get_correction_audit_detail(event_id, correction_id)
    except (
        AttendanceCorrectionResourceNotFoundError,
        AttendanceCorrectionPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    _protect(response)
    return AttendanceCorrectionAuditDetailResponse.model_validate(correction)


@router.get(
    "/events/{event_id}/attendance/corrections/{correction_id}",
    response_model=AttendanceCorrectionResponse,
)
def get_attendance_correction(
    event_id: UUID,
    correction_id: UUID,
    response: Response,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceCorrectionService, Depends(get_attendance_correction_service)],
) -> AttendanceCorrectionResponse:
    try:
        correction = service.get_correction(event_id, correction_id)
    except (
        AttendanceCorrectionResourceNotFoundError,
        AttendanceCorrectionPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    _protect(response)
    return AttendanceCorrectionResponse.model_validate(correction)


@router.get(
    "/events/{event_id}/attendance/guests/{guest_id}/corrections",
    response_model=AttendanceCorrectionListResponse,
)
def list_attendance_corrections(
    event_id: UUID,
    guest_id: UUID,
    response: Response,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceCorrectionService, Depends(get_attendance_correction_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AttendanceCorrectionListResponse:
    try:
        items = service.list_corrections(
            event_id,
            guest_id,
            limit=limit,
            offset=offset,
        )
    except (
        AttendanceCorrectionResourceNotFoundError,
        AttendanceCorrectionRequestError,
        AttendanceCorrectionPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    _protect(response)
    return AttendanceCorrectionListResponse(items=items, limit=limit, offset=offset)


@router.post(
    "/events/{event_id}/attendance/guests/{guest_id}/corrections",
    response_model=AttendanceCorrectionResultResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_attendance_correction(
    event_id: UUID,
    guest_id: UUID,
    payload: AttendanceCorrectionRequest,
    response: Response,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[AttendanceCorrectionService, Depends(get_attendance_correction_service)],
) -> AttendanceCorrectionResultResponse:
    try:
        result = service.record_correction(
            event_id=event_id,
            guest_id=guest_id,
            expected_correction_revision=payload.expected_correction_revision,
            expected_effective_status=payload.expected_effective_status,
            decision=payload.decision,
            reason_code=payload.reason_code,
            reason_note=payload.reason_note,
            companions_admitted=payload.companions_admitted,
            administrator_id=administrator.id,
        )
    except (
        AttendanceCorrectionResourceNotFoundError,
        AttendanceCorrectionConflictError,
        AttendanceCorrectionRequestError,
        AttendanceCorrectionPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    _protect(response)
    return AttendanceCorrectionResultResponse.model_validate(result)
