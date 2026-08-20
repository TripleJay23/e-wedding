"""Thin authenticated M6 template, design, photo, and generated-card routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from app.api.dependencies import get_card_service, get_current_administrator
from app.models.administrator import Administrator
from app.models.cards import GeneratedCardStatus
from app.schemas.cards import (
    BulkCardGenerationRequest,
    BulkCardGenerationResponse,
    CardDesignApprovalMetadata,
    CardDesignRevisionCreate,
    CardDesignRevisionListResponse,
    CardDesignRevisionMetadata,
    CardExportRequest,
    EventPhotoListResponse,
    EventPhotoMetadata,
    GeneratedCardListResponse,
    GeneratedCardMetadata,
    TemplateVersionListResponse,
    TemplateVersionMetadata,
)
from app.services.cards import (
    CardConflictError,
    CardInputError,
    CardPersistenceError,
    CardResourceNotFoundError,
    CardService,
    CardStorageError,
)

router = APIRouter(tags=["templates", "event-photos", "card-designs", "cards"])


def _translate_domain_error(exc: Exception) -> None:
    if isinstance(exc, CardResourceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, CardConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, CardInputError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, CardStorageError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="private card storage is unavailable",
        ) from exc
    if isinstance(exc, CardPersistenceError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="card service is unavailable",
        ) from exc
    raise exc


def _template_metadata(record: tuple[object, object]) -> TemplateVersionMetadata:
    preset, version = record
    return TemplateVersionMetadata.model_validate(
        {
            "id": version.id,
            "template_key": preset.template_key,
            "display_name": preset.display_name,
            "version": version.version,
            "raw_package_sha256": version.raw_package_sha256,
            "logical_package_sha256": version.logical_package_sha256,
            "manifest_sha256": version.manifest_sha256,
            "approved_preview_sha256": version.approved_preview_sha256,
            "canvas_width": version.canvas_width,
            "canvas_height": version.canvas_height,
            "asset_count": version.asset_count,
            "font_count": version.font_count,
            "status": version.status,
            "verified_at": version.verified_at,
        }
    )


@router.post(
    "/templates/wedding_floral/versions/10/register",
    response_model=TemplateVersionMetadata,
    status_code=status.HTTP_201_CREATED,
)
def register_verified_template(
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> TemplateVersionMetadata:
    try:
        return _template_metadata(
            service.register_verified_template(administrator_id=administrator.id)
        )
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardInputError,
        CardStorageError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)


@router.get("/templates", response_model=TemplateVersionListResponse)
def list_templates(
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> TemplateVersionListResponse:
    records = service.list_templates(limit=limit, offset=offset)
    return TemplateVersionListResponse(
        items=[_template_metadata(record) for record in records],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/templates/{template_key}/versions/{template_version}",
    response_model=TemplateVersionMetadata,
)
def get_template(
    template_key: str,
    template_version: int,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> TemplateVersionMetadata:
    try:
        return _template_metadata(service.get_template(template_key, template_version))
    except CardResourceNotFoundError as exc:
        _translate_domain_error(exc)


@router.get("/templates/wedding_floral/versions/10/approved-preview")
def download_approved_preview(
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> Response:
    try:
        content = service.read_approved_preview()
    except CardStorageError as exc:
        _translate_domain_error(exc)
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'inline; filename="wedding-floral-v10-approved-preview.png"',
        },
    )


@router.post(
    "/events/{event_id}/photos",
    response_model=EventPhotoMetadata,
    status_code=status.HTTP_201_CREATED,
)
async def upload_event_photo(
    event_id: UUID,
    request: Request,
    photo: Annotated[UploadFile, File(description="Private wedding/event photo")],
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
    select_as_primary: bool = True,
) -> EventPhotoMetadata:
    maximum_bytes: int = request.app.state.settings.MAX_EVENT_PHOTO_UPLOAD_BYTES
    content = await photo.read(maximum_bytes + 1)
    await photo.close()
    if len(content) > maximum_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="event photo exceeds the configured upload limit",
        )
    try:
        result = service.upload_event_photo(
            event_id,
            content,
            administrator_id=administrator.id,
            select_as_primary=select_as_primary,
        )
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardInputError,
        CardStorageError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return EventPhotoMetadata.model_validate(result)


@router.get("/events/{event_id}/photos", response_model=EventPhotoListResponse)
def list_event_photos(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> EventPhotoListResponse:
    try:
        photos = service.list_event_photos(event_id, limit=limit, offset=offset)
    except CardResourceNotFoundError as exc:
        _translate_domain_error(exc)
    return EventPhotoListResponse(items=photos, limit=limit, offset=offset)


@router.post(
    "/events/{event_id}/photos/{photo_id}/select",
    response_model=EventPhotoMetadata,
)
def select_event_photo(
    event_id: UUID,
    photo_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> EventPhotoMetadata:
    try:
        return EventPhotoMetadata.model_validate(service.select_event_photo(event_id, photo_id))
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)


@router.get("/events/{event_id}/photos/{photo_id}/content")
def download_event_photo(
    event_id: UUID,
    photo_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> Response:
    try:
        content = service.read_event_photo(event_id, photo_id)
    except (CardResourceNotFoundError, CardStorageError) as exc:
        _translate_domain_error(exc)
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="event-photo-{photo_id}.png"',
        },
    )


@router.put(
    "/events/{event_id}/card-design",
    response_model=CardDesignRevisionMetadata,
    status_code=status.HTTP_201_CREATED,
)
def create_card_design_revision(
    event_id: UUID,
    payload: CardDesignRevisionCreate,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardDesignRevisionMetadata:
    try:
        revision = service.create_design_revision(
            event_id,
            template_key=payload.template_key,
            template_version=payload.template_version,
            event_photo_id=payload.event_photo_id,
            photo_fit=payload.photo_fit,
            photo_focus_x=payload.photo_focus_x,
            photo_focus_y=payload.photo_focus_y,
            photo_zoom=payload.photo_zoom,
            administrator_id=administrator.id,
        )
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardInputError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return CardDesignRevisionMetadata.model_validate(revision)


@router.get(
    "/events/{event_id}/card-design",
    response_model=CardDesignRevisionMetadata,
)
def get_latest_card_design_revision(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardDesignRevisionMetadata:
    try:
        revision = service.get_latest_design_revision(event_id)
    except CardResourceNotFoundError as exc:
        _translate_domain_error(exc)
    return CardDesignRevisionMetadata.model_validate(revision)


@router.get(
    "/events/{event_id}/card-design/revisions",
    response_model=CardDesignRevisionListResponse,
)
def list_card_design_revisions(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> CardDesignRevisionListResponse:
    try:
        revisions = service.list_design_revisions(event_id, limit=limit, offset=offset)
    except CardResourceNotFoundError as exc:
        _translate_domain_error(exc)
    return CardDesignRevisionListResponse(items=revisions, limit=limit, offset=offset)


@router.post(
    "/events/{event_id}/card-design/revisions/{revision_id}/approve",
    response_model=CardDesignApprovalMetadata,
)
def approve_card_design_revision(
    event_id: UUID,
    revision_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardDesignApprovalMetadata:
    try:
        approval = service.approve_design_revision(
            event_id,
            revision_id,
            administrator_id=administrator.id,
        )
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return CardDesignApprovalMetadata.model_validate(approval)


@router.post(
    "/events/{event_id}/card-design/approvals/{approval_id}/revoke",
    response_model=CardDesignApprovalMetadata,
)
def revoke_card_design_approval(
    event_id: UUID,
    approval_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardDesignApprovalMetadata:
    try:
        approval = service.revoke_design_approval(
            event_id,
            approval_id,
            administrator_id=administrator.id,
        )
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return CardDesignApprovalMetadata.model_validate(approval)


@router.get("/events/{event_id}/card-design/preview")
def preview_card_design(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
    revision_id: UUID | None = None,
) -> Response:
    try:
        content = service.render_design_preview(event_id, revision_id)
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardInputError,
        CardStorageError,
    ) as exc:
        _translate_domain_error(exc)
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post(
    "/events/{event_id}/cards/guests/{guest_id}",
    response_model=GeneratedCardMetadata,
    status_code=status.HTTP_201_CREATED,
)
def generate_guest_card(
    event_id: UUID,
    guest_id: UUID,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> GeneratedCardMetadata:
    try:
        card = service.generate_guest_card(
            event_id,
            guest_id,
            administrator_id=administrator.id,
        )
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardInputError,
        CardStorageError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return GeneratedCardMetadata.model_validate(card)


@router.post(
    "/events/{event_id}/cards/generate",
    response_model=BulkCardGenerationResponse,
)
def generate_event_cards(
    event_id: UUID,
    payload: BulkCardGenerationRequest,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> BulkCardGenerationResponse:
    try:
        cards = service.generate_event_cards(
            event_id,
            administrator_id=administrator.id,
            guest_ids=payload.guest_ids,
            limit=payload.limit,
        )
    except (
        CardResourceNotFoundError,
        CardConflictError,
        CardInputError,
        CardStorageError,
        CardPersistenceError,
    ) as exc:
        _translate_domain_error(exc)
    return BulkCardGenerationResponse(items=cards, generated_or_current_count=len(cards))


@router.post("/events/{event_id}/cards/export")
def export_event_cards(
    event_id: UUID,
    payload: CardExportRequest,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> Response:
    try:
        content = service.export_cards(event_id, payload.card_ids)
    except (CardResourceNotFoundError, CardInputError, CardStorageError) as exc:
        _translate_domain_error(exc)
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="event-{event_id}-cards.zip"',
        },
    )


@router.get("/events/{event_id}/cards", response_model=GeneratedCardListResponse)
def list_generated_cards(
    event_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
    card_status: Annotated[GeneratedCardStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> GeneratedCardListResponse:
    try:
        cards = service.list_cards(
            event_id,
            status=card_status,
            limit=limit,
            offset=offset,
        )
    except CardResourceNotFoundError as exc:
        _translate_domain_error(exc)
    return GeneratedCardListResponse(items=cards, limit=limit, offset=offset)


@router.get(
    "/events/{event_id}/cards/{card_id}",
    response_model=GeneratedCardMetadata,
)
def get_generated_card(
    event_id: UUID,
    card_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> GeneratedCardMetadata:
    try:
        card = service.get_card(event_id, card_id)
    except CardResourceNotFoundError as exc:
        _translate_domain_error(exc)
    return GeneratedCardMetadata.model_validate(card)


@router.get("/events/{event_id}/cards/{card_id}/content")
def download_generated_card(
    event_id: UUID,
    card_id: UUID,
    _: Annotated[Administrator, Depends(get_current_administrator)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> Response:
    try:
        content = service.read_card_content(event_id, card_id)
    except (CardResourceNotFoundError, CardStorageError) as exc:
        _translate_domain_error(exc)
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="card-{card_id}.png"',
        },
    )
