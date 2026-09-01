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
from app.models.imports import ImportBatch, ImportBatchRow, ImportRowRevision
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

        guests_before_progress = await client.get(
            f"/api/v1/events/{event_id}/guests?limit=100&offset=0",
            headers=headers,
        )
        assert guests_before_progress.status_code == 200
        assert guests_before_progress.json()["items"] == []

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
        assert len(evidence_files) == 2
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
