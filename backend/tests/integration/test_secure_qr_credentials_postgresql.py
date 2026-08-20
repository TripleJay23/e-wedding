"""Opt-in disposable PostgreSQL M5 lifecycle, security, and concurrency verification."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier
from time import perf_counter
from uuid import UUID, uuid4

import httpx
import pytest
import zxingcpp
from PIL import Image
from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator
from app.models.event import Event, EventStatus
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import hash_qr_credential
from app.qr.storage import QrImageStorage
from app.repositories.administrators import AdministratorRepository
from app.repositories.qr import QrCredentialRepository
from app.services.admin_provisioning import AdministratorProvisioningService
from app.services.qr import QrConflictError, QrCredentialService

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
]


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
    monkeypatch.setenv("QR_IMAGE_STORAGE_ROOT", str(private_root / "qr-codes"))
    return Settings()


async def exercise_qr_api(
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
        event = await client.post(
            "/api/v1/events",
            headers=headers,
            json={
                "event_name": "Synthetic M5 PostgreSQL Wedding",
                "bride_name": "Amina",
                "groom_name": "Baraka",
                "event_datetime": "2027-06-12T14:30:00+03:00",
                "venue": "Synthetic Hall",
                "max_companions_per_invitation": 2,
            },
        )
        assert event.status_code == 201, event.text
        event_id = event.json()["id"]
        guest = await client.post(
            f"/api/v1/events/{event_id}/guests",
            headers=headers,
            json={
                "full_name": "Synthetic QR Guest",
                "phone_number": "+255700000033",
                "email": "qr-guest@example.test",
                "invitation_type": "Family",
                "allowed_companions": 2,
                "table_number": "T9",
            },
        )
        assert guest.status_code == 201, guest.text
        guest_id = guest.json()["id"]

        issued = await client.post(f"/api/v1/guests/{guest_id}/qr-code", headers=headers)
        assert issued.status_code == 201, issued.text
        safe_keys = {
            "id",
            "event_id",
            "guest_id",
            "status",
            "replaces_credential_id",
            "issued_at",
            "deactivated_at",
            "superseded_at",
            "created_at",
            "updated_at",
        }
        assert set(issued.json()) == safe_keys
        assert all(
            forbidden not in issued.text.casefold()
            for forbidden in ("token", "hash", "storage", "private", "example.test", "+255")
        )

        duplicate = await client.post(f"/api/v1/guests/{guest_id}/qr-code", headers=headers)
        metadata = await client.get(f"/api/v1/guests/{guest_id}/qr-code", headers=headers)
        deactivated = await client.post(
            f"/api/v1/guests/{guest_id}/qr-code/deactivate", headers=headers
        )
        activated = await client.post(
            f"/api/v1/guests/{guest_id}/qr-code/activate", headers=headers
        )
        regenerated = await client.post(
            f"/api/v1/guests/{guest_id}/qr-code/regenerate", headers=headers
        )
        listed = await client.get(f"/api/v1/events/{event_id}/qr-codes?limit=100", headers=headers)
        unauthenticated = await client.get(f"/api/v1/guests/{guest_id}/qr-code")
        invalid_auth = await client.get(
            f"/api/v1/guests/{guest_id}/qr-code",
            headers={"Authorization": "Bearer invalid-token"},
        )
        missing = await client.post(f"/api/v1/guests/{uuid4()}/qr-code", headers=headers)

        assert duplicate.status_code == 409
        assert metadata.status_code == 200
        assert deactivated.json()["status"] == "INACTIVE"
        assert activated.json()["status"] == "ACTIVE"
        assert regenerated.status_code == 201
        assert regenerated.json()["replaces_credential_id"] == issued.json()["id"]
        assert len(listed.json()["items"]) == 2
        assert unauthenticated.status_code == invalid_auth.status_code == 401
        assert missing.status_code == 404

        inactive_guest = await client.post(
            f"/api/v1/events/{event_id}/guests",
            headers=headers,
            json={
                "full_name": "Synthetic Inactive Guest",
                "invitation_type": "Individual",
            },
        )
        inactive_guest_id = inactive_guest.json()["id"]
        made_inactive = await client.patch(
            f"/api/v1/events/{event_id}/guests/{inactive_guest_id}",
            headers=headers,
            json={"status": "INACTIVE"},
        )
        ineligible = await client.post(
            f"/api/v1/guests/{inactive_guest_id}/qr-code", headers=headers
        )
        assert made_inactive.status_code == 200
        assert ineligible.status_code == 409

        return {
            "event_id": event_id,
            "guest_id": guest_id,
            "original_credential_id": issued.json()["id"],
            "replacement_credential_id": regenerated.json()["id"],
        }


def decode_png(content: bytes) -> str:
    decoded = zxingcpp.read_barcodes(Image.open(BytesIO(content)))
    assert len(decoded) == 1
    return decoded[0].text


def make_direct_credential(
    *,
    event_id: UUID,
    guest_id: UUID,
    administrator_id: UUID,
    token_hash: str,
    status: QrCredentialStatus = QrCredentialStatus.ACTIVE,
    deactivated_at: datetime | None = None,
) -> GuestQrCredential:
    credential_id = uuid4()
    return GuestQrCredential(
        id=credential_id,
        event_id=event_id,
        guest_id=guest_id,
        token_hash=token_hash,
        image_storage_key=f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png",
        image_sha256="f" * 64,
        status=status,
        replaces_credential_id=None,
        issued_by=administrator_id,
        issued_at=datetime.now(UTC),
        deactivated_at=deactivated_at,
        superseded_at=None,
    )


def test_m5_schema_api_hash_storage_constraints_and_concurrency(
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
    username = f"m5-admin-{uuid4().hex[:8]}"
    try:
        security = AuthenticationSecurity.from_settings(settings)
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session), security.passwords
            ).provision(username, "synthetic administrator password")
            administrator_id = administrator.id

        identifiers = asyncio.run(exercise_qr_api(settings, runtime, username))
        guest_id = UUID(identifiers["guest_id"])
        event_id = UUID(identifiers["event_id"])
        original_id = UUID(identifiers["original_credential_id"])
        replacement_id = UUID(identifiers["replacement_credential_id"])
        qr_root = settings.QR_IMAGE_STORAGE_ROOT
        assert qr_root is not None
        storage = QrImageStorage(qr_root)

        with Session(engine) as session:
            original = session.get(GuestQrCredential, original_id)
            replacement = session.get(GuestQrCredential, replacement_id)
            assert original is not None
            assert replacement is not None
            original_raw = decode_png(storage.read(original.image_storage_key))
            replacement_raw = decode_png(storage.read(replacement.image_storage_key))
            assert original.token_hash == hash_qr_credential(original_raw)
            assert replacement.token_hash == hash_qr_credential(replacement_raw)
            assert original_raw != replacement_raw
            assert original.status is QrCredentialStatus.SUPERSEDED
            assert replacement.status is QrCredentialStatus.ACTIVE
            assert original_raw not in original.token_hash
            assert replacement_raw not in replacement.token_hash
            repository = QrCredentialRepository(session)
            assert repository.get_active_by_hash(original.token_hash) is None
            active_replacement = repository.get_active_by_hash(replacement.token_hash)
            assert active_replacement is not None
            assert active_replacement.id == replacement.id

        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("guest_qr_credentials")} == {
            "id",
            "event_id",
            "guest_id",
            "token_hash",
            "image_storage_key",
            "image_sha256",
            "status",
            "replaces_credential_id",
            "issued_by",
            "issued_at",
            "deactivated_at",
            "superseded_at",
            "created_at",
            "updated_at",
        }
        index_names = {index["name"] for index in inspector.get_indexes("guest_qr_credentials")}
        assert "uq_guest_qr_credentials_one_active_guest" in index_names
        assert "ix_guest_qr_credentials_guest_issued_id" in index_names
        check_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("guest_qr_credentials")
        }
        assert {
            "ck_guest_qr_credentials_token_hash",
            "ck_guest_qr_credentials_storage_key",
            "ck_guest_qr_credentials_status_timestamps",
        } <= check_names

        assert administrator_id is not None
        with Session(engine, expire_on_commit=False) as session:
            performance_guest = Guest(
                id=uuid4(),
                event_id=event_id,
                full_name="Synthetic Query Observation Guest",
                phone_number=None,
                email=None,
                invitation_type="Individual",
                allowed_companions=0,
                table_number=None,
                category=None,
                status=GuestStatus.ACTIVE,
            )
            session.add(performance_guest)
            session.commit()
            performance_guest_id = performance_guest.id

        statements: list[str] = []

        def record_statement(*args) -> None:
            statements.append(str(args[2]))

        sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
        try:
            with Session(engine, expire_on_commit=False, autoflush=False) as session:
                service = QrCredentialService(
                    QrCredentialRepository(session),
                    QrImageStorage(qr_root),
                )
                started = perf_counter()
                service.issue(performance_guest_id, administrator_id=administrator_id)
                issuance_ms = (perf_counter() - started) * 1_000
            issuance_statements = list(statements)
            statements.clear()
            with Session(engine, expire_on_commit=False, autoflush=False) as session:
                service = QrCredentialService(
                    QrCredentialRepository(session),
                    QrImageStorage(qr_root),
                )
                started = perf_counter()
                service.get(performance_guest_id)
                metadata_ms = (perf_counter() - started) * 1_000
            metadata_statements = list(statements)
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", record_statement)

        issuance_selects = sum(
            statement.lstrip().upper().startswith("SELECT") for statement in issuance_statements
        )
        issuance_inserts = sum(
            statement.lstrip().upper().startswith("INSERT") for statement in issuance_statements
        )
        metadata_selects = sum(
            statement.lstrip().upper().startswith("SELECT") for statement in metadata_statements
        )
        assert issuance_selects == 2
        assert issuance_inserts == 1
        assert metadata_selects == 1
        print(
            "M5_QUERY_OBSERVATION "
            f"issuance_ms={issuance_ms:.3f} issuance_sql={len(issuance_statements)} "
            f"metadata_ms={metadata_ms:.3f} metadata_sql={len(metadata_statements)}"
        )

        with Session(engine, expire_on_commit=False) as session:
            event = session.get(Event, event_id)
            assert event is not None
            concurrent_guest = Guest(
                id=uuid4(),
                event_id=event_id,
                full_name="Synthetic Concurrent Issue Guest",
                phone_number=None,
                email=None,
                invitation_type="Individual",
                allowed_companions=0,
                table_number=None,
                category=None,
                status=GuestStatus.ACTIVE,
            )
            session.add(concurrent_guest)
            session.commit()
            concurrent_guest_id = concurrent_guest.id

        issue_barrier = Barrier(2)

        def concurrent_issue() -> str:
            with Session(engine, expire_on_commit=False, autoflush=False) as session:
                service = QrCredentialService(
                    QrCredentialRepository(session),
                    QrImageStorage(qr_root),
                )
                issue_barrier.wait(timeout=10)
                try:
                    service.issue(concurrent_guest_id, administrator_id=administrator_id)
                except QrConflictError:
                    return "conflict"
                return "issued"

        with ThreadPoolExecutor(max_workers=2) as executor:
            issue_results = list(executor.map(lambda _: concurrent_issue(), range(2)))
        assert sorted(issue_results) == ["conflict", "issued"]

        regenerate_barrier = Barrier(2)

        def concurrent_regenerate() -> str:
            with Session(engine, expire_on_commit=False, autoflush=False) as session:
                service = QrCredentialService(
                    QrCredentialRepository(session),
                    QrImageStorage(qr_root),
                )
                regenerate_barrier.wait(timeout=10)
                service.regenerate(concurrent_guest_id, administrator_id=administrator_id)
                return "regenerated"

        with ThreadPoolExecutor(max_workers=2) as executor:
            regeneration_results = list(executor.map(lambda _: concurrent_regenerate(), range(2)))
        assert regeneration_results == ["regenerated", "regenerated"]
        with Session(engine) as session:
            concurrent_records = list(
                session.execute(
                    select(GuestQrCredential).where(
                        GuestQrCredential.guest_id == concurrent_guest_id
                    )
                ).scalars()
            )
            assert len(concurrent_records) == 3
            assert (
                sum(record.status is QrCredentialStatus.ACTIVE for record in concurrent_records)
                == 1
            )
            assert (
                sum(record.status is QrCredentialStatus.SUPERSEDED for record in concurrent_records)
                == 2
            )

        with Session(engine, expire_on_commit=False) as session:
            second_event = Event(
                id=uuid4(),
                event_name="Synthetic Cross Event",
                bride_name="Neema",
                groom_name="Juma",
                event_datetime=datetime(2027, 7, 1, tzinfo=UTC),
                venue="Synthetic Venue",
                status=EventStatus.DRAFT,
                max_companions_per_invitation=2,
                created_by=administrator_id,
            )
            second_guest = Guest(
                id=uuid4(),
                event_id=second_event.id,
                full_name="Synthetic Constraint Guest",
                phone_number=None,
                email=None,
                invitation_type="Individual",
                allowed_companions=0,
                table_number=None,
                category=None,
                status=GuestStatus.ACTIVE,
            )
            session.add_all([second_event, second_guest])
            session.commit()

            cross_event = make_direct_credential(
                event_id=event_id,
                guest_id=second_guest.id,
                administrator_id=administrator_id,
                token_hash="1" * 64,
            )
            session.add(cross_event)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            duplicate_active = make_direct_credential(
                event_id=event_id,
                guest_id=guest_id,
                administrator_id=administrator_id,
                token_hash="2" * 64,
            )
            session.add(duplicate_active)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            duplicate_hash = make_direct_credential(
                event_id=second_event.id,
                guest_id=second_guest.id,
                administrator_id=administrator_id,
                token_hash="1" * 64,
            )
            session.add(duplicate_hash)
            session.commit()
            first_direct_hash = duplicate_hash.token_hash
            session.delete(duplicate_hash)
            session.commit()
            duplicate_hash_again = make_direct_credential(
                event_id=second_event.id,
                guest_id=second_guest.id,
                administrator_id=administrator_id,
                token_hash=first_direct_hash,
            )
            duplicate_hash_again.token_hash = replacement.token_hash
            session.add(duplicate_hash_again)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            invalid_inactive = make_direct_credential(
                event_id=second_event.id,
                guest_id=second_guest.id,
                administrator_id=administrator_id,
                token_hash="3" * 64,
                status=QrCredentialStatus.INACTIVE,
                deactivated_at=None,
            )
            session.add(invalid_inactive)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            original = session.get(GuestQrCredential, original_id)
            assert original is not None
            original.status = QrCredentialStatus.ACTIVE
            original.superseded_at = None
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        runtime.close()
        if administrator_id is not None:
            with Session(engine) as session:
                session.execute(delete(Event).where(Event.created_by == administrator_id))
                administrator = session.get(Administrator, administrator_id)
                if administrator is not None:
                    session.delete(administrator)
                session.commit()
        engine.dispose()
