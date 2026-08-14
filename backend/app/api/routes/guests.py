"""Thin authenticated manual-guest routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_current_administrator, get_event_service
from app.models.administrator import Administrator
from app.schemas.guests import (
    GuestCreateRequest,
    GuestListResponse,
    GuestResponse,
    GuestUpdateRequest,
)
from app.services.events import EventService, M3ConflictError, M3ResourceNotFoundError

router = APIRouter(prefix="/events/{event_id}/guests", tags=["guests"])


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, M3ResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, M3ConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise exc


@router.post("", response_model=GuestResponse, status_code=status.HTTP_201_CREATED)
def create_guest(
    event_id: UUID,
    payload: GuestCreateRequest,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> GuestResponse:
    """Create one event-policy-compliant manual guest."""
    try:
        guest = service.create_guest(event_id, payload.model_dump())
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return GuestResponse.model_validate(guest)


@router.get("", response_model=GuestListResponse)
def list_guests(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> GuestListResponse:
    """List manual guests in deterministic name/identity order with a hard bound."""
    try:
        guests = service.list_guests(event_id, limit=limit, offset=offset)
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return GuestListResponse(items=guests, limit=limit, offset=offset)


@router.get("/{guest_id}", response_model=GuestResponse)
def get_guest(
    event_id: UUID,
    guest_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> GuestResponse:
    """Return one manual guest only within its event scope."""
    try:
        guest = service.get_guest(event_id, guest_id)
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return GuestResponse.model_validate(guest)


@router.patch("/{guest_id}", response_model=GuestResponse)
def update_guest(
    event_id: UUID,
    guest_id: UUID,
    payload: GuestUpdateRequest,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> GuestResponse:
    """Update one manual guest under current event field and companion policy."""
    try:
        guest = service.update_guest(
            event_id,
            guest_id,
            payload.model_dump(exclude_unset=True),
        )
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return GuestResponse.model_validate(guest)
