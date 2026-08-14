"""Thin authenticated event and guest-field routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_current_administrator, get_event_service
from app.models.administrator import Administrator
from app.models.event import EventStatus
from app.schemas.events import (
    EventCreateRequest,
    EventListResponse,
    EventResponse,
    EventUpdateRequest,
    GuestFieldConfigurationItem,
    GuestFieldConfigurationReplaceRequest,
    GuestFieldConfigurationResponse,
)
from app.services.events import EventService, M3ConflictError, M3ResourceNotFoundError

router = APIRouter(prefix="/events", tags=["events"])


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, M3ResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, M3ConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise exc


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreateRequest,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> EventResponse:
    """Create a DRAFT event and its seven default guest-field rules."""
    try:
        event = service.create_event(
            administrator_id=administrator.id,
            values=payload.model_dump(),
        )
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return EventResponse.model_validate(event)


@router.get("", response_model=EventListResponse)
def list_events(
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
    event_status: Annotated[EventStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> EventListResponse:
    """List events in deterministic date/identity order with a hard page bound."""
    events = service.list_events(event_status=event_status, limit=limit, offset=offset)
    return EventListResponse(items=events, limit=limit, offset=offset)


@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> EventResponse:
    """Return one event without relationship eager-loading."""
    try:
        event = service.get_event(event_id)
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return EventResponse.model_validate(event)


@router.patch("/{event_id}", response_model=EventResponse)
def update_event(
    event_id: UUID,
    payload: EventUpdateRequest,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> EventResponse:
    """Update event data or request a lifecycle transition centrally."""
    try:
        event = service.update_event(event_id, payload.model_dump(exclude_unset=True))
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return EventResponse.model_validate(event)


@router.get("/{event_id}/guest-fields", response_model=GuestFieldConfigurationResponse)
def get_guest_fields(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> GuestFieldConfigurationResponse:
    """Return the complete ordered canonical field configuration."""
    try:
        fields = service.get_guest_fields(event_id)
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return GuestFieldConfigurationResponse(
        event_id=event_id,
        fields=[GuestFieldConfigurationItem.model_validate(field) for field in fields],
    )


@router.put("/{event_id}/guest-fields", response_model=GuestFieldConfigurationResponse)
def replace_guest_fields(
    event_id: UUID,
    payload: GuestFieldConfigurationReplaceRequest,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[EventService, Depends(get_event_service)],
) -> GuestFieldConfigurationResponse:
    """Atomically replace enabled/required rules for exactly seven canonical fields."""
    configuration = [(item.field_key, item.enabled, item.required) for item in payload.fields]
    try:
        fields = service.replace_guest_fields(event_id, configuration)
    except (M3ResourceNotFoundError, M3ConflictError) as exc:
        _translate_domain_error(exc)
    return GuestFieldConfigurationResponse(
        event_id=event_id,
        fields=[GuestFieldConfigurationItem.model_validate(field) for field in fields],
    )
