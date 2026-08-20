"""Transactional M6 template, design, photo, and permanent-card workflows."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.cards.package import (
    APPROVED_PREVIEW_SHA256,
    CANVAS_SIZE,
    LOGICAL_PACKAGE_SHA256,
    MANIFEST_SHA256,
    PACKAGE_STORAGE_KEY,
    RAW_PACKAGE_SHA256,
    TEMPLATE_KEY,
    TEMPLATE_VERSION,
    TemplatePackageError,
    VerifiedTemplatePackage,
    VerifiedTemplateStorage,
)
from app.cards.photos import EventPhotoValidationError, canonicalize_event_photo
from app.cards.preview import PREVIEW_QR_SENTINEL, render_preview_qr_png
from app.cards.renderer import RENDERER_CONTRACT_VERSION, CardRenderError, ManifestRenderer
from app.cards.storage import EventPhotoStorage, GeneratedCardStorage
from app.models.cards import (
    DesignApprovalStatus,
    EventCardDesign,
    EventCardDesignApproval,
    EventCardDesignRevision,
    EventPhoto,
    GeneratedCardStatus,
    GeneratedGuestCard,
    PhotoFit,
    TemplatePreset,
    TemplatePresetVersion,
    TemplateVersionStatus,
)
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential
from app.repositories.cards import CardRepository, DesignContext
from app.services.qr import QrConflictError, QrCredentialService, QrStorageError

MAX_BULK_CARD_COUNT = 100


class CardResourceNotFoundError(LookupError):
    """Raised when an event-scoped M6 resource does not exist."""


class CardConflictError(ValueError):
    """Raised when current lifecycle state prevents an M6 operation."""


class CardInputError(ValueError):
    """Raised for bounded photo/design input validation failures."""


class CardStorageError(RuntimeError):
    """Raised when a protected M6 artifact is unavailable or corrupt."""


class CardPersistenceError(RuntimeError):
    """Raised when PostgreSQL cannot persist an M6 mutation."""


class CardService:
    """Coordinate exact template bytes, protected artifacts, and M6 persistence."""

    def __init__(
        self,
        repository: CardRepository,
        template_storage: VerifiedTemplateStorage,
        event_photo_storage: EventPhotoStorage,
        generated_card_storage: GeneratedCardStorage,
        qr_service: QrCredentialService,
        renderer: ManifestRenderer,
        *,
        maximum_event_photo_bytes: int,
    ) -> None:
        self._repository = repository
        self._template_storage = template_storage
        self._event_photo_storage = event_photo_storage
        self._generated_card_storage = generated_card_storage
        self._qr_service = qr_service
        self._renderer = renderer
        self._maximum_event_photo_bytes = maximum_event_photo_bytes

    def register_verified_template(
        self,
        *,
        administrator_id: UUID,
    ) -> tuple[TemplatePreset, TemplatePresetVersion]:
        """Register exact verified V10 identity idempotently after byte verification."""
        package = self._load_package()
        existing = self._repository.get_template(TEMPLATE_KEY, TEMPLATE_VERSION)
        if existing is not None:
            self._assert_registered_template(existing[1], package)
            return existing
        preset = self._repository.get_preset(TEMPLATE_KEY)
        if preset is None:
            preset = TemplatePreset(
                id=uuid4(),
                template_key=TEMPLATE_KEY,
                display_name="Wedding Floral",
            )
            self._repository.add(preset)
            try:
                self._repository.flush()
            except IntegrityError as exc:
                self._repository.rollback()
                raced = self._repository.get_template(TEMPLATE_KEY, TEMPLATE_VERSION)
                if raced is not None:
                    self._assert_registered_template(raced[1], package)
                    return raced
                raise CardConflictError(
                    "template registration conflicted with current state"
                ) from exc
        version = TemplatePresetVersion(
            id=uuid4(),
            preset_id=preset.id,
            version=TEMPLATE_VERSION,
            raw_package_sha256=package.raw_package_sha256,
            logical_package_sha256=package.logical_package_sha256,
            manifest_sha256=package.manifest_sha256,
            approved_preview_sha256=package.approved_preview_sha256,
            package_storage_key=PACKAGE_STORAGE_KEY,
            canvas_width=CANVAS_SIZE[0],
            canvas_height=CANVAS_SIZE[1],
            asset_count=6,
            font_count=2,
            status=TemplateVersionStatus.VERIFIED,
            verified_by=administrator_id,
        )
        self._repository.add(version)
        self._commit("template registration could not be persisted")
        return preset, version

    def get_template(
        self, template_key: str, version: int
    ) -> tuple[TemplatePreset, TemplatePresetVersion]:
        record = self._repository.get_template(template_key, version)
        if record is None:
            raise CardResourceNotFoundError("template version not found")
        return record

    def list_templates(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[tuple[TemplatePreset, TemplatePresetVersion]]:
        return self._repository.list_templates(limit=limit, offset=offset)

    def read_approved_preview(self) -> bytes:
        try:
            return self._template_storage.read_approved_preview()
        except (OSError, TemplatePackageError) as exc:
            raise CardStorageError("approved template preview is unavailable") from exc

    def upload_event_photo(
        self,
        event_id: UUID,
        content: bytes,
        *,
        administrator_id: UUID,
        select_as_primary: bool,
    ) -> EventPhoto:
        event = self._require_mutable_event(event_id)
        del event
        try:
            canonical = canonicalize_event_photo(
                content,
                maximum_bytes=self._maximum_event_photo_bytes,
            )
        except EventPhotoValidationError as exc:
            self._repository.rollback()
            raise CardInputError(str(exc)) from exc
        photo_id = uuid4()
        storage_key = EventPhotoStorage.key(event_id, photo_id)
        existing_primary = self._repository.get_primary_photo(event_id)
        make_primary = select_as_primary or existing_primary is None
        if make_primary and existing_primary is not None:
            existing_primary.is_primary = False
            try:
                self._repository.flush()
            except SQLAlchemyError as exc:
                self._repository.rollback()
                raise CardPersistenceError("event photo selection could not be persisted") from exc
        try:
            self._event_photo_storage.store(storage_key, canonical.content)
        except (OSError, ValueError) as exc:
            self._repository.rollback()
            raise CardStorageError("event photo could not be stored") from exc
        photo = EventPhoto(
            id=photo_id,
            event_id=event_id,
            storage_key=storage_key,
            sha256=canonical.sha256,
            media_type="image/png",
            byte_size=len(canonical.content),
            pixel_width=canonical.pixel_width,
            pixel_height=canonical.pixel_height,
            is_primary=make_primary,
            uploaded_by=administrator_id,
        )
        self._repository.add(photo)
        try:
            self._repository.commit()
        except SQLAlchemyError as exc:
            self._repository.rollback()
            self._delete_photo_after_failure(storage_key)
            if isinstance(exc, IntegrityError):
                raise CardConflictError("event photo conflicted with current state") from exc
            raise CardPersistenceError("event photo could not be persisted") from exc
        return photo

    def select_event_photo(self, event_id: UUID, photo_id: UUID) -> EventPhoto:
        self._require_mutable_event(event_id)
        selected = self._repository.get_photo(event_id, photo_id)
        if selected is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("event photo not found")
        current = self._repository.get_primary_photo(event_id)
        if current is not None and current.id == selected.id:
            self._repository.rollback()
            return selected
        if current is not None:
            current.is_primary = False
            self._repository.flush()
        selected.is_primary = True
        self._commit("event photo selection could not be persisted")
        return selected

    def list_event_photos(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[EventPhoto]:
        if self._repository.get_event(event_id) is None:
            raise CardResourceNotFoundError("event not found")
        return self._repository.list_photos(event_id, limit=limit, offset=offset)

    def read_event_photo(self, event_id: UUID, photo_id: UUID) -> bytes:
        photo = self._repository.get_photo(event_id, photo_id)
        if photo is None:
            raise CardResourceNotFoundError("event photo not found")
        return self._read_verified_photo(photo)

    def create_design_revision(
        self,
        event_id: UUID,
        *,
        template_key: str,
        template_version: int,
        event_photo_id: UUID,
        photo_fit: PhotoFit,
        photo_focus_x: Decimal,
        photo_focus_y: Decimal,
        photo_zoom: Decimal,
        administrator_id: UUID,
    ) -> EventCardDesignRevision:
        self._require_mutable_event(event_id)
        template = self._repository.get_template(template_key, template_version)
        if template is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("template version not found")
        photo = self._repository.get_photo(event_id, event_photo_id)
        if photo is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("event photo not found")
        if photo_fit is PhotoFit.CONTAIN and photo_zoom != Decimal("1"):
            self._repository.rollback()
            raise CardInputError("CONTAIN photo fit requires zoom 1.0")
        design = self._repository.get_design(event_id)
        if design is None:
            design = EventCardDesign(id=uuid4(), event_id=event_id, created_by=administrator_id)
            self._repository.add(design)
            self._repository.flush()
        latest = self._repository.get_latest_revision(event_id)
        revision = EventCardDesignRevision(
            id=uuid4(),
            design_id=design.id,
            event_id=event_id,
            revision_number=1 if latest is None else latest.revision_number + 1,
            template_version_id=template[1].id,
            event_photo_id=photo.id,
            photo_fit=photo_fit,
            photo_focus_x=photo_focus_x,
            photo_focus_y=photo_focus_y,
            photo_zoom=photo_zoom,
            created_by=administrator_id,
        )
        self._repository.add(revision)
        self._commit("card design revision could not be persisted")
        return revision

    def get_latest_design_revision(self, event_id: UUID) -> EventCardDesignRevision:
        if self._repository.get_event(event_id) is None:
            raise CardResourceNotFoundError("event not found")
        revision = self._repository.get_latest_revision(event_id)
        if revision is None:
            raise CardResourceNotFoundError("card design revision not found")
        return revision

    def list_design_revisions(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[EventCardDesignRevision]:
        if self._repository.get_event(event_id) is None:
            raise CardResourceNotFoundError("event not found")
        return self._repository.list_revisions(event_id, limit=limit, offset=offset)

    def approve_design_revision(
        self,
        event_id: UUID,
        revision_id: UUID,
        *,
        administrator_id: UUID,
    ) -> EventCardDesignApproval:
        self._require_mutable_event(event_id)
        revision = self._repository.get_revision(event_id, revision_id)
        if revision is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("card design revision not found")
        active = self._repository.get_active_approval(event_id)
        if active is not None and active.design_revision_id == revision.id:
            self._repository.rollback()
            return active
        now = datetime.now(UTC)
        if active is not None:
            active.status = DesignApprovalStatus.REVOKED
            active.revoked_by = administrator_id
            active.revoked_at = now
            self._repository.flush()
        approval = EventCardDesignApproval(
            id=uuid4(),
            event_id=event_id,
            design_revision_id=revision.id,
            status=DesignApprovalStatus.ACTIVE,
            approved_by=administrator_id,
            approved_at=now,
            revoked_by=None,
            revoked_at=None,
        )
        self._repository.add(approval)
        self._commit("card design approval could not be persisted")
        return approval

    def revoke_design_approval(
        self,
        event_id: UUID,
        approval_id: UUID,
        *,
        administrator_id: UUID,
    ) -> EventCardDesignApproval:
        self._require_mutable_event(event_id)
        approval = self._repository.get_approval(event_id, approval_id)
        if approval is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("card design approval not found")
        if approval.status is DesignApprovalStatus.REVOKED:
            self._repository.rollback()
            return approval
        approval.status = DesignApprovalStatus.REVOKED
        approval.revoked_by = administrator_id
        approval.revoked_at = datetime.now(UTC)
        self._commit("card design approval could not be revoked")
        return approval

    def render_design_preview(self, event_id: UUID, revision_id: UUID | None) -> bytes:
        event = self._repository.get_event(event_id)
        if event is None:
            raise CardResourceNotFoundError("event not found")
        revision = (
            self._repository.get_latest_revision(event_id)
            if revision_id is None
            else self._repository.get_revision(event_id, revision_id)
        )
        if revision is None:
            raise CardResourceNotFoundError("card design revision not found")
        template = self._template_for_revision(revision)
        photo = self._repository.get_photo(event_id, revision.event_photo_id)
        if photo is None:
            raise CardConflictError("card design photo is unavailable")
        package = self._load_package()
        self._assert_registered_template(template[1], package)
        return self._render(
            package,
            event,
            guest_name="PREVIEW GUEST",
            photo_bytes=self._read_verified_photo(photo),
            qr_bytes=render_preview_qr_png(),
            revision=revision,
        )

    def generate_guest_card(
        self,
        event_id: UUID,
        guest_id: UUID,
        *,
        administrator_id: UUID,
    ) -> GeneratedGuestCard:
        context = self._repository.get_guest_event_for_update(event_id, guest_id)
        if context is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("guest not found for event")
        guest, event = context
        self._require_generation_eligible(guest, event)
        credential = self._repository.get_active_qr(event_id, guest_id)
        if credential is None:
            self._repository.rollback()
            raise CardConflictError("guest has no active QR credential")
        design = self._repository.get_active_design_context(event_id)
        if design is None:
            self._repository.rollback()
            raise CardConflictError("event has no active approved card design")
        return self._generate_batch(
            event,
            [(guest, credential)],
            design,
            administrator_id=administrator_id,
        )[0]

    def generate_event_cards(
        self,
        event_id: UUID,
        *,
        administrator_id: UUID,
        guest_ids: Collection[UUID] | None,
        limit: int,
    ) -> list[GeneratedGuestCard]:
        event = self._repository.get_event(event_id, for_update=True)
        if event is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("event not found")
        if event.status not in {EventStatus.DRAFT, EventStatus.ACTIVE}:
            self._repository.rollback()
            raise CardConflictError(f"{event.status.value} events cannot generate cards")
        design = self._repository.get_active_design_context(event_id)
        if design is None:
            self._repository.rollback()
            raise CardConflictError("event has no active approved card design")
        contexts = self._repository.list_eligible_guest_credentials(
            event_id,
            guest_ids=guest_ids,
            limit=limit,
        )
        if not contexts:
            self._repository.rollback()
            return []
        return self._generate_batch(event, contexts, design, administrator_id=administrator_id)

    def get_card(self, event_id: UUID, card_id: UUID) -> GeneratedGuestCard:
        card = self._repository.get_card(event_id, card_id)
        if card is None:
            raise CardResourceNotFoundError("generated card not found")
        return card

    def list_cards(
        self,
        event_id: UUID,
        *,
        status: GeneratedCardStatus | None,
        limit: int,
        offset: int,
    ) -> list[GeneratedGuestCard]:
        if self._repository.get_event(event_id) is None:
            raise CardResourceNotFoundError("event not found")
        return self._repository.list_cards(
            event_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def read_card_content(self, event_id: UUID, card_id: UUID) -> bytes:
        card = self.get_card(event_id, card_id)
        return self._read_verified_card(card)

    def export_cards(self, event_id: UUID, card_ids: Sequence[UUID]) -> bytes:
        if (
            not card_ids
            or len(card_ids) > MAX_BULK_CARD_COUNT
            or len(set(card_ids)) != len(card_ids)
        ):
            raise CardInputError("card export requires 1..100 unique card IDs")
        cards = self._repository.list_cards_by_ids(event_id, card_ids)
        if len(cards) != len(card_ids):
            raise CardResourceNotFoundError("one or more generated cards were not found for event")
        output = BytesIO()
        with ZipFile(output, mode="w", compression=ZIP_STORED) as archive:
            for card in cards:
                info = ZipInfo(filename=f"card-{card.id}.png", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_STORED
                info.external_attr = 0o600 << 16
                archive.writestr(info, self._read_verified_card(card))
        return output.getvalue()

    def _generate_batch(
        self,
        event: Event,
        contexts: Sequence[tuple[Guest, GuestQrCredential]],
        design: DesignContext,
        *,
        administrator_id: UUID,
    ) -> list[GeneratedGuestCard]:
        approval, revision, template_version, preset, photo = design
        package = self._load_package()
        self._assert_registered_template(template_version, package)
        if (
            preset.template_key != TEMPLATE_KEY
            or revision.template_version_id != template_version.id
        ):
            self._repository.rollback()
            raise CardConflictError("approved design template identity is invalid")
        photo_bytes = self._read_verified_photo(photo)
        prepared: list[tuple[Guest, GuestQrCredential, bytes, str]] = []
        for guest, credential in contexts:
            self._require_generation_eligible(guest, event)
            try:
                qr_bytes = self._qr_service.read_renderable_image(credential)
            except QrConflictError as exc:
                self._repository.rollback()
                raise CardConflictError("guest QR credential is not active") from exc
            except QrStorageError as exc:
                self._repository.rollback()
                raise CardStorageError("protected QR image is unavailable") from exc
            render_input_sha256 = self._render_input_sha256(
                event,
                guest,
                credential,
                approval,
                revision,
                template_version,
                photo,
            )
            prepared.append((guest, credential, qr_bytes, render_input_sha256))
        identities = {item[3] for item in prepared}
        existing = self._repository.list_by_render_inputs(identities)
        current = self._repository.list_current_for_guests({item[0].id for item in prepared})
        now = datetime.now(UTC)
        results: list[GeneratedGuestCard] = []
        new_storage_keys: list[str] = []
        try:
            for guest, credential, qr_bytes, render_input_sha256 in prepared:
                old_current = current.get(guest.id)
                idempotent = existing.get(render_input_sha256)
                if idempotent is not None:
                    self._read_verified_card(idempotent)
                    if old_current is not None and old_current.id != idempotent.id:
                        old_current.status = GeneratedCardStatus.STALE
                        old_current.stale_at = now
                    idempotent.status = GeneratedCardStatus.CURRENT
                    idempotent.stale_at = None
                    current[guest.id] = idempotent
                    results.append(idempotent)
                    continue
                if old_current is not None:
                    old_current.status = GeneratedCardStatus.STALE
                    old_current.stale_at = now
                artifact = self._render(
                    package,
                    event,
                    guest_name=guest.full_name,
                    photo_bytes=photo_bytes,
                    qr_bytes=qr_bytes,
                    revision=revision,
                )
                if PREVIEW_QR_SENTINEL.encode() in artifact:
                    raise CardRenderError("preview QR sentinel leaked into a permanent artifact")
                card_id = uuid4()
                storage_key = GeneratedCardStorage.key(event.id, guest.id, card_id)
                self._generated_card_storage.store(storage_key, artifact)
                new_storage_keys.append(storage_key)
                card = GeneratedGuestCard(
                    id=card_id,
                    event_id=event.id,
                    guest_id=guest.id,
                    design_revision_id=revision.id,
                    approval_id=approval.id,
                    template_version_id=template_version.id,
                    qr_credential_id=credential.id,
                    render_input_sha256=render_input_sha256,
                    storage_key=storage_key,
                    artifact_sha256=hashlib.sha256(artifact).hexdigest(),
                    media_type="image/png",
                    byte_size=len(artifact),
                    pixel_width=CANVAS_SIZE[0],
                    pixel_height=CANVAS_SIZE[1],
                    status=GeneratedCardStatus.CURRENT,
                    generated_by=administrator_id,
                    generated_at=now,
                    stale_at=None,
                )
                self._repository.add(card)
                current[guest.id] = card
                results.append(card)
            self._repository.commit()
        except (CardInputError, CardStorageError):
            self._repository.rollback()
            self._cleanup_new_cards(new_storage_keys)
            raise
        except (CardRenderError, OSError, ValueError) as exc:
            self._repository.rollback()
            self._cleanup_new_cards(new_storage_keys)
            if isinstance(exc, CardRenderError):
                raise CardInputError("card render inputs do not fit the approved design") from exc
            raise CardStorageError("generated card artifact could not be stored") from exc
        except IntegrityError as exc:
            self._repository.rollback()
            self._cleanup_new_cards(new_storage_keys)
            raise CardConflictError("card generation conflicted with current state") from exc
        except SQLAlchemyError as exc:
            self._repository.rollback()
            self._cleanup_new_cards(new_storage_keys)
            raise CardPersistenceError("generated card could not be persisted") from exc
        return results

    def _render(
        self,
        package: VerifiedTemplatePackage,
        event: Event,
        *,
        guest_name: str,
        photo_bytes: bytes,
        qr_bytes: bytes,
        revision: EventCardDesignRevision,
    ) -> bytes:
        placeholders = {
            "event.address": event.address or "",
            "event.couple_names": [event.bride_name, event.groom_name],
            "event.date": event.event_datetime,
            "event.primary_photo": photo_bytes,
            "event.time": event.event_datetime,
            "event.venue": event.venue,
            "guest.full_name": guest_name,
            "system.qr_image": qr_bytes,
        }
        try:
            return self._renderer.render(
                package,
                placeholders,
                photo_fit=revision.photo_fit.value,
                photo_focus_x=float(revision.photo_focus_x),
                photo_focus_y=float(revision.photo_focus_y),
                photo_zoom=float(revision.photo_zoom),
            )
        except (CardRenderError, TemplatePackageError) as exc:
            raise CardInputError("card render inputs do not fit the approved design") from exc

    def _render_input_sha256(
        self,
        event: Event,
        guest: Guest,
        credential: GuestQrCredential,
        approval: EventCardDesignApproval,
        revision: EventCardDesignRevision,
        template_version: TemplatePresetVersion,
        photo: EventPhoto,
    ) -> str:
        identity = {
            "approval_id": str(approval.id),
            "design_revision_id": str(revision.id),
            "event": {
                "address": event.address or "",
                "bride_name": event.bride_name,
                "datetime": event.event_datetime.isoformat(),
                "groom_name": event.groom_name,
                "id": str(event.id),
                "venue": event.venue,
            },
            "guest": {"full_name": guest.full_name, "id": str(guest.id)},
            "photo": {
                "fit": revision.photo_fit.value,
                "focus_x": str(revision.photo_focus_x),
                "focus_y": str(revision.photo_focus_y),
                "id": str(photo.id),
                "sha256": photo.sha256,
                "zoom": str(revision.photo_zoom),
            },
            "qr": {"id": str(credential.id), "image_sha256": credential.image_sha256},
            "renderer_contract": RENDERER_CONTRACT_VERSION,
            "template": {
                "id": str(template_version.id),
                "logical_sha256": template_version.logical_package_sha256,
                "manifest_sha256": template_version.manifest_sha256,
                "raw_sha256": template_version.raw_package_sha256,
                "version": template_version.version,
            },
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest()

    def _template_for_revision(
        self,
        revision: EventCardDesignRevision,
    ) -> tuple[TemplatePreset, TemplatePresetVersion]:
        template = self._repository.get_template(TEMPLATE_KEY, TEMPLATE_VERSION)
        if template is None or template[1].id != revision.template_version_id:
            raise CardConflictError("card design template is unavailable")
        return template

    def _load_package(self) -> VerifiedTemplatePackage:
        try:
            return self._template_storage.load()
        except (OSError, TemplatePackageError) as exc:
            raise CardStorageError("verified Wedding Floral V10 package is unavailable") from exc

    def _assert_registered_template(
        self,
        version: TemplatePresetVersion,
        package: VerifiedTemplatePackage,
    ) -> None:
        observed = (
            version.version,
            version.raw_package_sha256,
            version.logical_package_sha256,
            version.manifest_sha256,
            version.approved_preview_sha256,
            version.package_storage_key,
            version.canvas_width,
            version.canvas_height,
            version.asset_count,
            version.font_count,
            version.status,
        )
        expected = (
            TEMPLATE_VERSION,
            RAW_PACKAGE_SHA256,
            LOGICAL_PACKAGE_SHA256,
            MANIFEST_SHA256,
            APPROVED_PREVIEW_SHA256,
            PACKAGE_STORAGE_KEY,
            CANVAS_SIZE[0],
            CANVAS_SIZE[1],
            6,
            2,
            TemplateVersionStatus.VERIFIED,
        )
        if observed != expected or package.logical_package_sha256 != LOGICAL_PACKAGE_SHA256:
            raise CardConflictError("registered template does not match the verified V10 package")

    def _require_mutable_event(self, event_id: UUID) -> Event:
        event = self._repository.get_event(event_id, for_update=True)
        if event is None:
            self._repository.rollback()
            raise CardResourceNotFoundError("event not found")
        if event.status not in {EventStatus.DRAFT, EventStatus.ACTIVE}:
            self._repository.rollback()
            raise CardConflictError(f"{event.status.value} events cannot change card design")
        return event

    def _require_generation_eligible(self, guest: Guest, event: Event) -> None:
        if guest.event_id != event.id:
            raise CardResourceNotFoundError("guest not found for event")
        if guest.status is not GuestStatus.ACTIVE:
            raise CardConflictError("inactive guests cannot receive current cards")
        if event.status not in {EventStatus.DRAFT, EventStatus.ACTIVE}:
            raise CardConflictError(f"{event.status.value} events cannot generate cards")

    def _read_verified_photo(self, photo: EventPhoto) -> bytes:
        try:
            content = self._event_photo_storage.read(photo.storage_key)
        except (OSError, ValueError) as exc:
            raise CardStorageError("event photo is unavailable") from exc
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), photo.sha256):
            raise CardStorageError("event photo failed its integrity check")
        return content

    def _read_verified_card(self, card: GeneratedGuestCard) -> bytes:
        try:
            content = self._generated_card_storage.read(card.storage_key)
        except (OSError, ValueError) as exc:
            raise CardStorageError("generated card artifact is unavailable") from exc
        actual = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual, card.artifact_sha256):
            raise CardStorageError("generated card artifact failed its integrity check")
        return content

    def _commit(self, message: str) -> None:
        try:
            self._repository.commit()
        except IntegrityError as exc:
            self._repository.rollback()
            raise CardConflictError(message) from exc
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise CardPersistenceError(message) from exc

    def _delete_photo_after_failure(self, storage_key: str) -> None:
        try:
            self._event_photo_storage.delete(storage_key)
        except (OSError, ValueError) as exc:
            raise CardStorageError("failed event-photo transaction cleanup") from exc

    def _cleanup_new_cards(self, storage_keys: Sequence[str]) -> None:
        cleanup_error: Exception | None = None
        for storage_key in storage_keys:
            try:
                self._generated_card_storage.delete(storage_key)
            except (OSError, ValueError) as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise CardStorageError(
                "failed card transaction cleanup left an orphan risk"
            ) from cleanup_error
