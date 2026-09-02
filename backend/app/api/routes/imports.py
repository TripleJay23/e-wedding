"""Thin authenticated event-scoped M4 Excel import routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status

from app.api.dependencies import get_current_administrator, get_import_service
from app.models.administrator import Administrator
from app.models.imports import ImportRowStatus
from app.schemas.imports import (
    ImportBatchDetailResponse,
    ImportBatchListResponse,
    ImportBatchSummaryResponse,
    ImportProgressRequest,
    ImportProgressResponse,
    ImportReconciliationResponse,
    ImportRevalidationResponse,
    ImportRowCorrectionRequest,
    ImportRowListResponse,
    ImportRowResponse,
)
from app.services.imports import (
    ImportConflictError,
    ImportResourceNotFoundError,
    ImportService,
    ImportUploadRejectedError,
)

router = APIRouter(prefix="/events/{event_id}/imports", tags=["imports"])


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, ImportResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ImportConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, ImportUploadRejectedError):
        response_status = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if exc.code == "WORKBOOK_TOO_LARGE"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=response_status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    raise exc


def _detail_response(
    service: ImportService, event_id: UUID, batch_id: UUID
) -> ImportBatchDetailResponse:
    batch = service.get_batch(event_id, batch_id)
    reconciliation = ImportReconciliationResponse.model_validate(service.reconcile_batch(batch))
    return ImportBatchDetailResponse.model_validate(
        {
            **ImportBatchSummaryResponse.model_validate(batch).model_dump(),
            "column_mapping_snapshot": batch.column_mapping_snapshot,
            "structure_snapshot": batch.structure_snapshot,
            "guest_field_snapshot": batch.guest_field_snapshot,
            "max_companions_snapshot": batch.max_companions_snapshot,
            "reconciliation": reconciliation,
        }
    )


@router.post("", response_model=ImportBatchDetailResponse, status_code=status.HTTP_201_CREATED)
async def upload_workbook(
    event_id: UUID,
    request: Request,
    workbook: Annotated[UploadFile, File(description="Private XLSX guest workbook")],
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> ImportBatchDetailResponse:
    """Upload one bounded private XLSX and return its deterministic staged preview."""
    maximum_bytes: int = request.app.state.settings.MAX_IMPORT_WORKBOOK_BYTES
    content = await workbook.read(maximum_bytes + 1)
    await workbook.close()
    try:
        batch = service.upload_workbook(
            event_id,
            administrator_id=administrator.id,
            filename=workbook.filename,
            content_type=workbook.content_type,
            content=content,
        )
        return _detail_response(service, event_id, batch.id)
    except (ImportResourceNotFoundError, ImportConflictError, ImportUploadRejectedError) as exc:
        _translate_domain_error(exc)


@router.get("", response_model=ImportBatchListResponse)
def list_import_history(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ImportBatchListResponse:
    """List event import history without loading staged rows."""
    try:
        batches = service.list_batches(event_id, limit=limit, offset=offset)
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)
    return ImportBatchListResponse(items=batches, limit=limit, offset=offset)


@router.get("/{batch_id}", response_model=ImportBatchDetailResponse)
def get_import_batch(
    event_id: UUID,
    batch_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> ImportBatchDetailResponse:
    """Return current batch structure, policy snapshot, and reconciliation."""
    try:
        return _detail_response(service, event_id, batch_id)
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)


@router.get("/{batch_id}/reconciliation", response_model=ImportReconciliationResponse)
def reconcile_import_batch(
    event_id: UUID,
    batch_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> ImportReconciliationResponse:
    """Return the exact current candidate-row partition."""
    try:
        return ImportReconciliationResponse.model_validate(service.reconcile(event_id, batch_id))
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)


@router.get("/{batch_id}/rows", response_model=ImportRowListResponse)
def list_staged_rows(
    event_id: UUID,
    batch_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
    row_status: Annotated[ImportRowStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ImportRowListResponse:
    """List authorized staged values and errors in workbook order."""
    try:
        rows = service.list_rows(
            event_id,
            batch_id,
            row_status=row_status,
            limit=limit,
            offset=offset,
        )
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)
    return ImportRowListResponse(items=rows, limit=limit, offset=offset)


@router.patch("/{batch_id}/rows/{row_id}", response_model=ImportRowResponse)
def correct_staged_row(
    event_id: UUID,
    batch_id: UUID,
    row_id: UUID,
    payload: ImportRowCorrectionRequest,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> ImportRowResponse:
    """Version canonical staged values while preserving workbook provenance."""
    try:
        row = service.correct_row(
            event_id,
            batch_id,
            row_id,
            administrator_id=administrator.id,
            expected_version=payload.expected_version,
            values=payload.values.model_dump(exclude_unset=True),
        )
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)
    return ImportRowResponse.model_validate(row)


@router.post("/{batch_id}/rows/{row_id}/revalidate", response_model=ImportRowResponse)
def revalidate_staged_row(
    event_id: UUID,
    batch_id: UUID,
    row_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> ImportRowResponse:
    """Explicitly revalidate one corrected row under its batch snapshot."""
    try:
        row = service.revalidate_row(
            event_id,
            batch_id,
            row_id,
            administrator_id=administrator.id,
        )
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)
    return ImportRowResponse.model_validate(row)


@router.post("/{batch_id}/revalidate", response_model=ImportRevalidationResponse)
def revalidate_pending_rows(
    event_id: UUID,
    batch_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
) -> ImportRevalidationResponse:
    """Revalidate all pending corrected rows in deterministic source order."""
    try:
        rows = service.revalidate_pending_rows(
            event_id,
            batch_id,
            administrator_id=administrator.id,
        )
        reconciliation = service.reconcile(event_id, batch_id)
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)
    return ImportRevalidationResponse(
        items=rows,
        reconciliation=ImportReconciliationResponse.model_validate(reconciliation),
    )


@router.post("/{batch_id}/progress", response_model=ImportProgressResponse)
def progress_valid_rows(
    event_id: UUID,
    batch_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[ImportService, Depends(get_import_service)],
    payload: ImportProgressRequest | None = None,
) -> ImportProgressResponse:
    """Import all valid rows, or an explicit exact UUID-selected subset, once."""
    try:
        result = service.progress_valid_rows(
            event_id,
            batch_id,
            administrator_id=administrator.id,
            staged_row_ids=None if payload is None else payload.staged_row_ids,
        )
        reconciliation = service.reconcile(event_id, batch_id)
    except (ImportResourceNotFoundError, ImportConflictError) as exc:
        _translate_domain_error(exc)
    return ImportProgressResponse(
        **result,
        reconciliation=ImportReconciliationResponse.model_validate(reconciliation),
    )
