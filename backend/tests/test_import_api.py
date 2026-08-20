"""Authenticated thin-route and privacy tests for the complete M4 API surface."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.dependencies import (
    get_authentication_service,
    get_current_administrator,
    get_import_service,
)
from app.core.settings import Settings
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.models.imports import ImportBatch, ImportBatchRow, ImportBatchStatus, ImportRowStatus
from app.services.imports import (
    ImportConflictError,
    ImportResourceNotFoundError,
    ImportUploadRejectedError,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class FakeDatabaseRuntime:
    def close(self) -> None:
        return None


class RejectIfCalledAuthenticationService:
    def current_administrator(self, _: str) -> Administrator:
        raise AssertionError("missing authentication must not reach token decoding")


def make_administrator() -> Administrator:
    return Administrator(
        id=uuid4(),
        username="admin",
        password_hash="$argon2id$synthetic",
        role=AdministratorRole.SUPER_ADMIN,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def make_batch(event_id: UUID, administrator_id: UUID) -> ImportBatch:
    return ImportBatch(
        id=uuid4(),
        event_id=event_id,
        uploaded_by=administrator_id,
        workbook_sha256="b" * 64,
        workbook_byte_size=123,
        storage_key="private/server/path-must-not-be-returned.xlsx",
        detected_sheet_name="Guests",
        header_row_number=4,
        column_mapping_snapshot={"full_name": 1, "invitation_type": 2},
        structure_snapshot={"ignored_rows": [{"row_number": 1, "reason": "PRESENTATION"}]},
        guest_field_snapshot=[
            {"field_key": "full_name", "enabled": True, "required": True, "sort_order": 0},
            {
                "field_key": "invitation_type",
                "enabled": True,
                "required": True,
                "sort_order": 3,
            },
        ],
        max_companions_snapshot=2,
        candidate_row_count=1,
        ignored_row_count=1,
        status=ImportBatchStatus.VALIDATED,
        created_at=NOW,
        updated_at=NOW,
    )


def make_row(batch_id: UUID) -> ImportBatchRow:
    values: dict[str, str | int | None] = {
        "full_name": "Synthetic Guest",
        "phone_number": None,
        "email": "bad-email",
        "invitation_type": "Family",
        "allowed_companions": 0,
        "table_number": None,
        "category": None,
    }
    errors = [{"field": "email", "code": "INVALID_EMAIL", "message": "email is invalid"}]
    return ImportBatchRow(
        id=uuid4(),
        batch_id=batch_id,
        source_row_number=5,
        original_values=dict(values),
        original_validation_errors=list(errors),
        current_values=dict(values),
        validation_errors=list(errors),
        identity_fingerprints=[],
        status=ImportRowStatus.INVALID,
        row_version=1,
        guest_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeImportService:
    def __init__(self, event_id: UUID, administrator_id: UUID) -> None:
        self.batch = make_batch(event_id, administrator_id)
        self.row = make_row(self.batch.id)
        self.calls: list[tuple[str, Any]] = []
        self.next_error: Exception | None = None

    def _raise_if_requested(self) -> None:
        if self.next_error is not None:
            requested = self.next_error
            self.next_error = None
            raise requested

    def upload_workbook(self, event_id: UUID, **kwargs: Any) -> ImportBatch:
        self._raise_if_requested()
        self.calls.append(("upload", (event_id, kwargs)))
        return self.batch

    def list_batches(self, event_id: UUID, *, limit: int, offset: int) -> list[ImportBatch]:
        self._raise_if_requested()
        self.calls.append(("history", (event_id, limit, offset)))
        return [self.batch]

    def get_batch(self, event_id: UUID, batch_id: UUID) -> ImportBatch:
        self._raise_if_requested()
        self.calls.append(("detail", (event_id, batch_id)))
        return self.batch

    def list_rows(
        self,
        event_id: UUID,
        batch_id: UUID,
        *,
        row_status: ImportRowStatus | None,
        limit: int,
        offset: int,
    ) -> list[ImportBatchRow]:
        self._raise_if_requested()
        self.calls.append(("rows", (event_id, batch_id, row_status, limit, offset)))
        return [self.row]

    def reconcile(self, event_id: UUID, batch_id: UUID) -> dict[str, int | str]:
        self._raise_if_requested()
        self.calls.append(("reconcile", (event_id, batch_id)))
        imported = 1 if self.row.status == ImportRowStatus.IMPORTED else 0
        valid = 1 if self.row.status == ImportRowStatus.VALID else 0
        invalid = 1 if self.row.status == ImportRowStatus.INVALID else 0
        duplicate = 1 if self.row.status == ImportRowStatus.DUPLICATE else 0
        pending = 1 if self.row.status == ImportRowStatus.PENDING_REVALIDATION else 0
        return {
            "actual_guest_rows_discovered": 1,
            "ignored_non_data_rows": 1,
            "currently_valid_staged_rows": valid,
            "currently_invalid_staged_rows": invalid,
            "duplicates": duplicate,
            "pending_revalidation": pending,
            "rows_already_imported": imported,
            "newly_importable_rows": valid,
            "remaining_unresolved_rows": invalid + duplicate + pending,
            "guests_created_from_batch": imported,
            "overall_batch_state": self.batch.status.value,
        }

    def reconcile_batch(self, batch: ImportBatch) -> dict[str, int | str]:
        return self.reconcile(batch.event_id, batch.id)

    def correct_row(self, event_id: UUID, batch_id: UUID, row_id: UUID, **kwargs: Any):
        self._raise_if_requested()
        self.calls.append(("correct", (event_id, batch_id, row_id, kwargs)))
        self.row.current_values.update(kwargs["values"])
        self.row.status = ImportRowStatus.PENDING_REVALIDATION
        self.row.validation_errors = []
        self.row.row_version += 1
        return self.row

    def revalidate_row(self, event_id: UUID, batch_id: UUID, row_id: UUID, **kwargs: Any):
        self._raise_if_requested()
        self.calls.append(("revalidate_row", (event_id, batch_id, row_id, kwargs)))
        self.row.status = ImportRowStatus.VALID
        self.row.row_version += 1
        return self.row

    def revalidate_pending_rows(self, event_id: UUID, batch_id: UUID, **kwargs: Any):
        self._raise_if_requested()
        self.calls.append(("revalidate_batch", (event_id, batch_id, kwargs)))
        self.row.status = ImportRowStatus.VALID
        self.row.row_version += 1
        return [self.row]

    def progress_valid_rows(self, event_id: UUID, batch_id: UUID, **kwargs: Any):
        self._raise_if_requested()
        self.calls.append(("progress", (event_id, batch_id, kwargs)))
        self.row.status = ImportRowStatus.IMPORTED
        self.row.guest_id = uuid4()
        self.batch.status = ImportBatchStatus.RECONCILED
        return {"imported_rows": 1, "invalidated_rows": 0, "duplicate_rows": 0}


def build_app(
    settings: Settings,
    *,
    authenticated: bool = True,
) -> tuple[object, FakeImportService, Administrator, UUID]:
    administrator = make_administrator()
    event_id = uuid4()
    service = FakeImportService(event_id, administrator.id)
    app = create_app(settings, database_runtime=FakeDatabaseRuntime())  # type: ignore[arg-type]
    app.dependency_overrides[get_import_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_administrator] = lambda: administrator
    else:
        app.dependency_overrides[get_authentication_service] = lambda: (
            RejectIfCalledAuthenticationService()
        )
    return app, service, administrator, event_id


async def request(app: object, method: str, path: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        return await client.request(method, path, **kwargs)


async def request_many(
    app: object,
    requests: list[tuple[str, str]],
) -> list[httpx.Response]:
    return [await request(app, method, path) for method, path in requests]


def test_authenticated_upload_is_multipart_bounded_and_hides_storage_path(
    development_settings: Settings,
) -> None:
    app, service, administrator, event_id = build_app(development_settings)
    response = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{event_id}/imports",
            files={
                "workbook": (
                    "synthetic.xlsx",
                    b"synthetic workbook",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(service.batch.id)
    assert body["reconciliation"]["actual_guest_rows_discovered"] == 1
    assert "storage_key" not in body
    assert "private/server" not in response.text
    upload_call = next(call for call in service.calls if call[0] == "upload")
    assert upload_call[1][1]["administrator_id"] == administrator.id
    assert upload_call[1][1]["content"] == b"synthetic workbook"


def test_history_detail_rows_and_reconciliation_are_bounded(
    development_settings: Settings,
) -> None:
    app, service, _, event_id = build_app(development_settings)
    batch_id = service.batch.id
    responses = asyncio.run(
        request_many(
            app,
            [
                ("GET", f"/api/v1/events/{event_id}/imports?limit=10&offset=2"),
                ("GET", f"/api/v1/events/{event_id}/imports/{batch_id}"),
                (
                    "GET",
                    f"/api/v1/events/{event_id}/imports/{batch_id}/rows"
                    "?status=INVALID&limit=20&offset=3",
                ),
                (
                    "GET",
                    f"/api/v1/events/{event_id}/imports/{batch_id}/reconciliation",
                ),
            ],
        )
    )
    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert responses[0].json()["limit"] == 10
    assert responses[2].json()["items"][0]["source_row_number"] == 5
    assert responses[2].json()["items"][0]["validation_errors"][0]["code"] == "INVALID_EMAIL"
    assert (
        "rows",
        (event_id, batch_id, ImportRowStatus.INVALID, 20, 3),
    ) in service.calls


def test_correction_revalidation_and_progression_routes_are_explicit(
    development_settings: Settings,
) -> None:
    app, service, _, event_id = build_app(development_settings)
    batch_id = service.batch.id
    row_id = service.row.id

    corrected = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/v1/events/{event_id}/imports/{batch_id}/rows/{row_id}",
            json={"expected_version": 1, "values": {"email": "fixed@example.test"}},
        )
    )
    revalidated = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{event_id}/imports/{batch_id}/rows/{row_id}/revalidate",
        )
    )
    service.row.status = ImportRowStatus.PENDING_REVALIDATION
    batch_revalidated = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{event_id}/imports/{batch_id}/revalidate",
        )
    )
    progressed = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{event_id}/imports/{batch_id}/progress",
        )
    )

    assert corrected.status_code == revalidated.status_code == 200
    assert corrected.json()["status"] == "PENDING_REVALIDATION"
    assert revalidated.json()["status"] == "VALID"
    assert batch_revalidated.status_code == 200
    assert progressed.status_code == 200
    assert progressed.json()["imported_rows"] == 1
    assert progressed.json()["reconciliation"]["overall_batch_state"] == "RECONCILED"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"expected_version": 1, "values": {}},
        {"expected_version": 1, "values": {"source_row_number": 99}},
        {"expected_version": 1, "values": {"allowed_companions": True}},
        {"expected_version": 1, "values": {"allowed_companions": None}},
        {"expected_version": 0, "values": {"email": "fixed@example.test"}},
    ],
)
def test_correction_schema_rejects_immutable_unknown_and_stale_shapes(
    development_settings: Settings,
    payload: dict[str, Any],
) -> None:
    app, service, _, event_id = build_app(development_settings)
    response = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/v1/events/{event_id}/imports/{service.batch.id}/rows/{service.row.id}",
            json=payload,
        )
    )
    assert response.status_code == 422
    assert not any(call[0] == "correct" for call in service.calls)


def test_missing_authentication_rejects_import_routes_before_service(
    development_settings: Settings,
) -> None:
    app, service, _, event_id = build_app(development_settings, authenticated=False)
    response = asyncio.run(request(app, "GET", f"/api/v1/events/{event_id}/imports"))
    assert response.status_code == 401
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (ImportResourceNotFoundError("import batch not found"), 404, None),
        (ImportConflictError("staged row version is stale"), 409, None),
        (
            ImportUploadRejectedError("MALFORMED_WORKBOOK", "safe rejection"),
            422,
            "MALFORMED_WORKBOOK",
        ),
        (
            ImportUploadRejectedError("WORKBOOK_TOO_LARGE", "safe rejection"),
            413,
            "WORKBOOK_TOO_LARGE",
        ),
    ],
)
def test_domain_errors_are_bounded_without_internal_paths(
    development_settings: Settings,
    error: Exception,
    expected_status: int,
    expected_code: str | None,
) -> None:
    app, service, _, event_id = build_app(development_settings)
    service.next_error = error
    response = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/events/{event_id}/imports",
            files={"workbook": ("synthetic.xlsx", b"x", "application/octet-stream")},
        )
    )
    assert response.status_code == expected_status
    assert "private/server" not in response.text
    assert "traceback" not in response.text.casefold()
    if expected_code is not None:
        assert response.json()["detail"]["code"] == expected_code
