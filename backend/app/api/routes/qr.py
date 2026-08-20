"""Thin authenticated M5 QR management routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_current_administrator, get_qr_credential_service
from app.models.administrator import Administrator
from app.schemas.qr import QrCredentialListResponse, QrCredentialMetadata
from app.services.qr import (
    QrConflictError,
    QrCredentialService,
    QrPersistenceError,
    QrResourceNotFoundError,
    QrStorageError,
)

router = APIRouter(tags=["qr-codes"])


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, QrResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, QrConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, QrStorageError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="protected QR image storage is unavailable",
        ) from exc
    if isinstance(exc, QrPersistenceError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QR credential service is unavailable",
        ) from exc
    raise exc


@router.post(
    "/guests/{guest_id}/qr-code",
    response_model=QrCredentialMetadata,
    status_code=status.HTTP_201_CREATED,
)
def issue_qr_credential(
    guest_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[QrCredentialService, Depends(get_qr_credential_service)],
) -> QrCredentialMetadata:
    try:
        credential = service.issue(guest_id, administrator_id=administrator.id)
    except (QrResourceNotFoundError, QrConflictError, QrStorageError, QrPersistenceError) as exc:
        _translate_domain_error(exc)
    return QrCredentialMetadata.model_validate(credential)


@router.get("/guests/{guest_id}/qr-code", response_model=QrCredentialMetadata)
def get_qr_credential(
    guest_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[QrCredentialService, Depends(get_qr_credential_service)],
) -> QrCredentialMetadata:
    try:
        credential = service.get(guest_id)
    except (QrResourceNotFoundError, QrConflictError, QrStorageError, QrPersistenceError) as exc:
        _translate_domain_error(exc)
    return QrCredentialMetadata.model_validate(credential)


@router.post("/guests/{guest_id}/qr-code/deactivate", response_model=QrCredentialMetadata)
def deactivate_qr_credential(
    guest_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[QrCredentialService, Depends(get_qr_credential_service)],
) -> QrCredentialMetadata:
    try:
        credential = service.deactivate(guest_id)
    except (QrResourceNotFoundError, QrConflictError, QrStorageError, QrPersistenceError) as exc:
        _translate_domain_error(exc)
    return QrCredentialMetadata.model_validate(credential)


@router.post("/guests/{guest_id}/qr-code/activate", response_model=QrCredentialMetadata)
def activate_qr_credential(
    guest_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[QrCredentialService, Depends(get_qr_credential_service)],
) -> QrCredentialMetadata:
    try:
        credential = service.activate(guest_id)
    except (QrResourceNotFoundError, QrConflictError, QrStorageError, QrPersistenceError) as exc:
        _translate_domain_error(exc)
    return QrCredentialMetadata.model_validate(credential)


@router.post(
    "/guests/{guest_id}/qr-code/regenerate",
    response_model=QrCredentialMetadata,
    status_code=status.HTTP_201_CREATED,
)
def regenerate_qr_credential(
    guest_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[QrCredentialService, Depends(get_qr_credential_service)],
) -> QrCredentialMetadata:
    try:
        credential = service.regenerate(guest_id, administrator_id=administrator.id)
    except (QrResourceNotFoundError, QrConflictError, QrStorageError, QrPersistenceError) as exc:
        _translate_domain_error(exc)
    return QrCredentialMetadata.model_validate(credential)


@router.get("/events/{event_id}/qr-codes", response_model=QrCredentialListResponse)
def list_event_qr_credentials(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[QrCredentialService, Depends(get_qr_credential_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> QrCredentialListResponse:
    try:
        credentials = service.list_for_event(event_id, limit=limit, offset=offset)
    except (QrResourceNotFoundError, QrConflictError, QrStorageError, QrPersistenceError) as exc:
        _translate_domain_error(exc)
    return QrCredentialListResponse(items=credentials, limit=limit, offset=offset)
