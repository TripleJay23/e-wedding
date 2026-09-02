"""M4 service state-machine, correction, duplicate, and partial-progression tests."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.imports.workbook import ParsedGuestRow, ParsedWorkbook, WorkbookRejectedError
from app.models.event import (
    DEFAULT_GUEST_FIELD_CONFIGURATION,
    Event,
    EventGuestField,
    EventStatus,
)
from app.models.guest import Guest
from app.models.imports import (
    ImportBatch,
    ImportBatchRow,
    ImportBatchStatus,
    ImportRevisionKind,
    ImportRowStatus,
)
from app.services.guest_validation import error, guest_identity_fingerprints
from app.services.imports import (
    ImportConflictError,
    ImportResourceNotFoundError,
    ImportService,
    ImportUploadRejectedError,
)


def make_event(*, status: EventStatus = EventStatus.DRAFT, maximum: int = 2) -> Event:
    return Event(
        id=uuid4(),
        event_name="Synthetic Import Wedding",
        bride_name="Amina",
        groom_name="Baraka",
        event_datetime=datetime(2027, 6, 12, tzinfo=UTC),
        venue="Synthetic Hall",
        status=status,
        max_companions_per_invitation=maximum,
        created_by=uuid4(),
    )


def make_fields(event_id: UUID) -> list[EventGuestField]:
    return [
        EventGuestField(
            id=uuid4(),
            event_id=event_id,
            field_key=field_key,
            enabled=enabled,
            required=required,
            sort_order=sort_order,
        )
        for sort_order, (field_key, enabled, required) in enumerate(
            DEFAULT_GUEST_FIELD_CONFIGURATION
        )
    ]


def guest_values(
    name: str,
    *,
    invitation_type: str | None = "Family",
    email: str | None = None,
    phone: str | None = None,
    companions: int = 0,
) -> dict[str, str | int | None]:
    return {
        "full_name": name,
        "phone_number": phone,
        "email": email,
        "invitation_type": invitation_type,
        "allowed_companions": companions,
        "table_number": None,
        "category": None,
    }


def parsed_row(
    row_number: int,
    values: dict[str, str | int | None],
    errors: list[dict[str, str | None]] | None = None,
    *,
    normalization_errors: list[dict[str, str | None]] | None = None,
) -> ParsedGuestRow:
    return ParsedGuestRow(
        source_row_number=row_number,
        values=values,
        normalization_errors=normalization_errors or [],  # type: ignore[arg-type]
        validation_errors=errors or [],  # type: ignore[arg-type]
        identity_fingerprints=guest_identity_fingerprints(values),
    )


def parsed_workbook(*rows: ParsedGuestRow) -> ParsedWorkbook:
    return ParsedWorkbook(
        sheet_name="Guests",
        header_row_number=4,
        column_mapping={"full_name": 1, "invitation_type": 2},
        ignored_rows=(
            {"row_number": 1, "reason": "PRESENTATION"},
            {"row_number": 2, "reason": "BLANK"},
        ),
        rows=rows,
    )


class FakeEventRepository:
    def __init__(self, event: Event | None) -> None:
        self.event = event
        self.locked_event = event
        self.fields = make_fields(event.id) if event is not None else []
        self.locked_fields: list[EventGuestField] | None = None

    def get(self, event_id: UUID) -> Event | None:
        if self.event is None or self.event.id != event_id:
            return None
        return self.event

    def get_for_update(self, event_id: UUID) -> Event | None:
        if self.locked_event is None or self.locked_event.id != event_id:
            return None
        if self.locked_fields is not None:
            self.fields = self.locked_fields
        return self.locked_event

    def get_guest_fields(self, event_id: UUID) -> list[EventGuestField]:
        if self.event is None or self.event.id != event_id:
            return []
        return self.fields


class FakeGuestRepository:
    def __init__(self) -> None:
        self.items: list[Guest] = []

    def add(self, guest: Guest) -> None:
        self.items.append(guest)


class FakeParser:
    def __init__(self, result: ParsedWorkbook) -> None:
        self.result = result
        self.error: WorkbookRejectedError | None = None
        self.calls = 0

    def parse(self, *args: object) -> ParsedWorkbook:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class FakeStorage:
    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail = False

    def store(self, event_id: UUID, batch_id: UUID, digest: str, content: bytes) -> str:
        if self.fail:
            raise OSError("synthetic storage failure")
        key = f"{event_id}/{batch_id}/{digest}.xlsx"
        self.stored[key] = content
        return key

    def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)
        self.stored.pop(storage_key, None)


class FakeSavepoint(AbstractContextManager[object]):
    def __init__(self, repository: FakeImportRepository) -> None:
        self.repository = repository
        self.guest_count = len(repository.guests.items)

    def __enter__(self) -> object:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if exc is not None:
            del self.repository.guests.items[self.guest_count :]
        return False


class FakeImportRepository:
    def __init__(self, guests: FakeGuestRepository) -> None:
        self.guests = guests
        self.batches: dict[UUID, ImportBatch] = {}
        self.rows: dict[UUID, ImportBatchRow] = {}
        self.revisions: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False
        self.fail_flush_for_name: str | None = None
        self.force_bad_counts = False

    def add_batch(self, batch: ImportBatch, rows: list[ImportBatchRow]) -> None:
        self.batches[batch.id] = batch
        self.rows.update({row.id: row for row in rows})

    def add_revision(self, revision: object) -> None:
        self.revisions.append(revision)

    def get_batch(
        self, event_id: UUID, batch_id: UUID, *, for_update: bool = False
    ) -> ImportBatch | None:
        batch = self.batches.get(batch_id)
        return batch if batch is not None and batch.event_id == event_id else None

    def get_row(
        self,
        event_id: UUID,
        batch_id: UUID,
        row_id: UUID,
        *,
        for_update: bool = False,
    ) -> ImportBatchRow | None:
        row = self.rows.get(row_id)
        batch = self.batches.get(batch_id)
        if row is None or batch is None or batch.event_id != event_id or row.batch_id != batch_id:
            return None
        return row

    def list_batches(self, event_id: UUID, *, limit: int, offset: int) -> list[ImportBatch]:
        values = [batch for batch in self.batches.values() if batch.event_id == event_id]
        return values[offset : offset + limit]

    def list_rows(
        self,
        batch_id: UUID,
        *,
        row_status: ImportRowStatus | None,
        limit: int,
        offset: int,
    ) -> list[ImportBatchRow]:
        values = sorted(
            (
                row
                for row in self.rows.values()
                if row.batch_id == batch_id and (row_status is None or row.status == row_status)
            ),
            key=lambda row: row.source_row_number,
        )
        return values[offset : offset + limit]

    def eligible_rows_for_update(self, batch_id: UUID) -> list[ImportBatchRow]:
        return self.list_rows(
            batch_id,
            row_status=ImportRowStatus.VALID,
            limit=10_000,
            offset=0,
        )

    def selected_rows_for_update(
        self,
        batch_id: UUID,
        row_ids: list[UUID],
    ) -> list[ImportBatchRow]:
        selected_ids = set(row_ids)
        return [
            row
            for row in self.list_rows(batch_id, row_status=None, limit=10_000, offset=0)
            if row.id in selected_ids
        ]

    def pending_rows_for_update(self, batch_id: UUID) -> list[ImportBatchRow]:
        return self.list_rows(
            batch_id,
            row_status=ImportRowStatus.PENDING_REVALIDATION,
            limit=10_000,
            offset=0,
        )

    def valid_staged_fingerprints(
        self,
        batch_id: UUID,
        *,
        exclude_row_id: UUID | None = None,
    ) -> list[list[str]]:
        result: list[list[str]] = []
        for row in self.rows.values():
            if (
                row.batch_id == batch_id
                and row.status == ImportRowStatus.VALID
                and row.id != exclude_row_id
            ):
                result.append(list(row.identity_fingerprints))
        return result

    def guest_identity_values(self, event_id: UUID) -> list[dict[str, object]]:
        return [
            {
                "full_name": guest.full_name,
                "phone_number": guest.phone_number,
                "email": guest.email,
                "invitation_type": guest.invitation_type,
            }
            for guest in self.guests.items
            if guest.event_id == event_id
        ]

    def status_counts(self, batch_id: UUID) -> dict[ImportRowStatus, int]:
        counts: dict[ImportRowStatus, int] = {}
        for row in self.rows.values():
            if row.batch_id == batch_id:
                counts[row.status] = counts.get(row.status, 0) + 1
        if self.force_bad_counts and counts:
            first = next(iter(counts))
            counts[first] -= 1
        return counts

    def savepoint(self) -> FakeSavepoint:
        return FakeSavepoint(self)

    def flush(self) -> None:
        if (
            self.fail_flush_for_name is not None
            and self.guests.items
            and self.guests.items[-1].full_name == self.fail_flush_for_name
        ):
            self.fail_flush_for_name = None
            raise IntegrityError("synthetic", {}, Exception("constraint"))

    def commit(self) -> None:
        if self.fail_commit:
            raise IntegrityError("synthetic", {}, Exception("constraint"))
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def make_batch(
    event: Event, *, status: ImportBatchStatus = ImportBatchStatus.VALIDATED
) -> ImportBatch:
    return ImportBatch(
        id=uuid4(),
        event_id=event.id,
        uploaded_by=event.created_by,
        workbook_sha256="a" * 64,
        workbook_byte_size=100,
        storage_key=f"{event.id}/synthetic.xlsx",
        detected_sheet_name="Guests",
        header_row_number=1,
        column_mapping_snapshot={"full_name": 1, "invitation_type": 2},
        structure_snapshot={"ignored_rows": []},
        guest_field_snapshot=[
            {
                "field_key": field_key.value,
                "enabled": enabled,
                "required": required,
                "sort_order": index,
            }
            for index, (field_key, enabled, required) in enumerate(
                DEFAULT_GUEST_FIELD_CONFIGURATION
            )
        ],
        max_companions_snapshot=2,
        candidate_row_count=0,
        ignored_row_count=0,
        status=status,
    )


def make_staged_row(
    batch: ImportBatch,
    row_number: int,
    values: dict[str, str | int | None],
    *,
    status: ImportRowStatus,
    errors: list[dict[str, str | None]] | None = None,
) -> ImportBatchRow:
    return ImportBatchRow(
        id=uuid4(),
        batch_id=batch.id,
        source_row_number=row_number,
        original_values=dict(values),
        original_validation_errors=errors or [],
        current_values=dict(values),
        validation_errors=errors or [],
        identity_fingerprints=list(guest_identity_fingerprints(values)),
        status=status,
        row_version=1,
        guest_id=None,
    )


def build_service(
    parsed: ParsedWorkbook,
    *,
    event: Event | None = None,
) -> tuple[
    ImportService,
    FakeEventRepository,
    FakeGuestRepository,
    FakeImportRepository,
    FakeParser,
    FakeStorage,
]:
    resolved_event = event or make_event()
    events = FakeEventRepository(resolved_event)
    guests = FakeGuestRepository()
    imports = FakeImportRepository(guests)
    parser = FakeParser(parsed)
    storage = FakeStorage()
    service = ImportService(
        events,  # type: ignore[arg-type]
        guests,  # type: ignore[arg-type]
        imports,  # type: ignore[arg-type]
        parser,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        maximum_workbook_bytes=1_024,
    )
    return service, events, guests, imports, parser, storage


def test_upload_stages_valid_invalid_duplicates_and_private_evidence() -> None:
    valid = guest_values("Synthetic Valid", email="valid@example.test")
    invalid = guest_values("Synthetic Invalid", invitation_type=None)
    in_workbook_duplicate = guest_values("Synthetic Duplicate", email="valid@example.test")
    existing_duplicate = guest_values("Synthetic Existing Duplicate", email="exists@example.test")
    parsed = parsed_workbook(
        parsed_row(5, valid),
        parsed_row(6, invalid, [error("invitation_type", "REQUIRED", "required")]),
        parsed_row(
            7,
            in_workbook_duplicate,
            [error(None, "DUPLICATE_IN_WORKBOOK", "duplicate")],
        ),
        parsed_row(8, existing_duplicate),
    )
    service, events, guests, imports, parser, storage = build_service(parsed)
    assert events.event is not None
    guests.items.append(
        Guest(
            id=uuid4(),
            event_id=events.event.id,
            full_name="Existing Synthetic",
            email="exists@example.test",
            invitation_type="Family",
            allowed_companions=0,
        )
    )

    batch = service.upload_workbook(
        events.event.id,
        administrator_id=events.event.created_by,
        filename="client-path-is-not-used.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=b"synthetic workbook bytes",
    )

    statuses = [
        row.status for row in imports.list_rows(batch.id, row_status=None, limit=10, offset=0)
    ]
    assert statuses == [
        ImportRowStatus.VALID,
        ImportRowStatus.INVALID,
        ImportRowStatus.DUPLICATE,
        ImportRowStatus.DUPLICATE,
    ]
    assert batch.candidate_row_count == 4
    assert batch.ignored_row_count == 2
    assert batch.storage_key in storage.stored
    assert "client-path" not in batch.storage_key
    assert parser.calls == 1
    assert imports.commits == 1
    assert service.reconcile(events.event.id, batch.id) == {
        "actual_guest_rows_discovered": 4,
        "ignored_non_data_rows": 2,
        "currently_valid_staged_rows": 1,
        "currently_invalid_staged_rows": 1,
        "duplicates": 2,
        "pending_revalidation": 0,
        "rows_already_imported": 0,
        "newly_importable_rows": 1,
        "remaining_unresolved_rows": 3,
        "guests_created_from_batch": 0,
        "overall_batch_state": "VALIDATED",
    }


def test_unconfirmed_historical_preview_does_not_reserve_retry_identity() -> None:
    repeated_values = guest_values("Synthetic Retry", email="retry@example.test")
    parsed = parsed_workbook(parsed_row(2, repeated_values))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    historical_batch = make_batch(events.event)
    historical_row = make_staged_row(
        historical_batch,
        2,
        repeated_values,
        status=ImportRowStatus.VALID,
    )
    historical_batch.candidate_row_count = 1
    imports.add_batch(historical_batch, [historical_row])

    retry_batch = service.upload_workbook(
        events.event.id,
        administrator_id=events.event.created_by,
        filename="synthetic-retry.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=b"synthetic retry bytes",
    )

    retry_row = imports.list_rows(retry_batch.id, row_status=None, limit=10, offset=0)[0]
    assert retry_row.status == ImportRowStatus.VALID
    assert retry_row.validation_errors == []
    assert historical_row.status == ImportRowStatus.VALID
    assert historical_row.validation_errors == []


def test_partial_history_blocks_persisted_identity_but_not_unconfirmed_preview() -> None:
    imported_values = guest_values("Synthetic Imported", email="imported@example.test")
    preview_only_values = guest_values("Synthetic Preview", email="preview@example.test")
    parsed = parsed_workbook(
        parsed_row(2, imported_values),
        parsed_row(3, preview_only_values),
    )
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    persisted_guest = Guest(
        id=uuid4(),
        event_id=events.event.id,
        full_name="Synthetic Imported",
        email="imported@example.test",
        invitation_type="Family",
        allowed_companions=0,
    )
    guests.items.append(persisted_guest)
    historical_batch = make_batch(events.event, status=ImportBatchStatus.PARTIALLY_IMPORTED)
    imported_row = make_staged_row(
        historical_batch,
        2,
        imported_values,
        status=ImportRowStatus.IMPORTED,
    )
    imported_row.guest_id = persisted_guest.id
    preview_only_row = make_staged_row(
        historical_batch,
        3,
        preview_only_values,
        status=ImportRowStatus.VALID,
    )
    historical_batch.candidate_row_count = 2
    imports.add_batch(historical_batch, [imported_row, preview_only_row])

    retry_batch = service.upload_workbook(
        events.event.id,
        administrator_id=events.event.created_by,
        filename="synthetic-partial-retry.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=b"synthetic partial retry bytes",
    )

    retry_rows = imports.list_rows(retry_batch.id, row_status=None, limit=10, offset=0)
    assert [row.status for row in retry_rows] == [
        ImportRowStatus.DUPLICATE,
        ImportRowStatus.VALID,
    ]
    assert retry_rows[0].validation_errors[0]["code"] == "DUPLICATE_EXISTING_GUEST"
    assert retry_rows[1].validation_errors == []
    assert imported_row.status == ImportRowStatus.IMPORTED
    assert preview_only_row.status == ImportRowStatus.VALID


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "code"),
    [
        ("guests.csv", "text/csv", b"x", "UNSUPPORTED_FILE_TYPE"),
        ("guests.xlsx", "text/plain", b"x", "UNSUPPORTED_FILE_TYPE"),
        ("guests.xlsx", "application/octet-stream", b"", "EMPTY_WORKBOOK"),
        ("guests.xlsx", "application/octet-stream", b"x" * 1_025, "WORKBOOK_TOO_LARGE"),
    ],
)
def test_upload_envelope_fails_before_parser(
    filename: str,
    content_type: str,
    content: bytes,
    code: str,
) -> None:
    service, events, _, _, parser, _ = build_service(
        parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    )
    assert events.event is not None
    with pytest.raises(ImportUploadRejectedError) as exc_info:
        service.upload_workbook(
            events.event.id,
            administrator_id=events.event.created_by,
            filename=filename,
            content_type=content_type,
            content=content,
        )
    assert exc_info.value.code == code
    assert parser.calls == 0


def test_parser_rejection_is_bounded_and_storage_is_not_called() -> None:
    service, events, _, _, parser, storage = build_service(
        parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    )
    assert events.event is not None
    parser.error = WorkbookRejectedError("MALFORMED_WORKBOOK", "safe rejection")
    with pytest.raises(ImportUploadRejectedError, match="safe rejection") as exc_info:
        service.upload_workbook(
            events.event.id,
            administrator_id=events.event.created_by,
            filename="guests.xlsx",
            content_type="application/octet-stream",
            content=b"x",
        )
    assert exc_info.value.code == "MALFORMED_WORKBOOK"
    assert storage.stored == {}


def test_upload_rejects_missing_terminal_event_and_policy_race() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    missing_service, _, _, _, _, _ = build_service(parsed, event=None)
    missing_service._events.event = None  # type: ignore[attr-defined]
    with pytest.raises(ImportResourceNotFoundError, match="event not found"):
        missing_service.upload_workbook(
            uuid4(),
            administrator_id=uuid4(),
            filename="guests.xlsx",
            content_type="application/octet-stream",
            content=b"x",
        )

    completed = make_event(status=EventStatus.COMPLETED)
    terminal_service, _, _, _, _, _ = build_service(parsed, event=completed)
    with pytest.raises(ImportConflictError, match="immutable"):
        terminal_service.upload_workbook(
            completed.id,
            administrator_id=completed.created_by,
            filename="guests.xlsx",
            content_type="application/octet-stream",
            content=b"x",
        )

    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    events.locked_event = make_event(maximum=1)
    events.locked_event.id = events.event.id
    with pytest.raises(ImportConflictError, match="policy changed"):
        service.upload_workbook(
            events.event.id,
            administrator_id=events.event.created_by,
            filename="guests.xlsx",
            content_type="application/octet-stream",
            content=b"x",
        )
    assert imports.rollbacks == 1


def test_storage_and_commit_failures_are_bounded_and_cleanup_evidence() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, storage = build_service(parsed)
    assert events.event is not None
    storage.fail = True
    with pytest.raises(ImportConflictError, match="persisted"):
        service.upload_workbook(
            events.event.id,
            administrator_id=events.event.created_by,
            filename="guests.xlsx",
            content_type="application/octet-stream",
            content=b"x",
        )
    assert imports.rollbacks == 1

    storage.fail = False
    imports.fail_commit = True
    with pytest.raises(ImportConflictError, match="persisted"):
        service.upload_workbook(
            events.event.id,
            administrator_id=events.event.created_by,
            filename="guests.xlsx",
            content_type="application/octet-stream",
            content=b"x",
        )
    assert len(storage.deleted) == 1
    assert storage.stored == {}


def test_history_rows_cross_scope_and_reconciliation_integrity() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    row = make_staged_row(
        batch,
        2,
        guest_values("Synthetic"),
        status=ImportRowStatus.INVALID,
        errors=[error("email", "INVALID_EMAIL", "invalid")],
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])

    assert service.get_batch(events.event.id, batch.id) is batch
    assert service.list_batches(events.event.id, limit=10, offset=0) == [batch]
    assert service.list_rows(
        events.event.id,
        batch.id,
        row_status=ImportRowStatus.INVALID,
        limit=10,
        offset=0,
    ) == [row]
    with pytest.raises(ImportResourceNotFoundError):
        service.get_batch(uuid4(), batch.id)
    with pytest.raises(ImportResourceNotFoundError):
        service.list_batches(uuid4(), limit=10, offset=0)

    imports.force_bad_counts = True
    with pytest.raises(ImportConflictError, match="do not reconcile"):
        service.reconcile(events.event.id, batch.id)


def test_correction_is_optimistic_preserves_origin_and_requires_explicit_revalidation() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    original = guest_values("Synthetic", email=None)
    row = make_staged_row(
        batch,
        2,
        original,
        status=ImportRowStatus.INVALID,
        errors=[error("email", "FORMULA_NOT_ALLOWED", "formula")],
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])

    with pytest.raises(ImportConflictError, match="stale"):
        service.correct_row(
            events.event.id,
            batch.id,
            row.id,
            administrator_id=events.event.created_by,
            expected_version=9,
            values={"email": "fixed@example.test"},
        )
    corrected = service.correct_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
        expected_version=1,
        values={"email": " FIXED@EXAMPLE.TEST "},
    )
    assert corrected.status == ImportRowStatus.PENDING_REVALIDATION
    assert corrected.current_values["email"] == "fixed@example.test"
    assert corrected.original_values == original
    assert corrected.original_validation_errors[0]["code"] == "FORMULA_NOT_ALLOWED"
    assert corrected.validation_errors == []
    assert corrected.row_version == 2
    assert len(imports.revisions) == 1

    with pytest.raises(ImportConflictError, match="only valid, invalid, or duplicate"):
        service.correct_row(
            events.event.id,
            batch.id,
            row.id,
            administrator_id=events.event.created_by,
            expected_version=2,
            values={"email": "another@example.test"},
        )


def test_valid_unlinked_correction_requires_revalidation_and_preserves_restore_history() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event, status=ImportBatchStatus.PARTIALLY_IMPORTED)
    original = guest_values("Editable Preview", email="editable@example.test", companions=1)
    imported = make_staged_row(
        batch,
        2,
        guest_values("Already Imported", email="imported@example.test"),
        status=ImportRowStatus.IMPORTED,
    )
    imported_guest_id = uuid4()
    imported.guest_id = imported_guest_id
    target = make_staged_row(batch, 3, original, status=ImportRowStatus.VALID)
    batch.candidate_row_count = 2
    imports.add_batch(batch, [imported, target])

    first = service.correct_row(
        events.event.id,
        batch.id,
        target.id,
        administrator_id=events.event.created_by,
        expected_version=1,
        values={"allowed_companions": 3},
    )

    assert first.status == ImportRowStatus.PENDING_REVALIDATION
    assert first.row_version == 2
    assert first.current_values["allowed_companions"] == 3
    assert first.original_values == original
    assert first.guest_id is None
    assert batch.status == ImportBatchStatus.PARTIALLY_IMPORTED
    assert len(imports.revisions) == 1
    assert imports.revisions[0].kind == ImportRevisionKind.CORRECTION

    with pytest.raises(ImportConflictError, match="non-progressable"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[target.id],
        )
    assert guests.items == []
    assert target.guest_id is None

    invalid = service.revalidate_row(
        events.event.id,
        batch.id,
        target.id,
        administrator_id=events.event.created_by,
    )
    assert invalid.status == ImportRowStatus.INVALID
    assert invalid.row_version == 3
    assert [item["code"] for item in invalid.validation_errors] == ["COMPANION_LIMIT_EXCEEDED"]
    assert [revision.kind for revision in imports.revisions] == [
        ImportRevisionKind.CORRECTION,
        ImportRevisionKind.REVALIDATION,
    ]

    with pytest.raises(ImportConflictError, match="stale"):
        service.correct_row(
            events.event.id,
            batch.id,
            target.id,
            administrator_id=events.event.created_by,
            expected_version=1,
            values={"allowed_companions": original["allowed_companions"]},
        )
    assert target.row_version == 3
    assert len(imports.revisions) == 2

    restored = service.correct_row(
        events.event.id,
        batch.id,
        target.id,
        administrator_id=events.event.created_by,
        expected_version=3,
        values={"allowed_companions": original["allowed_companions"]},
    )
    assert restored.status == ImportRowStatus.PENDING_REVALIDATION
    assert restored.row_version == 4
    assert restored.current_values == original

    final = service.revalidate_row(
        events.event.id,
        batch.id,
        target.id,
        administrator_id=events.event.created_by,
    )
    assert final.status == ImportRowStatus.VALID
    assert final.validation_errors == []
    assert final.row_version == 5
    assert final.current_values == original
    assert final.original_values == original
    assert final.guest_id is None
    assert imported.status == ImportRowStatus.IMPORTED
    assert imported.guest_id == imported_guest_id
    assert batch.status == ImportBatchStatus.PARTIALLY_IMPORTED
    assert [revision.kind for revision in imports.revisions] == [
        ImportRevisionKind.CORRECTION,
        ImportRevisionKind.REVALIDATION,
        ImportRevisionKind.CORRECTION,
        ImportRevisionKind.REVALIDATION,
    ]


def test_valid_to_valid_correction_still_invalidates_prior_validation_evidence() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    row = make_staged_row(
        batch,
        2,
        guest_values("Still Valid", email="still-valid@example.test"),
        status=ImportRowStatus.VALID,
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])

    service.correct_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
        expected_version=1,
        values={"category": "Synthetic Category"},
    )
    assert row.status == ImportRowStatus.PENDING_REVALIDATION
    with pytest.raises(ImportConflictError, match="non-progressable"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[row.id],
        )
    assert guests.items == []

    service.revalidate_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
    )
    assert row.status == ImportRowStatus.VALID
    assert row.validation_errors == []
    assert row.guest_id is None


def test_valid_unlinked_noop_correction_does_not_advance_history() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    row = make_staged_row(
        batch,
        2,
        guest_values("No-op Preview", email="noop@example.test", companions=1),
        status=ImportRowStatus.VALID,
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])

    with pytest.raises(ImportConflictError, match="change at least one"):
        service.correct_row(
            events.event.id,
            batch.id,
            row.id,
            administrator_id=events.event.created_by,
            expected_version=1,
            values={"allowed_companions": 1},
        )

    assert row.status is ImportRowStatus.VALID
    assert row.row_version == 1
    assert imports.revisions == []


def test_duplicate_unlinked_row_correction_regression_revalidates_to_valid() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    original = guest_values("Duplicate Preview", email="duplicate@example.test")
    row = make_staged_row(
        batch,
        2,
        original,
        status=ImportRowStatus.DUPLICATE,
        errors=[error(None, "DUPLICATE_IN_WORKBOOK", "duplicate")],
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])

    service.correct_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
        expected_version=1,
        values={"email": "unique@example.test"},
    )
    assert row.status is ImportRowStatus.PENDING_REVALIDATION
    assert row.original_values == original

    service.revalidate_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
    )
    assert row.status is ImportRowStatus.VALID
    assert row.validation_errors == []
    assert row.row_version == 3
    assert row.guest_id is None


@pytest.mark.parametrize("status", [ImportRowStatus.IMPORTED, ImportRowStatus.VALID])
def test_imported_or_guest_linked_rows_cannot_be_corrected(status: ImportRowStatus) -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event, status=ImportBatchStatus.PARTIALLY_IMPORTED)
    row = make_staged_row(
        batch,
        2,
        guest_values("Protected", email="protected@example.test"),
        status=status,
    )
    row.guest_id = uuid4()
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])
    original = dict(row.current_values)

    with pytest.raises(ImportConflictError, match="imported or guest-linked"):
        service.correct_row(
            events.event.id,
            batch.id,
            row.id,
            administrator_id=events.event.created_by,
            expected_version=1,
            values={"allowed_companions": 2},
        )

    assert row.current_values == original
    assert row.row_version == 1
    assert imports.revisions == []


def test_unfixed_normalization_error_survives_correction_and_revalidation() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    row = make_staged_row(
        batch,
        2,
        guest_values("Synthetic", email=None),
        status=ImportRowStatus.INVALID,
        errors=[error("email", "FORMULA_NOT_ALLOWED", "formula")],
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])
    service.correct_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
        expected_version=1,
        values={"full_name": "Synthetic Corrected"},
    )
    assert row.validation_errors[0]["code"] == "FORMULA_NOT_ALLOWED"

    service.revalidate_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
    )
    assert row.status == ImportRowStatus.INVALID
    assert row.validation_errors[0]["code"] == "FORMULA_NOT_ALLOWED"
    assert row.row_version == 3


def test_revalidation_can_become_valid_remain_invalid_or_become_duplicate() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    valid_row = make_staged_row(
        batch,
        2,
        guest_values("Corrected Valid", email="valid@example.test"),
        status=ImportRowStatus.PENDING_REVALIDATION,
    )
    invalid_row = make_staged_row(
        batch,
        3,
        guest_values("Still Invalid", email="bad-email"),
        status=ImportRowStatus.PENDING_REVALIDATION,
    )
    duplicate_row = make_staged_row(
        batch,
        4,
        guest_values("Duplicate", email="exists@example.test"),
        status=ImportRowStatus.PENDING_REVALIDATION,
    )
    batch.candidate_row_count = 3
    imports.add_batch(batch, [valid_row, invalid_row, duplicate_row])
    guests.items.append(
        Guest(
            id=uuid4(),
            event_id=events.event.id,
            full_name="Existing",
            email="exists@example.test",
            invitation_type="Family",
            allowed_companions=0,
        )
    )

    rows = service.revalidate_pending_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
    )
    assert [row.status for row in rows] == [
        ImportRowStatus.VALID,
        ImportRowStatus.INVALID,
        ImportRowStatus.DUPLICATE,
    ]
    assert len(imports.revisions) == 3


def test_batch_revalidation_reserves_first_newly_valid_identity() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    first = make_staged_row(
        batch,
        2,
        guest_values("First", email="same@example.test"),
        status=ImportRowStatus.PENDING_REVALIDATION,
    )
    second = make_staged_row(
        batch,
        3,
        guest_values("Second", email="same@example.test"),
        status=ImportRowStatus.PENDING_REVALIDATION,
    )
    batch.candidate_row_count = 2
    imports.add_batch(batch, [first, second])

    service.revalidate_pending_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
    )
    assert first.status == ImportRowStatus.VALID
    assert second.status == ImportRowStatus.DUPLICATE


def test_revalidation_ignores_valid_identity_from_another_unconfirmed_batch() -> None:
    repeated_values = guest_values("Synthetic Retry", email="retry@example.test")
    parsed = parsed_workbook(parsed_row(2, repeated_values))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    historical_batch = make_batch(events.event)
    historical_row = make_staged_row(
        historical_batch,
        2,
        repeated_values,
        status=ImportRowStatus.VALID,
    )
    historical_batch.candidate_row_count = 1
    imports.add_batch(historical_batch, [historical_row])
    retry_batch = make_batch(events.event)
    retry_row = make_staged_row(
        retry_batch,
        2,
        repeated_values,
        status=ImportRowStatus.PENDING_REVALIDATION,
    )
    retry_batch.candidate_row_count = 1
    imports.add_batch(retry_batch, [retry_row])

    service.revalidate_row(
        events.event.id,
        retry_batch.id,
        retry_row.id,
        administrator_id=events.event.created_by,
    )

    assert retry_row.status == ImportRowStatus.VALID
    assert retry_row.validation_errors == []
    assert historical_row.status == ImportRowStatus.VALID


def test_partial_progression_imports_valid_once_and_isolates_other_rows() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    good = make_staged_row(
        batch,
        2,
        guest_values("Good", email="good@example.test"),
        status=ImportRowStatus.VALID,
    )
    invalid_now = make_staged_row(
        batch,
        3,
        guest_values("Policy Invalid", companions=3),
        status=ImportRowStatus.VALID,
    )
    duplicate_now = make_staged_row(
        batch,
        4,
        guest_values("Duplicate", email="exists@example.test"),
        status=ImportRowStatus.VALID,
    )
    persistence_failure = make_staged_row(
        batch,
        5,
        guest_values("Persistence Failure", email="failure@example.test"),
        status=ImportRowStatus.VALID,
    )
    batch.candidate_row_count = 4
    imports.add_batch(batch, [good, invalid_now, duplicate_now, persistence_failure])
    guests.items.append(
        Guest(
            id=uuid4(),
            event_id=events.event.id,
            full_name="Existing",
            email="exists@example.test",
            invitation_type="Family",
            allowed_companions=0,
        )
    )
    imports.fail_flush_for_name = "Persistence Failure"

    result = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
    )

    assert result == {"imported_rows": 1, "invalidated_rows": 2, "duplicate_rows": 1}
    assert good.status == ImportRowStatus.IMPORTED
    assert good.guest_id is not None
    assert invalid_now.status == ImportRowStatus.INVALID
    assert duplicate_now.status == ImportRowStatus.DUPLICATE
    assert persistence_failure.status == ImportRowStatus.INVALID
    assert {guest.full_name for guest in guests.items} == {"Existing", "Good"}
    assert batch.status == ImportBatchStatus.PARTIALLY_IMPORTED

    repeated = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
    )
    assert repeated == {"imported_rows": 0, "invalidated_rows": 0, "duplicate_rows": 0}
    assert len(guests.items) == 2


def test_explicit_progression_preserves_exact_set_continuation_fields_and_idempotency() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    rows = [
        make_staged_row(
            batch,
            source_row,
            {
                "full_name": f"Selected {source_row}",
                "phone_number": f"+25570000{source_row:04d}",
                "email": f"selected-{source_row}@example.test",
                "invitation_type": "Family" if source_row % 2 else "Individual",
                "allowed_companions": source_row % 3,
                "table_number": f"T-{source_row}",
                "category": "Friends",
            },
            status=ImportRowStatus.VALID,
        )
        for source_row in range(2, 8)
    ]
    batch.candidate_row_count = len(rows)
    imports.add_batch(batch, rows)

    first = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
        staged_row_ids=[rows[0].id, rows[2].id],
    )

    assert first == {"imported_rows": 2, "invalidated_rows": 0, "duplicate_rows": 0}
    assert [row.status for row in rows] == [
        ImportRowStatus.IMPORTED,
        ImportRowStatus.VALID,
        ImportRowStatus.IMPORTED,
        ImportRowStatus.VALID,
        ImportRowStatus.VALID,
        ImportRowStatus.VALID,
    ]
    assert all(row.row_version == 1 for row in rows)
    assert imports.revisions == []
    assert batch.status == ImportBatchStatus.PARTIALLY_IMPORTED
    assert service.reconcile(events.event.id, batch.id)["currently_valid_staged_rows"] == 4
    persisted = {guest.full_name: guest for guest in guests.items}
    assert persisted["Selected 2"].allowed_companions == 2
    assert persisted["Selected 2"].table_number == "T-2"
    assert persisted["Selected 4"].category == "Friends"

    mixed = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
        staged_row_ids=[rows[0].id, rows[3].id],
    )
    repeated = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
        staged_row_ids=[rows[0].id, rows[3].id],
    )
    continuation = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
    )

    assert mixed["imported_rows"] == 1
    assert repeated == {"imported_rows": 0, "invalidated_rows": 0, "duplicate_rows": 0}
    assert continuation["imported_rows"] == 3
    assert len(guests.items) == 6
    assert all(row.status == ImportRowStatus.IMPORTED for row in rows)
    assert batch.status == ImportBatchStatus.RECONCILED


def test_explicit_progression_rejects_entire_invalid_selection_before_mutation() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    eligible = make_staged_row(
        batch,
        2,
        guest_values("Eligible", email="eligible@example.test"),
        status=ImportRowStatus.VALID,
    )
    invalid = make_staged_row(
        batch,
        3,
        guest_values("Invalid", invitation_type=None),
        status=ImportRowStatus.INVALID,
    )
    batch.candidate_row_count = 2
    imports.add_batch(batch, [eligible, invalid])
    foreign_batch = make_batch(events.event)
    foreign = make_staged_row(
        foreign_batch,
        2,
        guest_values("Foreign"),
        status=ImportRowStatus.VALID,
    )
    imports.add_batch(foreign_batch, [foreign])

    with pytest.raises(ImportConflictError, match="non-progressable"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[eligible.id, invalid.id],
        )
    with pytest.raises(ImportResourceNotFoundError, match="selected staged import row"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[eligible.id, foreign.id],
        )
    with pytest.raises(ImportResourceNotFoundError, match="selected staged import row"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[eligible.id, uuid4()],
        )
    with pytest.raises(ImportConflictError, match="must not be empty"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[],
        )
    with pytest.raises(ImportConflictError, match="must not contain duplicates"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[eligible.id, eligible.id],
        )

    assert guests.items == []
    assert eligible.status == ImportRowStatus.VALID
    assert eligible.guest_id is None
    assert imports.revisions == []


def test_explicit_progression_preflights_stale_valid_rows_before_mutation() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    eligible = make_staged_row(
        batch,
        2,
        guest_values("Eligible", email="eligible@example.test"),
        status=ImportRowStatus.VALID,
    )
    stale_policy = make_staged_row(
        batch,
        3,
        guest_values("Stale Policy", companions=3),
        status=ImportRowStatus.VALID,
    )
    batch.candidate_row_count = 2
    imports.add_batch(batch, [eligible, stale_policy])

    with pytest.raises(ImportConflictError, match="no longer eligible"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
            staged_row_ids=[eligible.id, stale_policy.id],
        )

    assert guests.items == []
    assert eligible.status == stale_policy.status == ImportRowStatus.VALID
    assert eligible.guest_id is stale_policy.guest_id is None
    assert imports.revisions == []


def test_newly_valid_corrected_row_imports_exactly_once_and_reconciles() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, guests, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    row = make_staged_row(
        batch,
        2,
        guest_values("Correct Me", invitation_type=None),
        status=ImportRowStatus.INVALID,
        errors=[error("invitation_type", "REQUIRED", "required")],
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])

    service.correct_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
        expected_version=1,
        values={"invitation_type": "Individual"},
    )
    service.revalidate_row(
        events.event.id,
        batch.id,
        row.id,
        administrator_id=events.event.created_by,
    )
    assert row.status == ImportRowStatus.VALID

    first = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
    )
    second = service.progress_valid_rows(
        events.event.id,
        batch.id,
        administrator_id=events.event.created_by,
    )
    assert first["imported_rows"] == 1
    assert second["imported_rows"] == 0
    assert len(guests.items) == 1
    assert batch.status == ImportBatchStatus.RECONCILED
    assert service.reconcile(events.event.id, batch.id)["remaining_unresolved_rows"] == 0


def test_correction_and_progression_reject_wrong_scope_or_terminal_event() -> None:
    parsed = parsed_workbook(parsed_row(2, guest_values("Synthetic")))
    service, events, _, imports, _, _ = build_service(parsed)
    assert events.event is not None
    batch = make_batch(events.event)
    row = make_staged_row(
        batch,
        2,
        guest_values("Synthetic"),
        status=ImportRowStatus.INVALID,
    )
    batch.candidate_row_count = 1
    imports.add_batch(batch, [row])
    with pytest.raises(ImportResourceNotFoundError):
        service.correct_row(
            uuid4(),
            batch.id,
            row.id,
            administrator_id=events.event.created_by,
            expected_version=1,
            values={"full_name": "Changed"},
        )

    events.event.status = EventStatus.COMPLETED
    with pytest.raises(ImportConflictError, match="immutable"):
        service.progress_valid_rows(
            events.event.id,
            batch.id,
            administrator_id=events.event.created_by,
        )
