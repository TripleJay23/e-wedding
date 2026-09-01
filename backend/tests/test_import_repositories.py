"""M4 repository event scoping, pagination, locking, and transaction tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.models.imports import (
    ImportBatch,
    ImportBatchRow,
    ImportBatchStatus,
    ImportRevisionKind,
    ImportRowRevision,
    ImportRowStatus,
)
from app.repositories.imports import ImportRepository

NOW = datetime(2026, 8, 20, tzinfo=UTC)


class FakeResult:
    def __init__(
        self,
        *,
        scalars: list[Any] | None = None,
        scalar: Any = None,
        rows: list[Any] | None = None,
    ) -> None:
        self._scalars = scalars or []
        self._scalar = scalar
        self._rows = rows or []

    def scalars(self) -> list[Any]:
        return self._scalars

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def __iter__(self):
        return iter(self._rows)

    def tuples(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.statements: list[Any] = []
        self.result = FakeResult()
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.transaction = SimpleNamespace(name="savepoint")

    def add(self, value: Any) -> None:
        self.added.append(value)

    def add_all(self, values: list[Any]) -> None:
        self.added.extend(values)

    def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return self.result

    def begin_nested(self) -> object:
        return self.transaction

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def make_batch() -> ImportBatch:
    return ImportBatch(
        id=uuid4(),
        event_id=uuid4(),
        uploaded_by=uuid4(),
        workbook_sha256="c" * 64,
        workbook_byte_size=100,
        storage_key="synthetic/key.xlsx",
        detected_sheet_name="Guests",
        header_row_number=1,
        column_mapping_snapshot={"full_name": 1, "invitation_type": 2},
        structure_snapshot={"ignored_rows": []},
        guest_field_snapshot=[],
        max_companions_snapshot=2,
        candidate_row_count=1,
        ignored_row_count=0,
        status=ImportBatchStatus.VALIDATED,
        created_at=NOW,
        updated_at=NOW,
    )


def make_row(batch: ImportBatch) -> ImportBatchRow:
    values = {
        "full_name": "Synthetic",
        "invitation_type": "Family",
        "allowed_companions": 0,
    }
    return ImportBatchRow(
        id=uuid4(),
        batch_id=batch.id,
        source_row_number=2,
        original_values=values,
        original_validation_errors=[],
        current_values=values,
        validation_errors=[],
        identity_fingerprints=["d" * 64],
        status=ImportRowStatus.VALID,
        row_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def test_import_repository_writes_and_event_scoped_reads() -> None:
    session = FakeSession()
    repository = ImportRepository(session)  # type: ignore[arg-type]
    batch = make_batch()
    row = make_row(batch)
    revision = ImportRowRevision(
        id=uuid4(),
        row_id=row.id,
        revision=2,
        kind=ImportRevisionKind.CORRECTION,
        changed_values={"email": "synthetic@example.test"},
        values_snapshot=row.current_values,
        validation_status=ImportRowStatus.PENDING_REVALIDATION,
        validation_errors=[],
        actor_id=batch.uploaded_by,
        created_at=NOW,
    )
    repository.add_batch(batch, [row])
    repository.add_revision(revision)
    assert session.added == [batch, row, revision]

    session.result = FakeResult(scalar=batch)
    assert repository.get_batch(batch.event_id, batch.id) is batch
    assert "import_batches.event_id" in str(session.statements[-1])
    assert repository.get_batch(batch.event_id, batch.id, for_update=True) is batch

    session.result = FakeResult(scalar=row)
    assert repository.get_row(batch.event_id, batch.id, row.id) is row
    detail_sql = str(session.statements[-1])
    assert "JOIN import_batches" in detail_sql
    assert "import_batch_rows.id" in detail_sql
    assert repository.get_row(batch.event_id, batch.id, row.id, for_update=True) is row


def test_import_repository_lists_are_bounded_and_deterministic() -> None:
    session = FakeSession()
    repository = ImportRepository(session)  # type: ignore[arg-type]
    batch = make_batch()
    row = make_row(batch)

    session.result = FakeResult(scalars=[batch])
    assert repository.list_batches(batch.event_id, limit=25, offset=5) == [batch]
    history_sql = str(session.statements[-1])
    assert "ORDER BY import_batches.created_at DESC, import_batches.id DESC" in history_sql
    assert "LIMIT" in history_sql
    assert "OFFSET" in history_sql

    session.result = FakeResult(scalars=[row])
    assert repository.list_rows(
        batch.id,
        row_status=None,
        limit=50,
        offset=0,
    ) == [row]
    assert "ORDER BY import_batch_rows.source_row_number" in str(session.statements[-1])
    assert repository.list_rows(
        batch.id,
        row_status=ImportRowStatus.VALID,
        limit=10,
        offset=3,
    ) == [row]
    assert "import_batch_rows.status" in str(session.statements[-1])

    assert repository.rows_for_reconciliation(batch.id) == [row]
    assert repository.eligible_rows_for_update(batch.id) == [row]
    assert repository.pending_rows_for_update(batch.id) == [row]


def test_import_repository_bulk_duplicate_and_count_queries() -> None:
    session = FakeSession()
    repository = ImportRepository(session)  # type: ignore[arg-type]
    batch = make_batch()
    row = make_row(batch)

    session.result = FakeResult(scalars=[["a" * 64], ["b" * 64]])
    assert repository.valid_staged_fingerprints(batch.id) == [
        ["a" * 64],
        ["b" * 64],
    ]
    staged_sql = str(session.statements[-1])
    assert "import_batch_rows.batch_id" in staged_sql
    assert "import_batches" not in staged_sql
    assert "import_batch_rows.status" in staged_sql
    repository.valid_staged_fingerprints(batch.id, exclude_row_id=row.id)
    assert "import_batch_rows.id !=" in str(session.statements[-1])

    guest_row = SimpleNamespace(
        _mapping={
            "full_name": "Synthetic",
            "phone_number": None,
            "email": None,
            "invitation_type": "Family",
        }
    )
    session.result = FakeResult(rows=[guest_row])
    assert repository.guest_identity_values(batch.event_id) == [dict(guest_row._mapping)]
    guest_sql = str(session.statements[-1])
    assert "guests.event_id" in guest_sql
    assert "guests.allowed_companions" not in guest_sql

    session.result = FakeResult(rows=[(ImportRowStatus.VALID, 2), (ImportRowStatus.INVALID, 1)])
    assert repository.status_counts(batch.id) == {
        ImportRowStatus.VALID: 2,
        ImportRowStatus.INVALID: 1,
    }
    assert "GROUP BY import_batch_rows.status" in str(session.statements[-1])


def test_import_repository_delegates_savepoint_flush_and_transaction() -> None:
    session = FakeSession()
    repository = ImportRepository(session)  # type: ignore[arg-type]
    assert repository.savepoint() is session.transaction
    repository.flush()
    repository.commit()
    repository.rollback()
    assert (session.flushes, session.commits, session.rollbacks) == (1, 1, 1)
