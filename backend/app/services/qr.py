"""Transactional M5 QR issuance, lifecycle, and M6 image boundary."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.event import EventStatus
from app.models.guest import GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import generate_qr_credential, hash_qr_credential
from app.qr.images import render_qr_png
from app.qr.storage import QrImageStorage
from app.repositories.qr import QrCredentialRepository


class QrResourceNotFoundError(LookupError):
    """Raised when a guest, event, or credential does not exist."""


class QrConflictError(ValueError):
    """Raised when a lifecycle request conflicts with current M5 state."""


class QrStorageError(RuntimeError):
    """Raised with a bounded message when protected artifact operations fail."""


class QrPersistenceError(RuntimeError):
    """Raised when PostgreSQL cannot persist a non-conflicting M5 mutation."""


class QrCredentialService:
    """Coordinate hash-only persistence with protected QR artifact storage."""

    def __init__(self, repository: QrCredentialRepository, storage: QrImageStorage) -> None:
        self._repository = repository
        self._storage = storage

    def issue(self, guest_id: UUID, *, administrator_id: UUID) -> GuestQrCredential:
        """Issue once for an eligible guest without returning the raw credential."""
        guest, event = self._require_eligible_guest(guest_id)
        if self._repository.get_latest_for_guest(guest_id) is not None:
            self._repository.rollback()
            raise QrConflictError("QR credential already exists; activate or regenerate it")
        credential, storage_key = self._prepare_credential(
            event.id,
            guest.id,
            administrator_id=administrator_id,
            replaces_credential_id=None,
        )
        self._repository.add(credential)
        self._commit_new_artifact(storage_key, conflict_message="QR credential already exists")
        return credential

    def get(self, guest_id: UUID) -> GuestQrCredential:
        """Return only the current/latest persisted credential representation."""
        credential = self._repository.get_latest_for_guest(guest_id)
        if credential is None:
            raise QrResourceNotFoundError("QR credential not found")
        return credential

    def list_for_event(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[GuestQrCredential]:
        if self._repository.get_event(event_id) is None:
            raise QrResourceNotFoundError("event not found")
        return self._repository.list_for_event(event_id, limit=limit, offset=offset)

    def deactivate(self, guest_id: UUID) -> GuestQrCredential:
        """Idempotently make the current credential unusable without deleting evidence."""
        self._require_guest_context(guest_id)
        credential = self._require_latest(guest_id)
        if credential.status is QrCredentialStatus.SUPERSEDED:
            self._repository.rollback()
            raise QrConflictError("superseded QR credentials cannot be changed")
        if credential.status is QrCredentialStatus.INACTIVE:
            self._repository.rollback()
            return credential
        credential.status = QrCredentialStatus.INACTIVE
        credential.deactivated_at = datetime.now(UTC)
        self._commit_lifecycle("QR credential could not be deactivated")
        return credential

    def activate(self, guest_id: UUID) -> GuestQrCredential:
        """Reactivate only a non-terminal credential for a currently eligible guest."""
        self._require_eligible_guest(guest_id)
        credential = self._require_latest(guest_id)
        if credential.status is QrCredentialStatus.SUPERSEDED:
            self._repository.rollback()
            raise QrConflictError("superseded QR credentials cannot be reactivated")
        if credential.status is QrCredentialStatus.ACTIVE:
            self._repository.rollback()
            return credential
        credential.status = QrCredentialStatus.ACTIVE
        credential.deactivated_at = None
        self._commit_lifecycle("QR credential could not be activated")
        return credential

    def regenerate(self, guest_id: UUID, *, administrator_id: UUID) -> GuestQrCredential:
        """Atomically supersede the current verifier and issue a new image-backed one."""
        guest, event = self._require_eligible_guest(guest_id)
        previous = self._require_latest(guest_id)
        credential, storage_key = self._prepare_credential(
            event.id,
            guest.id,
            administrator_id=administrator_id,
            replaces_credential_id=previous.id,
        )
        previous.status = QrCredentialStatus.SUPERSEDED
        previous.deactivated_at = None
        previous.superseded_at = datetime.now(UTC)
        try:
            self._repository.flush()
            self._repository.add(credential)
            self._repository.commit()
        except IntegrityError as exc:
            self._rollback_and_delete(storage_key)
            raise QrConflictError(
                "QR credential regeneration conflicted with current state"
            ) from exc
        except SQLAlchemyError as exc:
            self._rollback_and_delete(storage_key)
            raise QrPersistenceError("QR credential regeneration could not be persisted") from exc
        return credential

    def read_renderable_image(self, credential: GuestQrCredential) -> bytes:
        """Provide verified protected bytes to M6 without recovering the raw credential."""
        if credential.status is not QrCredentialStatus.ACTIVE:
            raise QrConflictError("only active QR credentials may be rendered")
        try:
            content = self._storage.read(credential.image_storage_key)
        except (OSError, ValueError) as exc:
            raise QrStorageError("protected QR image is unavailable") from exc
        actual_digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual_digest, credential.image_sha256):
            raise QrStorageError("protected QR image failed its integrity check")
        return content

    def _require_guest_context(self, guest_id: UUID):
        context = self._repository.get_guest_context_for_update(guest_id)
        if context is None:
            self._repository.rollback()
            raise QrResourceNotFoundError("guest not found")
        return context

    def _require_eligible_guest(self, guest_id: UUID):
        guest, event = self._require_guest_context(guest_id)
        if guest.status is not GuestStatus.ACTIVE:
            self._repository.rollback()
            raise QrConflictError("inactive guests are not eligible for QR credentials")
        if event.status not in {EventStatus.DRAFT, EventStatus.ACTIVE}:
            self._repository.rollback()
            raise QrConflictError(f"{event.status.value} events cannot issue QR credentials")
        return guest, event

    def _require_latest(self, guest_id: UUID) -> GuestQrCredential:
        credential = self._repository.get_latest_for_guest(guest_id)
        if credential is None:
            self._repository.rollback()
            raise QrResourceNotFoundError("QR credential not found")
        return credential

    def _prepare_credential(
        self,
        event_id: UUID,
        guest_id: UUID,
        *,
        administrator_id: UUID,
        replaces_credential_id: UUID | None,
    ) -> tuple[GuestQrCredential, str]:
        credential_id = uuid4()
        raw_credential = generate_qr_credential()
        try:
            token_hash = hash_qr_credential(raw_credential)
            image = render_qr_png(raw_credential)
        finally:
            raw_credential = ""
        image_sha256 = hashlib.sha256(image).hexdigest()
        try:
            storage_key = self._storage.store(event_id, guest_id, credential_id, image)
        except (OSError, ValueError) as exc:
            self._repository.rollback()
            raise QrStorageError("protected QR image could not be stored") from exc
        issued_at = datetime.now(UTC)
        return (
            GuestQrCredential(
                id=credential_id,
                event_id=event_id,
                guest_id=guest_id,
                token_hash=token_hash,
                image_storage_key=storage_key,
                image_sha256=image_sha256,
                status=QrCredentialStatus.ACTIVE,
                replaces_credential_id=replaces_credential_id,
                issued_by=administrator_id,
                issued_at=issued_at,
                deactivated_at=None,
                superseded_at=None,
            ),
            storage_key,
        )

    def _commit_new_artifact(self, storage_key: str, *, conflict_message: str) -> None:
        try:
            self._repository.commit()
        except IntegrityError as exc:
            self._rollback_and_delete(storage_key)
            raise QrConflictError(conflict_message) from exc
        except SQLAlchemyError as exc:
            self._rollback_and_delete(storage_key)
            raise QrPersistenceError("QR credential could not be persisted") from exc

    def _commit_lifecycle(self, message: str) -> None:
        try:
            self._repository.commit()
        except IntegrityError as exc:
            self._repository.rollback()
            raise QrConflictError(message) from exc
        except SQLAlchemyError as exc:
            self._repository.rollback()
            raise QrPersistenceError(message) from exc

    def _rollback_and_delete(self, storage_key: str) -> None:
        self._repository.rollback()
        try:
            self._storage.delete(storage_key)
        except (OSError, ValueError) as exc:
            raise QrStorageError(
                "failed QR transaction cleanup could not remove its image"
            ) from exc
