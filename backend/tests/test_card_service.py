"""M6 service lifecycle, M5 image bridge, idempotency, and rollback tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
import zxingcpp
from PIL import Image
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.cards.package import VerifiedTemplateStorage
from app.cards.renderer import ManifestRenderer
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
)
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.images import render_qr_png
from app.services.cards import (
    CardConflictError,
    CardInputError,
    CardPersistenceError,
    CardResourceNotFoundError,
    CardService,
    CardStorageError,
)
from app.services.qr import QrConflictError

PRIVATE_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "storage" / "private" / "templates"
PRIVATE_PACKAGE = (
    PRIVATE_TEMPLATE_ROOT / "wedding_floral" / "v10" / "wedding_floral_v10.template-package.zip"
)
pytestmark = pytest.mark.skipif(
    not PRIVATE_PACKAGE.is_file(),
    reason="exact ignored Wedding Floral V10 recovery is not present",
)


class FakeCardRepository:
    """Small transactional state double exposing the real repository contract."""

    def __init__(self) -> None:
        self.presets: dict[UUID, TemplatePreset] = {}
        self.versions: dict[UUID, TemplatePresetVersion] = {}
        self.events: dict[UUID, Event] = {}
        self.photos: dict[UUID, EventPhoto] = {}
        self.designs: dict[UUID, EventCardDesign] = {}
        self.revisions: dict[UUID, EventCardDesignRevision] = {}
        self.approvals: dict[UUID, EventCardDesignApproval] = {}
        self.guests: dict[UUID, Guest] = {}
        self.credentials: dict[UUID, GuestQrCredential] = {}
        self.cards: dict[UUID, GeneratedGuestCard] = {}
        self.fail_next_commit = False
        self.next_commit_error: SQLAlchemyError | None = None
        self.next_flush_error: SQLAlchemyError | None = None
        self.rollback_count = 0
        self.bulk_context_queries = 0
        self.design_context_queries = 0

    def get_template(self, template_key: str, version: int):
        for template_version in self.versions.values():
            preset = self.presets[template_version.preset_id]
            if preset.template_key == template_key and template_version.version == version:
                return preset, template_version
        return None

    def get_preset(self, template_key: str):
        return next(
            (preset for preset in self.presets.values() if preset.template_key == template_key),
            None,
        )

    def list_templates(self, *, limit: int, offset: int):
        records = [(self.presets[version.preset_id], version) for version in self.versions.values()]
        return records[offset : offset + limit]

    def get_event(self, event_id: UUID, *, for_update: bool = False):
        del for_update
        return self.events.get(event_id)

    def list_photos(self, event_id: UUID, *, limit: int, offset: int):
        records = [photo for photo in self.photos.values() if photo.event_id == event_id]
        return records[offset : offset + limit]

    def get_photo(self, event_id: UUID, photo_id: UUID):
        photo = self.photos.get(photo_id)
        return photo if photo is not None and photo.event_id == event_id else None

    def get_primary_photo(self, event_id: UUID):
        return next(
            (
                photo
                for photo in self.photos.values()
                if photo.event_id == event_id and photo.is_primary
            ),
            None,
        )

    def get_design(self, event_id: UUID):
        return next(
            (design for design in self.designs.values() if design.event_id == event_id),
            None,
        )

    def get_latest_revision(self, event_id: UUID):
        revisions = [
            revision for revision in self.revisions.values() if revision.event_id == event_id
        ]
        return max(revisions, key=lambda item: item.revision_number, default=None)

    def get_revision(self, event_id: UUID, revision_id: UUID):
        revision = self.revisions.get(revision_id)
        return revision if revision is not None and revision.event_id == event_id else None

    def list_revisions(self, event_id: UUID, *, limit: int, offset: int):
        records = sorted(
            (revision for revision in self.revisions.values() if revision.event_id == event_id),
            key=lambda item: item.revision_number,
            reverse=True,
        )
        return records[offset : offset + limit]

    def get_active_approval(self, event_id: UUID):
        return next(
            (
                approval
                for approval in self.approvals.values()
                if approval.event_id == event_id and approval.status is DesignApprovalStatus.ACTIVE
            ),
            None,
        )

    def get_approval(self, event_id: UUID, approval_id: UUID):
        approval = self.approvals.get(approval_id)
        return approval if approval is not None and approval.event_id == event_id else None

    def get_active_design_context(self, event_id: UUID):
        self.design_context_queries += 1
        approval = self.get_active_approval(event_id)
        if approval is None:
            return None
        revision = self.revisions[approval.design_revision_id]
        version = self.versions[revision.template_version_id]
        preset = self.presets[version.preset_id]
        photo = self.photos[revision.event_photo_id]
        return approval, revision, version, preset, photo

    def get_guest_event_for_update(self, event_id: UUID, guest_id: UUID):
        guest = self.guests.get(guest_id)
        event = self.events.get(event_id)
        if guest is None or event is None or guest.event_id != event_id:
            return None
        return guest, event

    def get_active_qr(self, event_id: UUID, guest_id: UUID):
        credential = self.credentials.get(guest_id)
        if (
            credential is None
            or credential.event_id != event_id
            or credential.status is not QrCredentialStatus.ACTIVE
        ):
            return None
        return credential

    def list_eligible_guest_credentials(
        self,
        event_id: UUID,
        *,
        guest_ids: set[UUID] | list[UUID] | None,
        limit: int,
    ):
        self.bulk_context_queries += 1
        requested = None if guest_ids is None else set(guest_ids)
        records = []
        for guest in sorted(self.guests.values(), key=lambda item: (item.full_name, item.id)):
            credential = self.get_active_qr(event_id, guest.id)
            if (
                guest.event_id == event_id
                and guest.status is GuestStatus.ACTIVE
                and credential is not None
                and (requested is None or guest.id in requested)
            ):
                records.append((guest, credential))
        return records[:limit]

    def list_by_render_inputs(self, identities: set[str]):
        return {
            card.render_input_sha256: card
            for card in self.cards.values()
            if card.render_input_sha256 in identities
        }

    def list_current_for_guests(self, guest_ids: set[UUID]):
        return {
            card.guest_id: card
            for card in self.cards.values()
            if card.guest_id in guest_ids and card.status is GeneratedCardStatus.CURRENT
        }

    def get_card(self, event_id: UUID, card_id: UUID):
        card = self.cards.get(card_id)
        return card if card is not None and card.event_id == event_id else None

    def list_cards(self, event_id: UUID, *, status, limit: int, offset: int):
        records = [card for card in self.cards.values() if card.event_id == event_id]
        if status is not None:
            records = [card for card in records if card.status is status]
        return records[offset : offset + limit]

    def list_cards_by_ids(self, event_id: UUID, card_ids: list[UUID]):
        return [
            card for card_id in card_ids if (card := self.get_card(event_id, card_id)) is not None
        ]

    def add(self, *records: object) -> None:
        for record in records:
            if isinstance(record, TemplatePreset):
                self.presets[record.id] = record
            elif isinstance(record, TemplatePresetVersion):
                record.verified_at = datetime.now(UTC)
                self.versions[record.id] = record
            elif isinstance(record, EventPhoto):
                now = datetime.now(UTC)
                record.created_at = now
                record.updated_at = now
                self.photos[record.id] = record
            elif isinstance(record, EventCardDesign):
                self.designs[record.id] = record
            elif isinstance(record, EventCardDesignRevision):
                self.revisions[record.id] = record
            elif isinstance(record, EventCardDesignApproval):
                self.approvals[record.id] = record
            elif isinstance(record, GeneratedGuestCard):
                self.cards[record.id] = record
            else:  # pragma: no cover - protects test-double drift
                raise AssertionError(f"unexpected record type {type(record)}")

    def flush(self) -> None:
        if self.next_flush_error is not None:
            error = self.next_flush_error
            self.next_flush_error = None
            raise error

    def commit(self) -> None:
        if self.next_commit_error is not None:
            error = self.next_commit_error
            self.next_commit_error = None
            raise error
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise SQLAlchemyError("synthetic persistence failure")

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeQrImageBridge:
    def __init__(self) -> None:
        self.images: dict[UUID, bytes] = {}
        self.read_ids: list[UUID] = []

    def read_renderable_image(self, credential: GuestQrCredential) -> bytes:
        if credential.status is not QrCredentialStatus.ACTIVE:
            raise QrConflictError("only active QR credentials may be rendered")
        self.read_ids.append(credential.id)
        return self.images[credential.id]


def _image_bytes(color: str = "#b8c7a2") -> bytes:
    image = Image.new("RGB", (900, 700), color)
    output = BytesIO()
    image.save(output, format="JPEG", quality=92)
    return output.getvalue()


def _event(administrator_id: UUID) -> Event:
    return Event(
        id=uuid4(),
        event_name="Synthetic M6 Wedding",
        bride_name="Jordan",
        groom_name="Jessica",
        event_datetime=datetime(2027, 6, 12, 14, 30, tzinfo=UTC),
        venue="Synthetic Hall",
        address="123 Synthetic Road",
        description=None,
        status=EventStatus.ACTIVE,
        max_companions_per_invitation=2,
        created_by=administrator_id,
    )


def _guest(event_id: UUID, name: str = "Synthetic Guest") -> Guest:
    return Guest(
        id=uuid4(),
        event_id=event_id,
        full_name=name,
        phone_number=None,
        email=None,
        invitation_type="Family",
        allowed_companions=2,
        table_number=None,
        category=None,
        status=GuestStatus.ACTIVE,
    )


def _credential(event_id: UUID, guest_id: UUID, actor_id: UUID, payload: str):
    credential_id = uuid4()
    image = render_qr_png(payload)
    return (
        GuestQrCredential(
            id=credential_id,
            event_id=event_id,
            guest_id=guest_id,
            token_hash=hashlib.sha256(payload.encode()).hexdigest(),
            image_storage_key=f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png",
            image_sha256=hashlib.sha256(image).hexdigest(),
            status=QrCredentialStatus.ACTIVE,
            replaces_credential_id=None,
            issued_by=actor_id,
            issued_at=datetime.now(UTC),
            deactivated_at=None,
            superseded_at=None,
        ),
        image,
    )


def _service(
    tmp_path: Path,
    repository: FakeCardRepository,
    bridge: FakeQrImageBridge,
) -> CardService:
    return CardService(
        repository,  # type: ignore[arg-type]
        VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT),
        EventPhotoStorage(tmp_path / "event-photos"),
        GeneratedCardStorage(tmp_path / "generated-cards"),
        bridge,  # type: ignore[arg-type]
        ManifestRenderer(),
        maximum_event_photo_bytes=2_000_000,
    )


def _prepare_approved_design(
    service: CardService,
    repository: FakeCardRepository,
    event: Event,
    actor_id: UUID,
) -> tuple[EventPhoto, EventCardDesignRevision, EventCardDesignApproval]:
    service.register_verified_template(administrator_id=actor_id)
    photo = service.upload_event_photo(
        event.id,
        _image_bytes(),
        administrator_id=actor_id,
        select_as_primary=True,
    )
    revision = service.create_design_revision(
        event.id,
        template_key="wedding_floral",
        template_version=10,
        event_photo_id=photo.id,
        photo_fit=PhotoFit.COVER,
        photo_focus_x=0.5,
        photo_focus_y=0.5,
        photo_zoom=1.0,
        administrator_id=actor_id,
    )
    approval = service.approve_design_revision(
        event.id,
        revision.id,
        administrator_id=actor_id,
    )
    assert repository.get_active_approval(event.id) is approval
    return photo, revision, approval


def test_permanent_generation_uses_protected_qr_is_idempotent_and_regenerates(
    tmp_path: Path,
) -> None:
    actor_id = uuid4()
    repository = FakeCardRepository()
    bridge = FakeQrImageBridge()
    service = _service(tmp_path, repository, bridge)
    event = _event(actor_id)
    guest = _guest(event.id)
    repository.events[event.id] = event
    repository.guests[guest.id] = guest
    photo, revision, approval = _prepare_approved_design(service, repository, event, actor_id)
    credential, qr_image = _credential(event.id, guest.id, actor_id, "A" * 32)
    repository.credentials[guest.id] = credential
    bridge.images[credential.id] = qr_image

    first = service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)
    retry = service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)
    content = service.read_card_content(event.id, first.id)

    assert retry.id == first.id
    assert len(repository.cards) == 1
    assert first.status is GeneratedCardStatus.CURRENT
    assert first.qr_credential_id == credential.id
    assert first.design_revision_id == revision.id
    assert first.approval_id == approval.id
    assert first.template_version_id == revision.template_version_id
    assert first.artifact_sha256 == hashlib.sha256(content).hexdigest()
    assert photo.storage_key not in repr(first.__dict__)
    with Image.open(BytesIO(content)) as image:
        decoded = zxingcpp.read_barcodes(image.crop((80, 87, 278, 285)))
        assert [result.text for result in decoded] == ["A" * 32]
    assert b"PREVIEW-QR-DO-NOT-SCAN" not in content
    assert bridge.read_ids == [credential.id, credential.id]

    credential.status = QrCredentialStatus.SUPERSEDED
    credential.superseded_at = datetime.now(UTC)
    replacement, replacement_image = _credential(event.id, guest.id, actor_id, "B" * 32)
    replacement.replaces_credential_id = credential.id
    repository.credentials[guest.id] = replacement
    bridge.images[replacement.id] = replacement_image
    regenerated = service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)

    assert regenerated.id != first.id
    assert regenerated.qr_credential_id == replacement.id
    assert first.status is GeneratedCardStatus.STALE
    assert first.stale_at is not None
    assert regenerated.status is GeneratedCardStatus.CURRENT
    with Image.open(BytesIO(service.read_card_content(event.id, regenerated.id))) as image:
        assert [
            result.text for result in zxingcpp.read_barcodes(image.crop((80, 87, 278, 285)))
        ] == ["B" * 32]


def test_preview_photo_design_approval_bulk_and_private_export(tmp_path: Path) -> None:
    actor_id = uuid4()
    repository = FakeCardRepository()
    bridge = FakeQrImageBridge()
    service = _service(tmp_path, repository, bridge)
    event = _event(actor_id)
    repository.events[event.id] = event
    photo, revision, approval = _prepare_approved_design(service, repository, event, actor_id)

    assert service.register_verified_template(administrator_id=actor_id)[1].version == 10
    assert service.get_template("wedding_floral", 10)[1].logical_package_sha256.startswith("7544")
    assert len(service.list_templates(limit=10, offset=0)) == 1
    assert service.list_event_photos(event.id, limit=10, offset=0) == [photo]
    assert service.read_event_photo(event.id, photo.id).startswith(b"\x89PNG")
    assert service.get_latest_design_revision(event.id) is revision
    assert service.list_design_revisions(event.id, limit=10, offset=0) == [revision]
    assert (
        service.approve_design_revision(event.id, revision.id, administrator_id=actor_id)
        is approval
    )

    preview = service.render_design_preview(event.id, revision.id)
    with Image.open(BytesIO(preview)) as image:
        assert [
            result.text for result in zxingcpp.read_barcodes(image.crop((80, 87, 278, 285)))
        ] == ["PREVIEW-QR-DO-NOT-SCAN"]
    assert hashlib.sha256(service.read_approved_preview()).hexdigest().startswith("1f7e8a38")

    guests = [_guest(event.id, "Synthetic Guest One"), _guest(event.id, "Synthetic Guest Two")]
    for index, guest in enumerate(guests):
        repository.guests[guest.id] = guest
        payload = chr(ord("C") + index) * 32
        credential, image = _credential(event.id, guest.id, actor_id, payload)
        repository.credentials[guest.id] = credential
        bridge.images[credential.id] = image
    cards = service.generate_event_cards(
        event.id,
        administrator_id=actor_id,
        guest_ids=[guest.id for guest in guests],
        limit=100,
    )

    assert len(cards) == 2
    assert repository.bulk_context_queries == 1
    assert repository.design_context_queries >= 1
    assert (
        service.list_cards(
            event.id,
            status=GeneratedCardStatus.CURRENT,
            limit=100,
            offset=0,
        )
        == cards
    )
    export = service.export_cards(event.id, [card.id for card in cards])
    with ZipFile(BytesIO(export)) as archive:
        assert archive.namelist() == [f"card-{card.id}.png" for card in cards]
        assert all("Synthetic" not in name for name in archive.namelist())

    revoked = service.revoke_design_approval(event.id, approval.id, administrator_id=actor_id)
    assert revoked.status is DesignApprovalStatus.REVOKED
    assert revoked.revoked_by == actor_id
    assert (
        service.revoke_design_approval(event.id, approval.id, administrator_id=actor_id) is revoked
    )


def test_missing_inactive_superseded_and_cross_event_inputs_are_rejected(tmp_path: Path) -> None:
    actor_id = uuid4()
    repository = FakeCardRepository()
    bridge = FakeQrImageBridge()
    service = _service(tmp_path, repository, bridge)
    event = _event(actor_id)
    guest = _guest(event.id)
    repository.events[event.id] = event
    repository.guests[guest.id] = guest
    _prepare_approved_design(service, repository, event, actor_id)

    with pytest.raises(CardConflictError, match="no active QR"):
        service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)
    credential, image = _credential(event.id, guest.id, actor_id, "E" * 32)
    bridge.images[credential.id] = image
    repository.credentials[guest.id] = credential
    for qr_status in (QrCredentialStatus.INACTIVE, QrCredentialStatus.SUPERSEDED):
        credential.status = qr_status
        with pytest.raises(CardConflictError, match="no active QR"):
            service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)

    second_event = _event(actor_id)
    repository.events[second_event.id] = second_event
    with pytest.raises(CardResourceNotFoundError, match="guest not found for event"):
        service.generate_guest_card(second_event.id, guest.id, administrator_id=actor_id)
    with pytest.raises(CardResourceNotFoundError, match="generated card not found"):
        service.get_card(second_event.id, uuid4())


def test_database_failure_cleans_new_card_and_photo_artifacts(tmp_path: Path) -> None:
    actor_id = uuid4()
    repository = FakeCardRepository()
    bridge = FakeQrImageBridge()
    service = _service(tmp_path, repository, bridge)
    event = _event(actor_id)
    guest = _guest(event.id)
    repository.events[event.id] = event
    repository.guests[guest.id] = guest
    _prepare_approved_design(service, repository, event, actor_id)
    credential, image = _credential(event.id, guest.id, actor_id, "F" * 32)
    repository.credentials[guest.id] = credential
    bridge.images[credential.id] = image
    repository.fail_next_commit = True

    with pytest.raises(CardPersistenceError, match="could not be persisted"):
        service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)
    assert not list((tmp_path / "generated-cards").rglob("*.png"))

    second_repository = FakeCardRepository()
    second_repository.events[event.id] = event
    second_service = _service(tmp_path / "second", second_repository, bridge)
    second_repository.fail_next_commit = True
    with pytest.raises(CardPersistenceError, match="event photo could not be persisted"):
        second_service.upload_event_photo(
            event.id,
            _image_bytes(),
            administrator_id=actor_id,
            select_as_primary=True,
        )
    assert not list((tmp_path / "second" / "event-photos").rglob("*.png"))


def test_bounded_template_photo_and_design_conflicts_are_explicit(tmp_path: Path) -> None:
    actor_id = uuid4()
    repository = FakeCardRepository()
    bridge = FakeQrImageBridge()
    service = _service(tmp_path, repository, bridge)
    event = _event(actor_id)
    repository.events[event.id] = event

    with pytest.raises(CardResourceNotFoundError, match="template version not found"):
        service.get_template("missing", 1)
    with pytest.raises(CardResourceNotFoundError, match="event not found"):
        service.upload_event_photo(
            uuid4(),
            _image_bytes(),
            administrator_id=actor_id,
            select_as_primary=True,
        )
    with pytest.raises(CardInputError, match="event photo is not a safe readable image"):
        service.upload_event_photo(
            event.id,
            b"not-an-image",
            administrator_id=actor_id,
            select_as_primary=True,
        )

    service.register_verified_template(administrator_id=actor_id)
    first = service.upload_event_photo(
        event.id,
        _image_bytes("#ffffff"),
        administrator_id=actor_id,
        select_as_primary=True,
    )
    second = service.upload_event_photo(
        event.id,
        _image_bytes("#4f6f52"),
        administrator_id=actor_id,
        select_as_primary=True,
    )
    assert not first.is_primary
    assert second.is_primary
    assert service.select_event_photo(event.id, second.id) is second
    assert service.select_event_photo(event.id, first.id) is first
    assert first.is_primary
    assert not second.is_primary

    with pytest.raises(CardResourceNotFoundError, match="event photo not found"):
        service.select_event_photo(event.id, uuid4())
    with pytest.raises(CardResourceNotFoundError, match="event not found"):
        service.list_event_photos(uuid4(), limit=10, offset=0)
    with pytest.raises(CardResourceNotFoundError, match="event photo not found"):
        service.read_event_photo(event.id, uuid4())
    with pytest.raises(CardResourceNotFoundError, match="template version not found"):
        service.create_design_revision(
            event.id,
            template_key="missing",
            template_version=9,
            event_photo_id=first.id,
            photo_fit=PhotoFit.COVER,
            photo_focus_x=0.5,
            photo_focus_y=0.5,
            photo_zoom=1.0,
            administrator_id=actor_id,
        )
    with pytest.raises(CardResourceNotFoundError, match="event photo not found"):
        service.create_design_revision(
            event.id,
            template_key="wedding_floral",
            template_version=10,
            event_photo_id=uuid4(),
            photo_fit=PhotoFit.COVER,
            photo_focus_x=0.5,
            photo_focus_y=0.5,
            photo_zoom=1.0,
            administrator_id=actor_id,
        )
    with pytest.raises(CardInputError, match=r"CONTAIN photo fit requires zoom 1\.0"):
        service.create_design_revision(
            event.id,
            template_key="wedding_floral",
            template_version=10,
            event_photo_id=first.id,
            photo_fit=PhotoFit.CONTAIN,
            photo_focus_x=0.5,
            photo_focus_y=0.5,
            photo_zoom=1.1,
            administrator_id=actor_id,
        )

    empty_event = _event(actor_id)
    repository.events[empty_event.id] = empty_event
    with pytest.raises(CardResourceNotFoundError, match="card design revision not found"):
        service.get_latest_design_revision(empty_event.id)
    with pytest.raises(CardResourceNotFoundError, match="event not found"):
        service.get_latest_design_revision(uuid4())
    with pytest.raises(CardResourceNotFoundError, match="event not found"):
        service.list_design_revisions(uuid4(), limit=10, offset=0)
    with pytest.raises(CardResourceNotFoundError, match="card design revision not found"):
        service.approve_design_revision(event.id, uuid4(), administrator_id=actor_id)
    with pytest.raises(CardResourceNotFoundError, match="card design approval not found"):
        service.revoke_design_approval(event.id, uuid4(), administrator_id=actor_id)
    with pytest.raises(CardResourceNotFoundError, match="event not found"):
        service.render_design_preview(uuid4(), None)
    with pytest.raises(CardResourceNotFoundError, match="card design revision not found"):
        service.render_design_preview(empty_event.id, None)


def test_generation_bulk_storage_integrity_and_terminal_event_errors(tmp_path: Path) -> None:
    actor_id = uuid4()
    repository = FakeCardRepository()
    bridge = FakeQrImageBridge()
    service = _service(tmp_path, repository, bridge)
    event = _event(actor_id)
    guest = _guest(event.id)
    repository.events[event.id] = event
    repository.guests[guest.id] = guest
    photo, revision, approval = _prepare_approved_design(service, repository, event, actor_id)
    credential, image = _credential(event.id, guest.id, actor_id, "G" * 32)
    repository.credentials[guest.id] = credential
    bridge.images[credential.id] = image

    approval.status = DesignApprovalStatus.REVOKED
    with pytest.raises(CardConflictError, match="no active approved"):
        service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)
    approval.status = DesignApprovalStatus.ACTIVE
    card = service.generate_guest_card(event.id, guest.id, administrator_id=actor_id)

    card_path = tmp_path / "generated-cards" / Path(card.storage_key)
    card_path.write_bytes(b"corrupt")
    with pytest.raises(CardStorageError, match="failed its integrity check"):
        service.read_card_content(event.id, card.id)
    card_path.unlink()
    with pytest.raises(CardStorageError, match="artifact is unavailable"):
        service.read_card_content(event.id, card.id)

    photo_path = tmp_path / "event-photos" / Path(photo.storage_key)
    photo_path.write_bytes(b"corrupt")
    with pytest.raises(CardStorageError, match="failed its integrity check"):
        service.read_event_photo(event.id, photo.id)

    terminal = _event(actor_id)
    terminal.status = EventStatus.COMPLETED
    repository.events[terminal.id] = terminal
    with pytest.raises(CardConflictError, match="cannot generate cards"):
        service.generate_event_cards(
            terminal.id,
            administrator_id=actor_id,
            guest_ids=None,
            limit=100,
        )
    with pytest.raises(CardResourceNotFoundError, match="event not found"):
        service.generate_event_cards(
            uuid4(),
            administrator_id=actor_id,
            guest_ids=None,
            limit=100,
        )
    approval.status = DesignApprovalStatus.REVOKED
    with pytest.raises(CardConflictError, match="no active approved"):
        service.generate_event_cards(
            event.id,
            administrator_id=actor_id,
            guest_ids=None,
            limit=100,
        )
    approval.status = DesignApprovalStatus.ACTIVE
    repository.guests.clear()
    assert (
        service.generate_event_cards(
            event.id,
            administrator_id=actor_id,
            guest_ids=None,
            limit=100,
        )
        == []
    )
    with pytest.raises(CardResourceNotFoundError, match="event not found"):
        service.list_cards(uuid4(), status=None, limit=10, offset=0)
    with pytest.raises(CardInputError, match=r"1\.\.100 unique"):
        service.export_cards(event.id, [])
    with pytest.raises(CardResourceNotFoundError, match="one or more"):
        service.export_cards(event.id, [uuid4()])

    repository.approvals[approval.id] = approval
    repository.photos.pop(photo.id)
    with pytest.raises(CardConflictError, match="photo is unavailable"):
        service.render_design_preview(event.id, revision.id)


def test_registration_and_commit_integrity_conflicts_fail_closed(tmp_path: Path) -> None:
    actor_id = uuid4()
    repository = FakeCardRepository()
    bridge = FakeQrImageBridge()
    service = _service(tmp_path, repository, bridge)
    event = _event(actor_id)
    repository.events[event.id] = event

    repository.next_flush_error = IntegrityError("synthetic", {}, Exception())
    with pytest.raises(CardConflictError, match="registration conflicted"):
        service.register_verified_template(administrator_id=actor_id)

    repository = FakeCardRepository()
    repository.events[event.id] = event
    service = _service(tmp_path / "commit", repository, bridge)
    repository.next_commit_error = IntegrityError("synthetic", {}, Exception())
    with pytest.raises(CardConflictError, match="registration could not be persisted"):
        service.register_verified_template(administrator_id=actor_id)
