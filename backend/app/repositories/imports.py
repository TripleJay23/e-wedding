"""Bounded event-scoped persistence operations for M4 Excel imports."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransaction

from app.models.guest import Guest
from app.models.imports import (
    ImportBatch,
    ImportBatchRow,
    ImportRowRevision,
    ImportRowStatus,
)


class ImportRepository:
    """Keep import queries event-scoped, paginated, and relationship-free."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_batch(self, batch: ImportBatch, rows: Sequence[ImportBatchRow]) -> None:
        self._session.add(batch)
        self._session.add_all(rows)

    def add_revision(self, revision: ImportRowRevision) -> None:
        self._session.add(revision)

    def get_batch(
        self,
        event_id: UUID,
        batch_id: UUID,
        *,
        for_update: bool = False,
    ) -> ImportBatch | None:
        statement: Select[tuple[ImportBatch]] = select(ImportBatch).where(
            ImportBatch.id == batch_id,
            ImportBatch.event_id == event_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.execute(statement).scalar_one_or_none()

    def get_row(
        self,
        event_id: UUID,
        batch_id: UUID,
        row_id: UUID,
        *,
        for_update: bool = False,
    ) -> ImportBatchRow | None:
        statement: Select[tuple[ImportBatchRow]] = (
            select(ImportBatchRow)
            .join(ImportBatch, ImportBatch.id == ImportBatchRow.batch_id)
            .where(
                ImportBatch.event_id == event_id,
                ImportBatch.id == batch_id,
                ImportBatchRow.id == row_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=ImportBatchRow)
        return self._session.execute(statement).scalar_one_or_none()

    def list_batches(self, event_id: UUID, *, limit: int, offset: int) -> list[ImportBatch]:
        statement = (
            select(ImportBatch)
            .where(ImportBatch.event_id == event_id)
            .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def list_rows(
        self,
        batch_id: UUID,
        *,
        row_status: ImportRowStatus | None,
        limit: int,
        offset: int,
    ) -> list[ImportBatchRow]:
        statement: Select[tuple[ImportBatchRow]] = select(ImportBatchRow).where(
            ImportBatchRow.batch_id == batch_id
        )
        if row_status is not None:
            statement = statement.where(ImportBatchRow.status == row_status)
        statement = (
            statement.order_by(ImportBatchRow.source_row_number, ImportBatchRow.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def rows_for_reconciliation(self, batch_id: UUID) -> list[ImportBatchRow]:
        statement = (
            select(ImportBatchRow)
            .where(ImportBatchRow.batch_id == batch_id)
            .order_by(ImportBatchRow.source_row_number, ImportBatchRow.id)
        )
        return list(self._session.execute(statement).scalars())

    def eligible_rows_for_update(self, batch_id: UUID) -> list[ImportBatchRow]:
        statement = (
            select(ImportBatchRow)
            .where(
                ImportBatchRow.batch_id == batch_id,
                ImportBatchRow.status == ImportRowStatus.VALID,
            )
            .order_by(ImportBatchRow.source_row_number, ImportBatchRow.id)
            .with_for_update()
        )
        return list(self._session.execute(statement).scalars())

    def pending_rows_for_update(self, batch_id: UUID) -> list[ImportBatchRow]:
        statement = (
            select(ImportBatchRow)
            .where(
                ImportBatchRow.batch_id == batch_id,
                ImportBatchRow.status == ImportRowStatus.PENDING_REVALIDATION,
            )
            .order_by(ImportBatchRow.source_row_number, ImportBatchRow.id)
            .with_for_update()
        )
        return list(self._session.execute(statement).scalars())

    def valid_staged_fingerprints(
        self,
        batch_id: UUID,
        *,
        exclude_row_id: UUID | None = None,
    ) -> list[list[str]]:
        statement = select(ImportBatchRow.identity_fingerprints).where(
            ImportBatchRow.batch_id == batch_id,
            ImportBatchRow.status == ImportRowStatus.VALID,
        )
        if exclude_row_id is not None:
            statement = statement.where(ImportBatchRow.id != exclude_row_id)
        return [list(value) for value in self._session.execute(statement).scalars()]

    def guest_identity_values(self, event_id: UUID) -> list[dict[str, object]]:
        statement = select(
            Guest.full_name,
            Guest.phone_number,
            Guest.email,
            Guest.invitation_type,
        ).where(Guest.event_id == event_id)
        return [dict(row._mapping) for row in self._session.execute(statement)]

    def status_counts(self, batch_id: UUID) -> dict[ImportRowStatus, int]:
        statement = (
            select(ImportBatchRow.status, func.count())
            .where(ImportBatchRow.batch_id == batch_id)
            .group_by(ImportBatchRow.status)
        )
        return dict(self._session.execute(statement).tuples().all())

    def savepoint(self) -> AbstractContextManager[SessionTransaction]:
        return self._session.begin_nested()

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
