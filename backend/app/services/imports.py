"""Complete M4 Excel import orchestration and transaction boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.imports.storage import ImportEvidenceStorage
from app.imports.workbook import ParsedWorkbook, WorkbookParser, WorkbookRejectedError
from app.models.event import DEFAULT_GUEST_FIELD_CONFIGURATION, EventGuestField, GuestFieldKey
from app.models.guest import Guest
from app.models.imports import (
    ImportBatch,
    ImportBatchRow,
    ImportBatchStatus,
    ImportRevisionKind,
    ImportRowRevision,
    ImportRowStatus,
)
from app.repositories.events import EventRepository
from app.repositories.guests import GuestRepository
from app.repositories.imports import ImportRepository
from app.services.events import EventLifecycle, M3ConflictError
from app.services.guest_validation import (
    GuestFieldError,
    GuestFieldPolicy,
    error,
    guest_identity_fingerprints,
    validate_guest_values,
)

ALLOWED_XLSX_MEDIA_TYPES = frozenset(
    {
        "application/octet-stream",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
_NORMALIZATION_ERROR_CODES = frozenset(
    {
        "FORMULA_NOT_ALLOWED",
        "INVALID_COMPANION_COUNT",
        "LOSSY_VALUE_NOT_ALLOWED",
        "UNSUPPORTED_VALUE_TYPE",
    }
)


class ImportResourceNotFoundError(LookupError):
    """An event-scoped M4 resource does not exist."""


class ImportConflictError(ValueError):
    """A requested import mutation conflicts with current staged state."""


class ImportUploadRejectedError(ValueError):
    """A client upload failed a bounded media, size, or workbook check."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ImportService:
    """Coordinate XLSX parsing, staging, correction, progression, and reconciliation."""

    def __init__(
        self,
        events: EventRepository,
        guests: GuestRepository,
        imports: ImportRepository,
        parser: WorkbookParser,
        storage: ImportEvidenceStorage,
        *,
        maximum_workbook_bytes: int,
    ) -> None:
        self._events = events
        self._guests = guests
        self._imports = imports
        self._parser = parser
        self._storage = storage
        self._maximum_workbook_bytes = maximum_workbook_bytes

    def upload_workbook(
        self,
        event_id: UUID,
        *,
        administrator_id: UUID,
        filename: str | None,
        content_type: str | None,
        content: bytes,
    ) -> ImportBatch:
        """Parse once, snapshot policy, privately store evidence, and stage every guest row."""
        self._validate_upload_envelope(filename, content_type, content)
        event = self._events.get(event_id)
        if event is None:
            raise ImportResourceNotFoundError("event not found")
        try:
            EventLifecycle.require_mutable(event)
        except M3ConflictError as exc:
            raise ImportConflictError(str(exc)) from exc
        fields = self._require_guest_fields(event_id)
        policies = _policies_from_models(fields)
        initial_field_snapshot = _field_snapshot(fields)
        initial_companion_maximum = event.max_companions_per_invitation

        try:
            parsed = self._parser.parse(content, policies, event.max_companions_per_invitation)
        except WorkbookRejectedError as exc:
            raise ImportUploadRejectedError(exc.code, str(exc)) from exc

        locked_event = self._events.get_for_update(event_id)
        if locked_event is None:
            raise ImportResourceNotFoundError("event not found")
        try:
            EventLifecycle.require_mutable(locked_event)
        except M3ConflictError as exc:
            raise ImportConflictError(str(exc)) from exc
        locked_fields = self._require_guest_fields(event_id)
        if initial_field_snapshot != _field_snapshot(locked_fields) or (
            initial_companion_maximum != locked_event.max_companions_per_invitation
        ):
            self._imports.rollback()
            raise ImportConflictError("event guest policy changed during upload; retry")

        batch_id = uuid4()
        digest = hashlib.sha256(content).hexdigest()
        existing_fingerprints = self._existing_guest_fingerprints(event_id)
        staged_fingerprints = _flatten_fingerprints(
            self._imports.valid_staged_fingerprints(event_id)
        )
        rows = self._build_staged_rows(
            batch_id,
            parsed,
            existing_fingerprints=existing_fingerprints,
            staged_fingerprints=staged_fingerprints,
        )
        batch = ImportBatch(
            id=batch_id,
            event_id=event_id,
            uploaded_by=administrator_id,
            workbook_sha256=digest,
            workbook_byte_size=len(content),
            storage_key="pending",
            detected_sheet_name=parsed.sheet_name,
            header_row_number=parsed.header_row_number,
            column_mapping_snapshot=parsed.column_mapping,
            structure_snapshot={"ignored_rows": list(parsed.ignored_rows)},
            guest_field_snapshot=_field_snapshot(locked_fields),
            max_companions_snapshot=locked_event.max_companions_per_invitation,
            candidate_row_count=len(rows),
            ignored_row_count=len(parsed.ignored_rows),
            status=ImportBatchStatus.VALIDATED,
        )
        if all(row.status == ImportRowStatus.IMPORTED for row in rows):
            batch.status = ImportBatchStatus.RECONCILED

        storage_key: str | None = None
        try:
            storage_key = self._storage.store(event_id, batch_id, digest, content)
            batch.storage_key = storage_key
            self._imports.add_batch(batch, rows)
            self._imports.commit()
        except (IntegrityError, OSError) as exc:
            self._imports.rollback()
            if storage_key is not None:
                self._storage.delete(storage_key)
            raise ImportConflictError("import batch could not be persisted") from exc
        return batch

    def list_batches(self, event_id: UUID, *, limit: int, offset: int) -> list[ImportBatch]:
        self._require_event(event_id)
        return self._imports.list_batches(event_id, limit=limit, offset=offset)

    def get_batch(self, event_id: UUID, batch_id: UUID) -> ImportBatch:
        batch = self._imports.get_batch(event_id, batch_id)
        if batch is None:
            raise ImportResourceNotFoundError("import batch not found")
        return batch

    def list_rows(
        self,
        event_id: UUID,
        batch_id: UUID,
        *,
        row_status: ImportRowStatus | None,
        limit: int,
        offset: int,
    ) -> list[ImportBatchRow]:
        self.get_batch(event_id, batch_id)
        return self._imports.list_rows(
            batch_id,
            row_status=row_status,
            limit=limit,
            offset=offset,
        )

    def reconcile(self, event_id: UUID, batch_id: UUID) -> dict[str, int | str]:
        """Return counts whose candidate-row partition must always balance exactly."""
        batch = self.get_batch(event_id, batch_id)
        return self.reconcile_batch(batch)

    def reconcile_batch(self, batch: ImportBatch) -> dict[str, int | str]:
        """Reconcile an already event-scoped batch without a redundant detail query."""
        counts = self._imports.status_counts(batch.id)
        valid = counts.get(ImportRowStatus.VALID, 0)
        invalid = counts.get(ImportRowStatus.INVALID, 0)
        duplicate = counts.get(ImportRowStatus.DUPLICATE, 0)
        pending = counts.get(ImportRowStatus.PENDING_REVALIDATION, 0)
        imported = counts.get(ImportRowStatus.IMPORTED, 0)
        discovered = valid + invalid + duplicate + pending + imported
        if discovered != batch.candidate_row_count:
            raise ImportConflictError("import batch row counts do not reconcile")
        return {
            "actual_guest_rows_discovered": discovered,
            "ignored_non_data_rows": batch.ignored_row_count,
            "currently_valid_staged_rows": valid,
            "currently_invalid_staged_rows": invalid,
            "duplicates": duplicate,
            "pending_revalidation": pending,
            "rows_already_imported": imported,
            "newly_importable_rows": valid,
            "remaining_unresolved_rows": invalid + duplicate + pending,
            "guests_created_from_batch": imported,
            "overall_batch_state": batch.status.value,
        }

    def correct_row(
        self,
        event_id: UUID,
        batch_id: UUID,
        row_id: UUID,
        *,
        administrator_id: UUID,
        expected_version: int,
        values: dict[str, Any],
    ) -> ImportBatchRow:
        """Version a permitted correction without mutating original workbook evidence."""
        batch = self._require_locked_batch(event_id, batch_id)
        row = self._require_locked_row(event_id, batch_id, row_id)
        if row.status not in {ImportRowStatus.INVALID, ImportRowStatus.DUPLICATE}:
            raise ImportConflictError("only invalid or duplicate staged rows may be corrected")
        if row.row_version != expected_version:
            raise ImportConflictError("staged row version is stale")

        normalized_patch = _normalize_correction_patch(values)
        current_values = dict(row.current_values)
        changed_values = {
            key: value
            for key, value in normalized_patch.items()
            if current_values.get(key) != value
        }
        if not changed_values:
            raise ImportConflictError("correction must change at least one staged value")
        current_values.update(changed_values)

        preserved_errors = [
            item
            for item in row.validation_errors
            if item.get("code") in _NORMALIZATION_ERROR_CODES
            and item.get("field") not in changed_values
        ]
        row.current_values = current_values
        row.identity_fingerprints = list(guest_identity_fingerprints(current_values))
        row.validation_errors = preserved_errors
        row.status = ImportRowStatus.PENDING_REVALIDATION
        row.row_version += 1
        self._imports.add_revision(
            ImportRowRevision(
                id=uuid4(),
                row_id=row.id,
                revision=row.row_version,
                kind=ImportRevisionKind.CORRECTION,
                changed_values=changed_values,
                values_snapshot=current_values,
                validation_status=ImportRowStatus.PENDING_REVALIDATION,
                validation_errors=preserved_errors,
                actor_id=administrator_id,
            )
        )
        self._refresh_batch_status(batch)
        self._commit_bounded("staged row correction could not be persisted")
        return row

    def revalidate_row(
        self,
        event_id: UUID,
        batch_id: UUID,
        row_id: UUID,
        *,
        administrator_id: UUID,
    ) -> ImportBatchRow:
        """Explicitly revalidate one corrected row under its immutable batch snapshot."""
        batch = self._require_locked_batch(event_id, batch_id)
        row = self._require_locked_row(event_id, batch_id, row_id)
        if row.status != ImportRowStatus.PENDING_REVALIDATION:
            raise ImportConflictError("staged row is not pending revalidation")
        reserved = self._reserved_fingerprints(event_id, exclude_row_id=row.id)
        self._apply_revalidation(row, batch, administrator_id, reserved)
        self._refresh_batch_status(batch)
        self._commit_bounded("staged row revalidation could not be persisted")
        return row

    def revalidate_pending_rows(
        self,
        event_id: UUID,
        batch_id: UUID,
        *,
        administrator_id: UUID,
    ) -> list[ImportBatchRow]:
        """Revalidate all corrected rows once, in deterministic workbook order."""
        batch = self._require_locked_batch(event_id, batch_id)
        rows = self._imports.pending_rows_for_update(batch.id)
        reserved = self._reserved_fingerprints(event_id)
        for row in rows:
            self._apply_revalidation(row, batch, administrator_id, reserved)
            if row.status == ImportRowStatus.VALID:
                reserved.update(row.identity_fingerprints)
        self._refresh_batch_status(batch)
        self._commit_bounded("staged row revalidation could not be persisted")
        return rows

    def progress_valid_rows(
        self,
        event_id: UUID,
        batch_id: UUID,
        *,
        administrator_id: UUID,
    ) -> dict[str, int]:
        """Import currently valid rows once, isolating row-level persistence failures."""
        event = self._events.get_for_update(event_id)
        if event is None:
            raise ImportResourceNotFoundError("event not found")
        try:
            EventLifecycle.require_mutable(event)
        except M3ConflictError as exc:
            raise ImportConflictError(str(exc)) from exc
        batch = self._require_locked_batch(event_id, batch_id)
        policies = _policies_from_models(self._require_guest_fields(event_id))
        rows = self._imports.eligible_rows_for_update(batch.id)
        reserved = self._existing_guest_fingerprints(event_id)
        imported_count = 0
        invalidated_count = 0
        duplicate_count = 0

        for row in rows:
            current_values = dict(row.current_values)
            errors = validate_guest_values(
                current_values,
                policies,
                event.max_companions_per_invitation,
            )
            fingerprints = set(guest_identity_fingerprints(current_values))
            if not errors and fingerprints & reserved:
                errors.append(
                    error(None, "DUPLICATE_EXISTING_GUEST", "row duplicates an event guest")
                )
            if errors:
                row.status = _status_for_errors(errors)
                row.validation_errors = errors
                row.identity_fingerprints = list(fingerprints)
                self._append_revalidation_revision(row, administrator_id)
                if row.status == ImportRowStatus.DUPLICATE:
                    duplicate_count += 1
                else:
                    invalidated_count += 1
                continue

            guest = Guest(
                id=uuid4(),
                event_id=event_id,
                **_guest_persistence_values(current_values),
            )
            try:
                with self._imports.savepoint():
                    self._guests.add(guest)
                    self._imports.flush()
                    row.guest_id = guest.id
                    row.status = ImportRowStatus.IMPORTED
                    row.validation_errors = []
                    row.identity_fingerprints = list(fingerprints)
                    self._imports.flush()
            except IntegrityError:
                row.guest_id = None
                row.status = ImportRowStatus.INVALID
                row.validation_errors = [
                    error(
                        None,
                        "PERSISTENCE_CONSTRAINT_REJECTED",
                        "row conflicts with current event constraints; revalidate it",
                    )
                ]
                self._append_revalidation_revision(row, administrator_id)
                invalidated_count += 1
                continue

            reserved.update(fingerprints)
            imported_count += 1

        self._refresh_batch_status(batch)
        self._commit_bounded("eligible rows could not be progressed")
        return {
            "imported_rows": imported_count,
            "invalidated_rows": invalidated_count,
            "duplicate_rows": duplicate_count,
        }

    def _apply_revalidation(
        self,
        row: ImportBatchRow,
        batch: ImportBatch,
        administrator_id: UUID,
        reserved: set[str],
    ) -> None:
        policies = _policies_from_snapshot(batch.guest_field_snapshot)
        current_values = dict(row.current_values)
        prior_errors = [dict(item) for item in row.validation_errors]
        errors = validate_guest_values(
            current_values,
            policies,
            batch.max_companions_snapshot,
            prior_errors=prior_errors,
        )
        fingerprints = set(guest_identity_fingerprints(current_values))
        if not errors and fingerprints & reserved:
            errors.append(error(None, "DUPLICATE_STAGED_OR_EXISTING", "row is a duplicate"))
        row.identity_fingerprints = list(fingerprints)
        row.validation_errors = errors
        row.status = ImportRowStatus.VALID if not errors else _status_for_errors(errors)
        self._append_revalidation_revision(row, administrator_id)

    def _append_revalidation_revision(
        self,
        row: ImportBatchRow,
        administrator_id: UUID,
    ) -> None:
        row.row_version += 1
        self._imports.add_revision(
            ImportRowRevision(
                id=uuid4(),
                row_id=row.id,
                revision=row.row_version,
                kind=ImportRevisionKind.REVALIDATION,
                changed_values={},
                values_snapshot=dict(row.current_values),
                validation_status=row.status,
                validation_errors=list(row.validation_errors),
                actor_id=administrator_id,
            )
        )

    def _build_staged_rows(
        self,
        batch_id: UUID,
        parsed: ParsedWorkbook,
        *,
        existing_fingerprints: set[str],
        staged_fingerprints: set[str],
    ) -> list[ImportBatchRow]:
        rows: list[ImportBatchRow] = []
        for parsed_row in parsed.rows:
            errors = list(parsed_row.validation_errors)
            fingerprints = set(parsed_row.identity_fingerprints)
            if not errors and fingerprints & existing_fingerprints:
                errors.append(
                    error(None, "DUPLICATE_EXISTING_GUEST", "row duplicates an event guest")
                )
            elif not errors and fingerprints & staged_fingerprints:
                errors.append(
                    error(None, "DUPLICATE_STAGED_ROW", "row duplicates another staged row")
                )
            row_status = ImportRowStatus.VALID if not errors else _status_for_errors(errors)
            if row_status == ImportRowStatus.VALID:
                staged_fingerprints.update(fingerprints)
            rows.append(
                ImportBatchRow(
                    id=uuid4(),
                    batch_id=batch_id,
                    source_row_number=parsed_row.source_row_number,
                    original_values=dict(parsed_row.values),
                    original_validation_errors=errors,
                    current_values=dict(parsed_row.values),
                    validation_errors=errors,
                    identity_fingerprints=list(fingerprints),
                    status=row_status,
                    row_version=1,
                    guest_id=None,
                )
            )
        return rows

    def _reserved_fingerprints(
        self,
        event_id: UUID,
        *,
        exclude_row_id: UUID | None = None,
    ) -> set[str]:
        reserved = self._existing_guest_fingerprints(event_id)
        reserved.update(
            _flatten_fingerprints(
                self._imports.valid_staged_fingerprints(
                    event_id,
                    exclude_row_id=exclude_row_id,
                )
            )
        )
        return reserved

    def _existing_guest_fingerprints(self, event_id: UUID) -> set[str]:
        result: set[str] = set()
        for values in self._imports.guest_identity_values(event_id):
            result.update(guest_identity_fingerprints(values))
        return result

    def _require_event(self, event_id: UUID) -> None:
        if self._events.get(event_id) is None:
            raise ImportResourceNotFoundError("event not found")

    def _require_guest_fields(self, event_id: UUID) -> list[EventGuestField]:
        fields = self._events.get_guest_fields(event_id)
        if len(fields) != len(DEFAULT_GUEST_FIELD_CONFIGURATION):
            raise ImportConflictError("event guest-field configuration is incomplete")
        return fields

    def _require_locked_batch(self, event_id: UUID, batch_id: UUID) -> ImportBatch:
        batch = self._imports.get_batch(event_id, batch_id, for_update=True)
        if batch is None:
            raise ImportResourceNotFoundError("import batch not found")
        return batch

    def _require_locked_row(
        self,
        event_id: UUID,
        batch_id: UUID,
        row_id: UUID,
    ) -> ImportBatchRow:
        row = self._imports.get_row(event_id, batch_id, row_id, for_update=True)
        if row is None:
            raise ImportResourceNotFoundError("staged import row not found")
        return row

    def _refresh_batch_status(self, batch: ImportBatch) -> None:
        try:
            self._imports.flush()
        except IntegrityError as exc:
            self._imports.rollback()
            raise ImportConflictError("import state could not be reconciled") from exc
        counts = self._imports.status_counts(batch.id)
        imported = counts.get(ImportRowStatus.IMPORTED, 0)
        remaining = sum(
            counts.get(row_status, 0)
            for row_status in (
                ImportRowStatus.VALID,
                ImportRowStatus.INVALID,
                ImportRowStatus.DUPLICATE,
                ImportRowStatus.PENDING_REVALIDATION,
            )
        )
        if remaining == 0:
            batch.status = ImportBatchStatus.RECONCILED
        elif imported:
            batch.status = ImportBatchStatus.PARTIALLY_IMPORTED
        else:
            batch.status = ImportBatchStatus.VALIDATED

    def _commit_bounded(self, message: str) -> None:
        try:
            self._imports.commit()
        except IntegrityError as exc:
            self._imports.rollback()
            raise ImportConflictError(message) from exc

    def _validate_upload_envelope(
        self,
        filename: str | None,
        content_type: str | None,
        content: bytes,
    ) -> None:
        if filename is None or Path(filename).suffix.casefold() != ".xlsx":
            raise ImportUploadRejectedError(
                "UNSUPPORTED_FILE_TYPE", "only .xlsx workbooks are supported"
            )
        normalized_media_type = (content_type or "").split(";", 1)[0].strip().casefold()
        if normalized_media_type not in ALLOWED_XLSX_MEDIA_TYPES:
            raise ImportUploadRejectedError(
                "UNSUPPORTED_FILE_TYPE", "workbook media type is unsupported"
            )
        if not content:
            raise ImportUploadRejectedError("EMPTY_WORKBOOK", "workbook is empty")
        if len(content) > self._maximum_workbook_bytes:
            raise ImportUploadRejectedError(
                "WORKBOOK_TOO_LARGE", "workbook exceeds the configured upload limit"
            )


def _policies_from_models(fields: list[EventGuestField]) -> list[GuestFieldPolicy]:
    return [
        GuestFieldPolicy(field.field_key, field.enabled, field.required)
        for field in sorted(fields, key=lambda item: item.sort_order)
    ]


def _policies_from_snapshot(snapshot: list[dict[str, object]]) -> list[GuestFieldPolicy]:
    return [
        GuestFieldPolicy(
            GuestFieldKey(str(item["field_key"])),
            bool(item["enabled"]),
            bool(item["required"]),
        )
        for item in snapshot
    ]


def _field_snapshot(fields: list[EventGuestField]) -> list[dict[str, object]]:
    return [
        {
            "field_key": field.field_key.value,
            "enabled": field.enabled,
            "required": field.required,
            "sort_order": field.sort_order,
        }
        for field in sorted(fields, key=lambda item: item.sort_order)
    ]


def _status_for_errors(errors: list[GuestFieldError] | list[dict[str, object]]) -> ImportRowStatus:
    if any(str(item.get("code", "")).startswith("DUPLICATE") for item in errors):
        return ImportRowStatus.DUPLICATE
    return ImportRowStatus.INVALID


def _flatten_fingerprints(values: list[list[str]]) -> set[str]:
    return {fingerprint for group in values for fingerprint in group}


def _normalize_correction_patch(values: dict[str, Any]) -> dict[str, str | int | None]:
    normalized: dict[str, str | int | None] = {}
    for key, value in values.items():
        field_key = GuestFieldKey(key)
        if field_key is GuestFieldKey.ALLOWED_COMPANIONS:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ImportConflictError("allowed companions correction must be an integer")
            normalized[key] = value
            continue
        if value is None:
            normalized[key] = None
            continue
        if not isinstance(value, str):
            raise ImportConflictError(f"{key} correction must be text or null")
        normalized_value = value.strip()
        if not normalized_value:
            normalized[key] = None
        elif field_key is GuestFieldKey.EMAIL:
            normalized[key] = normalized_value.casefold()
        else:
            normalized[key] = normalized_value
    return normalized


def _guest_persistence_values(values: dict[str, Any]) -> dict[str, Any]:
    return {
        field_key.value: values.get(field_key.value)
        for field_key, _, _ in DEFAULT_GUEST_FIELD_CONFIGURATION
    }
