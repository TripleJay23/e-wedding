"""M5 lifecycle, rollback, eligibility, and render-boundary service tests."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import zxingcpp
from PIL import Image
from sqlalchemy.exc import IntegrityError

from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import hash_qr_credential
from app.qr.storage import QrImageStorage
from app.services.qr import (
    QrConflictError,
    QrCredentialService,
    QrResourceNotFoundError,
    QrStorageError,
)


class FakeQrRepository:
    def __init__(self, guest: Guest | None, event: Event | None) -> None:
        self.guest = guest
        self.event = event
        self.records: list[GuestQrCredential] = []
        self.pending: list[GuestQrCredential] = []
        self.snapshots: list[
            tuple[
                GuestQrCredential,
                QrCredentialStatus,
                datetime | None,
                datetime | None,
            ]
        ] = []
        self.commit_error: Exception | None = None
        self.flush_error: Exception | None = None
        self.rollback_count = 0

    def get_guest_context_for_update(self, guest_id: UUID):
        if self.guest is None or self.event is None or self.guest.id != guest_id:
            return None
        self.snapshots = [
            (record, record.status, record.deactivated_at, record.superseded_at)
            for record in self.records
        ]
        return self.guest, self.event

    def get_latest_for_guest(self, guest_id: UUID):
        matches = [record for record in self.records if record.guest_id == guest_id]
        return max(matches, key=lambda item: (item.issued_at, item.id.hex), default=None)

    def get_event(self, event_id: UUID):
        return self.event if self.event is not None and self.event.id == event_id else None

    def list_for_event(self, event_id: UUID, *, limit: int, offset: int):
        matches = [record for record in reversed(self.records) if record.event_id == event_id]
        return matches[offset : offset + limit]

    def add(self, credential: GuestQrCredential) -> None:
        self.pending.append(credential)

    def flush(self) -> None:
        if self.flush_error is not None:
            raise self.flush_error

    def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        now = datetime.now(UTC)
        for record in self.pending:
            record.created_at = now
            record.updated_at = now
        for record in self.records:
            record.updated_at = now
        self.records.extend(self.pending)
        self.pending = []
        self.snapshots = []

    def rollback(self) -> None:
        for record, status, deactivated_at, superseded_at in self.snapshots:
            record.status = status
            record.deactivated_at = deactivated_at
            record.superseded_at = superseded_at
        self.pending = []
        self.snapshots = []
        self.rollback_count += 1


def make_context() -> tuple[Guest, Event, UUID]:
    event_id = uuid4()
    guest = Guest(id=uuid4(), event_id=event_id, status=GuestStatus.ACTIVE)
    event = Event(id=event_id, status=EventStatus.DRAFT)
    return guest, event, uuid4()


def decode(content: bytes) -> str:
    decoded = zxingcpp.read_barcodes(Image.open(BytesIO(content)))
    assert len(decoded) == 1
    return decoded[0].text


def test_issue_hashes_at_rest_and_creates_private_renderable_image(tmp_path: Path) -> None:
    guest, event, administrator_id = make_context()
    repository = FakeQrRepository(guest, event)
    storage = QrImageStorage(tmp_path / "private" / "qr-codes")
    service = QrCredentialService(repository, storage)  # type: ignore[arg-type]

    credential = service.issue(guest.id, administrator_id=administrator_id)
    image = service.read_renderable_image(credential)
    raw_from_controlled_decode = decode(image)

    assert credential.status is QrCredentialStatus.ACTIVE
    assert credential.event_id == event.id
    assert credential.guest_id == guest.id
    assert credential.issued_by == administrator_id
    assert credential.token_hash == hash_qr_credential(raw_from_controlled_decode)
    assert raw_from_controlled_decode != credential.token_hash
    assert not hasattr(credential, "raw_credential")
    assert storage.exists(credential.image_storage_key)

    with pytest.raises(QrConflictError, match="already exists"):
        service.issue(guest.id, administrator_id=administrator_id)


def test_deactivate_activate_and_regenerate_preserve_one_active_credential(
    tmp_path: Path,
) -> None:
    guest, event, administrator_id = make_context()
    repository = FakeQrRepository(guest, event)
    storage = QrImageStorage(tmp_path / "private" / "qr-codes")
    service = QrCredentialService(repository, storage)  # type: ignore[arg-type]
    original = service.issue(guest.id, administrator_id=administrator_id)
    original_image = service.read_renderable_image(original)

    deactivated = service.deactivate(guest.id)
    assert deactivated.status is QrCredentialStatus.INACTIVE
    assert deactivated.deactivated_at is not None
    assert service.deactivate(guest.id) is deactivated

    activated = service.activate(guest.id)
    assert activated.status is QrCredentialStatus.ACTIVE
    assert activated.deactivated_at is None
    assert service.activate(guest.id) is activated

    replacement = service.regenerate(guest.id, administrator_id=administrator_id)
    replacement_image = service.read_renderable_image(replacement)
    assert replacement.replaces_credential_id == original.id
    assert replacement.status is QrCredentialStatus.ACTIVE
    assert original.status is QrCredentialStatus.SUPERSEDED
    assert original.superseded_at is not None
    assert replacement.token_hash != original.token_hash
    assert decode(replacement_image) != decode(original_image)
    assert storage.exists(original.image_storage_key)
    assert sum(item.status is QrCredentialStatus.ACTIVE for item in repository.records) == 1
    with pytest.raises(QrConflictError, match="only active"):
        service.read_renderable_image(original)


@pytest.mark.parametrize(
    ("guest_status", "event_status", "message"),
    [
        (GuestStatus.INACTIVE, EventStatus.DRAFT, "inactive guests"),
        (GuestStatus.ACTIVE, EventStatus.COMPLETED, "COMPLETED events"),
        (GuestStatus.ACTIVE, EventStatus.CANCELLED, "CANCELLED events"),
    ],
)
def test_ineligible_guest_or_event_cannot_issue(
    tmp_path: Path,
    guest_status: GuestStatus,
    event_status: EventStatus,
    message: str,
) -> None:
    guest, event, administrator_id = make_context()
    guest.status = guest_status
    event.status = event_status
    repository = FakeQrRepository(guest, event)
    service = QrCredentialService(
        repository,  # type: ignore[arg-type]
        QrImageStorage(tmp_path / "private" / "qr-codes"),
    )

    with pytest.raises(QrConflictError, match=message):
        service.issue(guest.id, administrator_id=administrator_id)


def test_nonexistent_guest_and_missing_credential_are_distinct(tmp_path: Path) -> None:
    repository = FakeQrRepository(None, None)
    service = QrCredentialService(
        repository,  # type: ignore[arg-type]
        QrImageStorage(tmp_path / "private" / "qr-codes"),
    )
    with pytest.raises(QrResourceNotFoundError, match="guest not found"):
        service.issue(uuid4(), administrator_id=uuid4())
    with pytest.raises(QrResourceNotFoundError, match="credential not found"):
        service.get(uuid4())


def test_database_failure_cleans_issuance_and_regeneration_artifacts(
    tmp_path: Path,
) -> None:
    guest, event, administrator_id = make_context()
    repository = FakeQrRepository(guest, event)
    storage_root = tmp_path / "private" / "qr-codes"
    service = QrCredentialService(repository, QrImageStorage(storage_root))  # type: ignore[arg-type]
    repository.commit_error = IntegrityError("synthetic", {}, Exception("conflict"))

    with pytest.raises(QrConflictError):
        service.issue(guest.id, administrator_id=administrator_id)
    assert list(storage_root.rglob("*.png")) == []
    assert repository.records == []

    repository.commit_error = None
    original = service.issue(guest.id, administrator_id=administrator_id)
    repository.commit_error = IntegrityError("synthetic", {}, Exception("conflict"))
    with pytest.raises(QrConflictError, match="regeneration"):
        service.regenerate(guest.id, administrator_id=administrator_id)
    assert original.status is QrCredentialStatus.ACTIVE
    assert list(storage_root.rglob("*.png")) == [storage_root / original.image_storage_key]
    assert len(repository.records) == 1


def test_storage_failure_rolls_back_without_database_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, event, administrator_id = make_context()
    repository = FakeQrRepository(guest, event)
    storage = QrImageStorage(tmp_path / "private" / "qr-codes")
    service = QrCredentialService(repository, storage)  # type: ignore[arg-type]

    def fail_store(*args, **kwargs):
        raise OSError("C:/private/secret/internal-path")

    monkeypatch.setattr(storage, "store", fail_store)
    with pytest.raises(QrStorageError, match="could not be stored") as exc_info:
        service.issue(guest.id, administrator_id=administrator_id)
    assert "internal-path" not in str(exc_info.value)
    assert repository.records == []
    assert repository.rollback_count == 1


def test_render_boundary_detects_image_tampering(tmp_path: Path) -> None:
    guest, event, administrator_id = make_context()
    repository = FakeQrRepository(guest, event)
    root = tmp_path / "private" / "qr-codes"
    service = QrCredentialService(repository, QrImageStorage(root))  # type: ignore[arg-type]
    credential = service.issue(guest.id, administrator_id=administrator_id)
    (root / credential.image_storage_key).write_bytes(b"tampered")

    with pytest.raises(QrStorageError, match="integrity"):
        service.read_renderable_image(credential)
