"""Opt-in disposable PostgreSQL M4 end-to-end import acceptance."""

from __future__ import annotations

import asyncio
import os
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator
from app.models.event import Event
from app.models.imports import ImportBatch, ImportBatchRow, ImportRowRevision, ImportRowStatus
from app.repositories.administrators import AdministratorRepository
from app.services.admin_provisioning import AdministratorProvisioningService

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
]


def synthetic_workbook() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Synthetic Guest List"
    worksheet.merge_cells("A1:E1")
    worksheet["A1"] = "Synthetic PostgreSQL Wedding Guests"
    worksheet.append([])
    worksheet.append(
        [
            "Unmanaged Notes",
            "Full Name",
            "Allowed Companions",
            "Invitation Type",
            "Email",
            "Phone",
            "Table Number",
        ]
    )
    worksheet.append([None, "Synthetic Valid One", 2, "Family", "one@example.test", None, 1])
    worksheet.append([None, "Synthetic Correctable", 0, None, "correct@example.test", None, 2.0])
    worksheet.append([None, "Synthetic Valid Two", 0, "Individual", None, "+255700000022", "A1"])
    worksheet.append([None, "Synthetic Duplicate", 1, "Family", "ONE@example.test", None, 12])
    worksheet.append([None, None, "=COUNTA(B4:B7)", "Grand Total", None, None, None])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def contextual_footer_workbook() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Synthetic Contextual Footer"
    worksheet.merge_cells("A1:D1")
    worksheet["A1"] = "Synthetic Wedding Guest Register"
    worksheet.merge_cells("A2:D2")
    worksheet["A2"] = "Synthetic Import Preview"
    worksheet.append([])
    worksheet.append(["Full Name", "Invitation Type", "Allowed Companions", "Category"])
    worksheet.append(["Synthetic Context One", "Family", 2, "Friends"])
    worksheet.append(["Synthetic Context Two", "Individual", 0, "Colleagues"])
    worksheet.append(["Synthetic Context Three", "Family", 1, None])
    worksheet.append([None, "Closing constellation reckoning", "=COUNTA(A5:A7)", None])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def partial_retry_workbook(*, corrected: bool) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Synthetic Partial Retry"
    worksheet.append(["Full Name", "Invitation Type", "Email", "Allowed Companions"])
    worksheet.append(["Synthetic Persisted", "Family", "persisted@example.test", 1])
    worksheet.append(
        [
            "Synthetic Preview Only",
            "Individual" if corrected else None,
            "preview-only@example.test",
            0,
        ]
    )
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def concurrent_retry_workbook() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Synthetic Concurrent Retry"
    worksheet.append(["Full Name", "Invitation Type", "Email", "Allowed Companions"])
    worksheet.append(["Synthetic Concurrent One", "Family", "concurrent-one@example.test", 2])
    worksheet.append(["Synthetic Concurrent Two", "Individual", "concurrent-two@example.test", 0])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def configure_test_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Settings:
    assert DISPOSABLE_URL is not None
    private_root = tmp_path / "private"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv("JWT_SECRET", "synthetic-jwt-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(private_root))
    monkeypatch.setenv("IMPORT_EVIDENCE_STORAGE_ROOT", str(private_root / "import-evidence"))
    return Settings()


async def exercise_import_api(
    settings: Settings,
    runtime: DatabaseRuntime,
    username: str,
) -> dict[str, str]:
    app = create_app(
        settings,
        database_runtime=runtime,
        authentication_security=AuthenticationSecurity.from_settings(settings),
    )
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "synthetic administrator password"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        event_payload = {
            "event_name": "Synthetic M4 PostgreSQL Wedding",
            "bride_name": "Amina",
            "groom_name": "Baraka",
            "event_datetime": "2027-06-12T14:30:00+03:00",
            "venue": "Synthetic Hall",
            "max_companions_per_invitation": 2,
        }
        created_event = await client.post("/api/v1/events", headers=headers, json=event_payload)
        assert created_event.status_code == 201, created_event.text
        event_id = created_event.json()["id"]

        upload = await client.post(
            f"/api/v1/events/{event_id}/imports",
            headers=headers,
            files={
                "workbook": (
                    "synthetic.xlsx",
                    synthetic_workbook(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        upload_body = upload.json()
        batch_id = upload_body["id"]
        reconciliation = upload_body["reconciliation"]
        assert reconciliation == {
            "actual_guest_rows_discovered": 4,
            "ignored_non_data_rows": 3,
            "currently_valid_staged_rows": 2,
            "currently_invalid_staged_rows": 1,
            "duplicates": 1,
            "pending_revalidation": 0,
            "rows_already_imported": 0,
            "newly_importable_rows": 2,
            "remaining_unresolved_rows": 2,
            "guests_created_from_batch": 0,
            "overall_batch_state": "VALIDATED",
        }
        assert "storage_key" not in upload_body
        assert "private" not in upload.text.casefold()

        staged_rows = await client.get(
            f"/api/v1/events/{event_id}/imports/{batch_id}/rows?limit=100&offset=0",
            headers=headers,
        )
        assert staged_rows.status_code == 200
        staged_items = staged_rows.json()["items"]
        assert [item["source_row_number"] for item in staged_items] == [4, 5, 6, 7]
        assert [item["current_values"]["table_number"] for item in staged_items] == [
            "1",
            "2",
            "A1",
            "12",
        ]
        assert all(item["original_values"] == item["current_values"] for item in staged_items)

        first_preview_snapshot = [
            {
                "id": item["id"],
                "status": item["status"],
                "original_values": item["original_values"],
                "validation_errors": item["validation_errors"],
            }
            for item in staged_items
        ]
        retry_batch_ids: list[str] = []
        for retry_number in (2, 3):
            retry = await client.post(
                f"/api/v1/events/{event_id}/imports",
                headers=headers,
                files={
                    "workbook": (
                        f"synthetic-retry-{retry_number}.xlsx",
                        synthetic_workbook(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert retry.status_code == 201, retry.text
            retry_body = retry.json()
            retry_batch_ids.append(retry_body["id"])
            assert retry_body["reconciliation"] == reconciliation
            retry_rows = await client.get(
                f"/api/v1/events/{event_id}/imports/{retry_body['id']}/rows?limit=100&offset=0",
                headers=headers,
            )
            assert retry_rows.status_code == 200
            retry_items = retry_rows.json()["items"]
            duplicate_codes = [
                error_item["code"]
                for item in retry_items
                for error_item in item["validation_errors"]
                if error_item["code"].startswith("DUPLICATE")
            ]
            assert duplicate_codes == ["DUPLICATE_IN_WORKBOOK"]

        unchanged_first_preview = await client.get(
            f"/api/v1/events/{event_id}/imports/{batch_id}/rows?limit=100&offset=0",
            headers=headers,
        )
        assert unchanged_first_preview.status_code == 200
        assert [
            {
                "id": item["id"],
                "status": item["status"],
                "original_values": item["original_values"],
                "validation_errors": item["validation_errors"],
            }
            for item in unchanged_first_preview.json()["items"]
        ] == first_preview_snapshot

        guests_before_progress = await client.get(
            f"/api/v1/events/{event_id}/guests?limit=100&offset=0",
            headers=headers,
        )
        assert guests_before_progress.status_code == 200
        assert guests_before_progress.json()["items"] == []

        contextual_event = await client.post(
            "/api/v1/events",
            headers=headers,
            json={**event_payload, "event_name": "Synthetic Contextual Footer Wedding"},
        )
        assert contextual_event.status_code == 201
        contextual_event_id = contextual_event.json()["id"]
        contextual_upload = await client.post(
            f"/api/v1/events/{contextual_event_id}/imports",
            headers=headers,
            files={
                "workbook": (
                    "synthetic-contextual-footer.xlsx",
                    contextual_footer_workbook(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert contextual_upload.status_code == 201, contextual_upload.text
        contextual_body = contextual_upload.json()
        contextual_batch_id = contextual_body["id"]
        assert contextual_body["reconciliation"] == {
            "actual_guest_rows_discovered": 3,
            "ignored_non_data_rows": 4,
            "currently_valid_staged_rows": 3,
            "currently_invalid_staged_rows": 0,
            "duplicates": 0,
            "pending_revalidation": 0,
            "rows_already_imported": 0,
            "newly_importable_rows": 3,
            "remaining_unresolved_rows": 0,
            "guests_created_from_batch": 0,
            "overall_batch_state": "VALIDATED",
        }
        contextual_rows = await client.get(
            f"/api/v1/events/{contextual_event_id}/imports/{contextual_batch_id}/rows",
            headers=headers,
        )
        assert contextual_rows.status_code == 200
        assert [item["source_row_number"] for item in contextual_rows.json()["items"]] == [
            5,
            6,
            7,
        ]
        assert all(item["validation_errors"] == [] for item in contextual_rows.json()["items"])
        contextual_guests = await client.get(
            f"/api/v1/events/{contextual_event_id}/guests?limit=100&offset=0",
            headers=headers,
        )
        assert contextual_guests.status_code == 200
        assert contextual_guests.json()["items"] == []

        first_progress = await client.post(
            f"/api/v1/events/{event_id}/imports/{batch_id}/progress",
            headers=headers,
        )
        assert first_progress.status_code == 200, first_progress.text
        assert first_progress.json()["imported_rows"] == 2
        assert first_progress.json()["reconciliation"]["rows_already_imported"] == 2

        invalid_rows = await client.get(
            f"/api/v1/events/{event_id}/imports/{batch_id}/rows?status=INVALID",
            headers=headers,
        )
        assert invalid_rows.status_code == 200
        invalid_row = invalid_rows.json()["items"][0]
        original_values = invalid_row["original_values"]
        row_id = invalid_row["id"]
        corrected = await client.patch(
            f"/api/v1/events/{event_id}/imports/{batch_id}/rows/{row_id}",
            headers=headers,
            json={
                "expected_version": invalid_row["row_version"],
                "values": {"invitation_type": "Family"},
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["status"] == "PENDING_REVALIDATION"
        assert corrected.json()["original_values"] == original_values

        revalidated = await client.post(
            f"/api/v1/events/{event_id}/imports/{batch_id}/rows/{row_id}/revalidate",
            headers=headers,
        )
        assert revalidated.status_code == 200, revalidated.text
        assert revalidated.json()["status"] == "VALID"

        second_progress = await client.post(
            f"/api/v1/events/{event_id}/imports/{batch_id}/progress",
            headers=headers,
        )
        repeated_progress = await client.post(
            f"/api/v1/events/{event_id}/imports/{batch_id}/progress",
            headers=headers,
        )
        assert second_progress.json()["imported_rows"] == 1
        assert repeated_progress.json()["imported_rows"] == 0
        assert repeated_progress.json()["reconciliation"]["rows_already_imported"] == 3
        assert repeated_progress.json()["reconciliation"]["duplicates"] == 1

        guests = await client.get(
            f"/api/v1/events/{event_id}/guests?limit=100&offset=0",
            headers=headers,
        )
        assert guests.status_code == 200
        assert len(guests.json()["items"]) == 3

        reupload = await client.post(
            f"/api/v1/events/{event_id}/imports",
            headers=headers,
            files={
                "workbook": (
                    "synthetic.xlsx",
                    synthetic_workbook(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert reupload.status_code == 201, reupload.text
        assert reupload.json()["reconciliation"]["duplicates"] == 3
        assert reupload.json()["reconciliation"]["currently_invalid_staged_rows"] == 1

        partial_event = await client.post(
            "/api/v1/events",
            headers=headers,
            json={**event_payload, "event_name": "Synthetic Partial Retry Wedding"},
        )
        assert partial_event.status_code == 201
        partial_event_id = partial_event.json()["id"]
        partial_preview = await client.post(
            f"/api/v1/events/{partial_event_id}/imports",
            headers=headers,
            files={
                "workbook": (
                    "synthetic-partial.xlsx",
                    partial_retry_workbook(corrected=False),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert partial_preview.status_code == 201, partial_preview.text
        partial_batch_id = partial_preview.json()["id"]
        assert partial_preview.json()["reconciliation"]["currently_valid_staged_rows"] == 1
        assert partial_preview.json()["reconciliation"]["currently_invalid_staged_rows"] == 1
        partial_progress = await client.post(
            f"/api/v1/events/{partial_event_id}/imports/{partial_batch_id}/progress",
            headers=headers,
        )
        assert partial_progress.status_code == 200, partial_progress.text
        assert partial_progress.json()["imported_rows"] == 1

        partial_retry = await client.post(
            f"/api/v1/events/{partial_event_id}/imports",
            headers=headers,
            files={
                "workbook": (
                    "synthetic-partial-retry.xlsx",
                    partial_retry_workbook(corrected=True),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert partial_retry.status_code == 201, partial_retry.text
        partial_retry_reconciliation = partial_retry.json()["reconciliation"]
        assert partial_retry_reconciliation["currently_valid_staged_rows"] == 1
        assert partial_retry_reconciliation["currently_invalid_staged_rows"] == 0
        assert partial_retry_reconciliation["duplicates"] == 1
        partial_retry_rows = await client.get(
            f"/api/v1/events/{partial_event_id}/imports/{partial_retry.json()['id']}/rows",
            headers=headers,
        )
        assert partial_retry_rows.status_code == 200
        partial_retry_items = partial_retry_rows.json()["items"]
        assert [item["status"] for item in partial_retry_items] == ["DUPLICATE", "VALID"]
        assert partial_retry_items[0]["validation_errors"][0]["code"] == (
            "DUPLICATE_EXISTING_GUEST"
        )
        assert partial_retry_items[1]["validation_errors"] == []
        partial_historical_rows = await client.get(
            f"/api/v1/events/{partial_event_id}/imports/{partial_batch_id}/rows",
            headers=headers,
        )
        assert partial_historical_rows.status_code == 200
        assert [item["status"] for item in partial_historical_rows.json()["items"]] == [
            "IMPORTED",
            "INVALID",
        ]
        partial_guests = await client.get(
            f"/api/v1/events/{partial_event_id}/guests?limit=100&offset=0",
            headers=headers,
        )
        assert partial_guests.status_code == 200
        assert len(partial_guests.json()["items"]) == 1

        concurrent_event = await client.post(
            "/api/v1/events",
            headers=headers,
            json={**event_payload, "event_name": "Synthetic Concurrent Retry Wedding"},
        )
        assert concurrent_event.status_code == 201
        concurrent_event_id = concurrent_event.json()["id"]
        concurrent_batch_ids: list[str] = []
        for upload_number in (1, 2, 3):
            concurrent_preview = await client.post(
                f"/api/v1/events/{concurrent_event_id}/imports",
                headers=headers,
                files={
                    "workbook": (
                        f"synthetic-concurrent-{upload_number}.xlsx",
                        concurrent_retry_workbook(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert concurrent_preview.status_code == 201, concurrent_preview.text
            concurrent_reconciliation = concurrent_preview.json()["reconciliation"]
            assert concurrent_reconciliation["currently_valid_staged_rows"] == 2
            assert concurrent_reconciliation["duplicates"] == 0
            concurrent_batch_ids.append(concurrent_preview.json()["id"])

        concurrent_guests_before_progress = await client.get(
            f"/api/v1/events/{concurrent_event_id}/guests?limit=100&offset=0",
            headers=headers,
        )
        assert concurrent_guests_before_progress.status_code == 200
        assert concurrent_guests_before_progress.json()["items"] == []

        concurrent_results = await asyncio.gather(
            *(
                client.post(
                    f"/api/v1/events/{concurrent_event_id}/imports/{concurrent_batch_id}/progress",
                    headers=headers,
                )
                for concurrent_batch_id in concurrent_batch_ids[:2]
            )
        )
        assert all(result.status_code == 200 for result in concurrent_results)
        assert sum(result.json()["imported_rows"] for result in concurrent_results) == 2
        assert sum(result.json()["duplicate_rows"] for result in concurrent_results) == 2
        concurrent_guests = await client.get(
            f"/api/v1/events/{concurrent_event_id}/guests?limit=100&offset=0",
            headers=headers,
        )
        assert concurrent_guests.status_code == 200
        assert len(concurrent_guests.json()["items"]) == 2
        untouched_third_preview = await client.get(
            f"/api/v1/events/{concurrent_event_id}/imports/{concurrent_batch_ids[2]}/rows",
            headers=headers,
        )
        assert untouched_third_preview.status_code == 200
        assert [item["status"] for item in untouched_third_preview.json()["items"]] == [
            "VALID",
            "VALID",
        ]

        second_event = await client.post(
            "/api/v1/events",
            headers=headers,
            json={**event_payload, "event_name": "Synthetic Cross-Scope Wedding"},
        )
        assert second_event.status_code == 201
        cross_scope = await client.get(
            f"/api/v1/events/{second_event.json()['id']}/imports/{batch_id}",
            headers=headers,
        )
        unauthenticated = await client.get(f"/api/v1/events/{event_id}/imports/{batch_id}")
        assert cross_scope.status_code == 404
        assert unauthenticated.status_code == 401

        return {
            "event_id": event_id,
            "second_event_id": second_event.json()["id"],
            "batch_id": batch_id,
            "row_id": row_id,
            "contextual_event_id": contextual_event_id,
            "contextual_batch_id": contextual_batch_id,
        }


def test_m4_schema_api_correction_reimport_and_immutability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = configure_test_environment(monkeypatch, tmp_path)
    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["check"]) == 0

    assert DISPOSABLE_URL is not None
    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    runtime = DatabaseRuntime(settings)
    administrator_id: UUID | None = None
    identifiers: dict[str, str] = {}
    username = f"m4-admin-{uuid4().hex[:8]}"
    try:
        security = AuthenticationSecurity.from_settings(settings)
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session),
                security.passwords,
            ).provision(username, "synthetic administrator password")
            administrator_id = administrator.id

        identifiers = asyncio.run(exercise_import_api(settings, runtime, username))

        with Session(engine) as session:
            contextual_batch = session.get(
                ImportBatch,
                UUID(identifiers["contextual_batch_id"]),
            )
            assert contextual_batch is not None
            assert contextual_batch.event_id == UUID(identifiers["contextual_event_id"])
            assert contextual_batch.candidate_row_count == 3
            assert contextual_batch.ignored_row_count == 4
            ignored_rows = contextual_batch.structure_snapshot["ignored_rows"]
            assert [item["reason"] for item in ignored_rows].count("PRESENTATION") == 2
            assert [item["reason"] for item in ignored_rows].count("BLANK") == 1
            assert [item["reason"] for item in ignored_rows].count("FOOTER") == 1
            contextual_rows = list(
                session.execute(
                    select(ImportBatchRow)
                    .where(ImportBatchRow.batch_id == contextual_batch.id)
                    .order_by(ImportBatchRow.source_row_number)
                ).scalars()
            )
            assert [row.source_row_number for row in contextual_rows] == [5, 6, 7]
            assert all(row.status is ImportRowStatus.VALID for row in contextual_rows)
            assert all(row.validation_errors == [] for row in contextual_rows)

        inspector = inspect(engine)
        assert {"import_batches", "import_batch_rows", "import_row_revisions"} <= set(
            inspector.get_table_names()
        )
        assert {index["name"] for index in inspector.get_indexes("import_batch_rows")} >= {
            "ix_import_batch_rows_batch_status_source"
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints("import_batch_rows")
        } >= {"ck_import_batch_rows_guest_link", "ck_import_batch_rows_status"}

        with Session(engine) as session:
            row = session.get(ImportBatchRow, UUID(identifiers["row_id"]))
            assert row is not None
            row.source_row_number += 1
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            revision = (
                session.execute(
                    select(ImportRowRevision)
                    .where(ImportRowRevision.row_id == UUID(identifiers["row_id"]))
                    .order_by(ImportRowRevision.revision)
                )
                .scalars()
                .first()
            )
            assert revision is not None
            revision.changed_values = {"forbidden": "mutation"}
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            revision = session.get(ImportRowRevision, revision.id)
            assert revision is not None
            session.delete(revision)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        evidence_files = list(settings.IMPORT_EVIDENCE_STORAGE_ROOT.rglob("*.xlsx"))
        assert len(evidence_files) == 10
        assert all("synthetic.xlsx" not in path.name for path in evidence_files)
    finally:
        runtime.close()
        if administrator_id is not None:
            with Session(engine) as session:
                events = list(
                    session.execute(
                        select(Event).where(Event.created_by == administrator_id)
                    ).scalars()
                )
                event_ids = [event.id for event in events]
                if event_ids:
                    session.execute(delete(ImportBatch).where(ImportBatch.event_id.in_(event_ids)))
                for event in events:
                    session.delete(event)
                session.flush()
                administrator = session.get(Administrator, administrator_id)
                if administrator is not None:
                    session.delete(administrator)
                session.commit()
        engine.dispose()
