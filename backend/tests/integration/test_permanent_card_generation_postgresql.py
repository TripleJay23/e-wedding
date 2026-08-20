"""Opt-in PostgreSQL M6 exact rendering, lifecycle, constraints, and concurrency."""

from __future__ import annotations

import asyncio
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier
from time import perf_counter
from uuid import UUID, uuid4
from zipfile import ZipFile

import httpx
import pytest
import zxingcpp
from PIL import Image
from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cards.package import (
    APPROVED_PREVIEW_SHA256,
    LOGICAL_PACKAGE_SHA256,
    MANIFEST_SHA256,
    RAW_PACKAGE_SHA256,
    VerifiedTemplateStorage,
)
from app.cards.renderer import ManifestRenderer
from app.cards.storage import EventPhotoStorage, GeneratedCardStorage
from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator
from app.models.cards import (
    EventCardDesignRevision,
    GeneratedCardStatus,
    GeneratedGuestCard,
    TemplatePreset,
    TemplatePresetVersion,
    TemplateVersionStatus,
)
from app.models.event import Event
from app.models.guest import Guest
from app.models.qr import GuestQrCredential, QrCredentialStatus
from app.qr.credentials import hash_qr_credential
from app.qr.storage import QrImageStorage
from app.repositories.administrators import AdministratorRepository
from app.repositories.cards import CardRepository
from app.repositories.qr import QrCredentialRepository
from app.services.admin_provisioning import AdministratorProvisioningService
from app.services.cards import CardService
from app.services.qr import QrCredentialService

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
RECOVERED_ROOT = Path(__file__).resolve().parents[2] / "storage" / "private" / "templates"
RECOVERED_V10 = RECOVERED_ROOT / "wedding_floral" / "v10"
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
    pytest.mark.skipif(
        not (RECOVERED_V10 / "wedding_floral_v10.template-package.zip").is_file(),
        reason="exact ignored Wedding Floral V10 recovery is not present",
    ),
]


def _configure_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    assert DISPOSABLE_URL is not None
    private_root = tmp_path / "private"
    destination = private_root / "templates" / "wedding_floral" / "v10"
    destination.mkdir(parents=True)
    for filename in (
        "wedding_floral_v10.template-package.zip",
        "wedding_floral_v10_default_preview.png",
    ):
        shutil.copyfile(RECOVERED_V10 / filename, destination / filename)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv("JWT_SECRET", "synthetic-jwt-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(private_root))
    monkeypatch.setenv("IMPORT_EVIDENCE_STORAGE_ROOT", str(private_root / "import-evidence"))
    monkeypatch.setenv("QR_IMAGE_STORAGE_ROOT", str(private_root / "qr-codes"))
    monkeypatch.setenv("TEMPLATE_STORAGE_ROOT", str(private_root / "templates"))
    monkeypatch.setenv("EVENT_PHOTO_STORAGE_ROOT", str(private_root / "event-photos"))
    monkeypatch.setenv("GENERATED_CARD_STORAGE_ROOT", str(private_root / "generated-cards"))
    return Settings()


def _photo_bytes() -> bytes:
    image = Image.new("RGB", (1200, 900), "#b8c7a2")
    output = BytesIO()
    image.save(output, format="JPEG", quality=92, comment=b"must be stripped")
    return output.getvalue()


def _decode_qr(content: bytes) -> str:
    with Image.open(BytesIO(content)) as image:
        results = zxingcpp.read_barcodes(image)
    assert len(results) == 1
    return results[0].text


def _decode_card_qr(content: bytes) -> str:
    with Image.open(BytesIO(content)) as image:
        assert image.size == (1240, 1748)
        results = zxingcpp.read_barcodes(image.crop((80, 87, 278, 285)))
    assert len(results) == 1
    return results[0].text


async def _exercise_m6_api(
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
                "event_name": "Synthetic M6 PostgreSQL Wedding",
                "bride_name": "Jordan",
                "groom_name": "Jessica",
                "event_datetime": "2027-06-12T14:30:00+03:00",
                "venue": "Synthetic Hall",
                "address": "123 Synthetic Road",
                "max_companions_per_invitation": 2,
            },
        )
        assert event.status_code == 201, event.text
        event_id = event.json()["id"]
        guest = await client.post(
            f"/api/v1/events/{event_id}/guests",
            headers=headers,
            json={
                "full_name": "Synthetic Permanent Card Guest",
                "invitation_type": "Family",
                "allowed_companions": 2,
            },
        )
        assert guest.status_code == 201, guest.text
        guest_id = guest.json()["id"]
        issued = await client.post(f"/api/v1/guests/{guest_id}/qr-code", headers=headers)
        assert issued.status_code == 201, issued.text
        original_credential_id = issued.json()["id"]

        registered = await client.post(
            "/api/v1/templates/wedding_floral/versions/10/register",
            headers=headers,
        )
        assert registered.status_code == 201, registered.text
        assert registered.json()["raw_package_sha256"] == RAW_PACKAGE_SHA256
        assert registered.json()["logical_package_sha256"] == LOGICAL_PACKAGE_SHA256
        assert registered.json()["manifest_sha256"] == MANIFEST_SHA256
        assert registered.json()["approved_preview_sha256"] == APPROVED_PREVIEW_SHA256
        assert "storage" not in registered.text.casefold()
        template_version_id = registered.json()["id"]
        registered_retry = await client.post(
            "/api/v1/templates/wedding_floral/versions/10/register",
            headers=headers,
        )
        assert registered_retry.json()["id"] == template_version_id

        photo = await client.post(
            f"/api/v1/events/{event_id}/photos",
            headers=headers,
            files={"photo": ("synthetic-private.jpg", _photo_bytes(), "image/jpeg")},
        )
        assert photo.status_code == 201, photo.text
        assert photo.json()["media_type"] == "image/png"
        assert "storage" not in photo.text.casefold()
        photo_id = photo.json()["id"]
        downloaded_photo = await client.get(
            f"/api/v1/events/{event_id}/photos/{photo_id}/content",
            headers=headers,
        )
        assert downloaded_photo.status_code == 200
        with Image.open(BytesIO(downloaded_photo.content)) as image:
            assert image.format == "PNG"
            assert image.info == {}

        design = await client.put(
            f"/api/v1/events/{event_id}/card-design",
            headers=headers,
            json={
                "template_key": "wedding_floral",
                "template_version": 10,
                "event_photo_id": photo_id,
                "photo_fit": "COVER",
                "photo_focus_x": "0.5",
                "photo_focus_y": "0.5",
                "photo_zoom": "1.0",
            },
        )
        assert design.status_code == 201, design.text
        revision_id = design.json()["id"]
        preview = await client.get(
            f"/api/v1/events/{event_id}/card-design/preview?revision_id={revision_id}",
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert _decode_card_qr(preview.content) == "PREVIEW-QR-DO-NOT-SCAN"
        approval = await client.post(
            f"/api/v1/events/{event_id}/card-design/revisions/{revision_id}/approve",
            headers=headers,
        )
        assert approval.status_code == 200, approval.text
        approval_id = approval.json()["id"]

        started = perf_counter()
        generated = await client.post(
            f"/api/v1/events/{event_id}/cards/guests/{guest_id}",
            headers=headers,
        )
        single_seconds = perf_counter() - started
        assert generated.status_code == 201, generated.text
        card_id = generated.json()["id"]
        assert generated.json()["qr_credential_id"] == original_credential_id
        assert generated.json()["template_version_id"] == template_version_id
        assert "storage" not in generated.text.casefold()
        assert "path" not in generated.text.casefold()
        assert "token" not in generated.text.casefold()
        retry = await client.post(
            f"/api/v1/events/{event_id}/cards/guests/{guest_id}",
            headers=headers,
        )
        assert retry.json()["id"] == card_id
        content = await client.get(
            f"/api/v1/events/{event_id}/cards/{card_id}/content",
            headers=headers,
        )
        assert content.status_code == 200
        assert content.headers["cache-control"] == "private, no-store"
        assert b"PREVIEW-QR-DO-NOT-SCAN" not in content.content

        listed = await client.get(f"/api/v1/events/{event_id}/cards", headers=headers)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [card_id]
        exported = await client.post(
            f"/api/v1/events/{event_id}/cards/export",
            headers=headers,
            json={"card_ids": [card_id]},
        )
        assert exported.status_code == 200
        with ZipFile(BytesIO(exported.content)) as archive:
            assert archive.namelist() == [f"card-{card_id}.png"]

        deactivated = await client.post(
            f"/api/v1/guests/{guest_id}/qr-code/deactivate",
            headers=headers,
        )
        assert deactivated.json()["status"] == "INACTIVE"
        rejected = await client.post(
            f"/api/v1/events/{event_id}/cards/guests/{guest_id}",
            headers=headers,
        )
        assert rejected.status_code == 409
        activated = await client.post(
            f"/api/v1/guests/{guest_id}/qr-code/activate",
            headers=headers,
        )
        assert activated.json()["status"] == "ACTIVE"
        reactivated = await client.post(
            f"/api/v1/events/{event_id}/cards/guests/{guest_id}",
            headers=headers,
        )
        assert reactivated.json()["id"] == card_id
        regenerated_qr = await client.post(
            f"/api/v1/guests/{guest_id}/qr-code/regenerate",
            headers=headers,
        )
        assert regenerated_qr.status_code == 201
        replacement_credential_id = regenerated_qr.json()["id"]
        replacement_card = await client.post(
            f"/api/v1/events/{event_id}/cards/guests/{guest_id}",
            headers=headers,
        )
        assert replacement_card.status_code == 201, replacement_card.text
        assert replacement_card.json()["id"] != card_id
        assert replacement_card.json()["qr_credential_id"] == replacement_credential_id

        other_event = await client.post(
            "/api/v1/events",
            headers=headers,
            json={
                "event_name": "Synthetic Cross Event",
                "bride_name": "Amina",
                "groom_name": "Baraka",
                "event_datetime": "2027-07-01T10:00:00+03:00",
                "venue": "Other Hall",
                "address": "456 Other Road",
            },
        )
        assert other_event.status_code == 201
        cross_event = await client.get(
            f"/api/v1/events/{other_event.json()['id']}/cards/{replacement_card.json()['id']}",
            headers=headers,
        )
        unauthenticated = await client.get(
            f"/api/v1/events/{event_id}/cards/{replacement_card.json()['id']}/content"
        )
        assert cross_event.status_code == 404
        assert unauthenticated.status_code == 401
        print(f"M6_SINGLE_RENDER_SECONDS={single_seconds:.4f}")
        return {
            "event_id": event_id,
            "guest_id": guest_id,
            "original_credential_id": original_credential_id,
            "replacement_credential_id": replacement_credential_id,
            "original_card_id": card_id,
            "replacement_card_id": replacement_card.json()["id"],
            "revision_id": revision_id,
            "approval_id": approval_id,
            "template_version_id": template_version_id,
        }


def _service(settings: Settings, session: Session) -> CardService:
    template_root = settings.TEMPLATE_STORAGE_ROOT
    photo_root = settings.EVENT_PHOTO_STORAGE_ROOT
    generated_root = settings.GENERATED_CARD_STORAGE_ROOT
    qr_root = settings.QR_IMAGE_STORAGE_ROOT
    assert template_root is not None
    assert photo_root is not None
    assert generated_root is not None
    assert qr_root is not None
    return CardService(
        CardRepository(session),
        VerifiedTemplateStorage(template_root),
        EventPhotoStorage(photo_root),
        GeneratedCardStorage(generated_root),
        QrCredentialService(QrCredentialRepository(session), QrImageStorage(qr_root)),
        ManifestRenderer(),
        maximum_event_photo_bytes=settings.MAX_EVENT_PHOTO_UPLOAD_BYTES,
    )


def test_m6_schema_api_qr_bridge_constraints_concurrency_and_query_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _configure_environment(monkeypatch, tmp_path)
    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["check"]) == 0
    assert DISPOSABLE_URL is not None
    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    runtime = DatabaseRuntime(settings)
    administrator_id: UUID | None = None
    username = f"m6-admin-{uuid4().hex[:8]}"
    try:
        security = AuthenticationSecurity.from_settings(settings)
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session), security.passwords
            ).provision(username, "synthetic administrator password")
            administrator_id = administrator.id

        identifiers = asyncio.run(_exercise_m6_api(settings, runtime, username))
        event_id = UUID(identifiers["event_id"])
        guest_id = UUID(identifiers["guest_id"])
        original_credential_id = UUID(identifiers["original_credential_id"])
        replacement_credential_id = UUID(identifiers["replacement_credential_id"])
        original_card_id = UUID(identifiers["original_card_id"])
        replacement_card_id = UUID(identifiers["replacement_card_id"])
        revision_id = UUID(identifiers["revision_id"])
        template_version_id = UUID(identifiers["template_version_id"])
        qr_root = settings.QR_IMAGE_STORAGE_ROOT
        generated_root = settings.GENERATED_CARD_STORAGE_ROOT
        assert qr_root is not None
        assert generated_root is not None

        with Session(engine) as session:
            original_credential = session.get(GuestQrCredential, original_credential_id)
            replacement_credential = session.get(GuestQrCredential, replacement_credential_id)
            original_card = session.get(GeneratedGuestCard, original_card_id)
            replacement_card = session.get(GeneratedGuestCard, replacement_card_id)
            assert original_credential is not None
            assert replacement_credential is not None
            assert original_card is not None
            assert replacement_card is not None
            assert original_credential.status is QrCredentialStatus.SUPERSEDED
            assert replacement_credential.status is QrCredentialStatus.ACTIVE
            assert original_card.status is GeneratedCardStatus.STALE
            assert replacement_card.status is GeneratedCardStatus.CURRENT
            original_payload = _decode_qr(
                QrImageStorage(qr_root).read(original_credential.image_storage_key)
            )
            replacement_payload = _decode_qr(
                QrImageStorage(qr_root).read(replacement_credential.image_storage_key)
            )
            card_content = GeneratedCardStorage(generated_root).read(replacement_card.storage_key)
            assert _decode_card_qr(card_content) == replacement_payload
            assert original_payload != replacement_payload
            assert hash_qr_credential(replacement_payload) == replacement_credential.token_hash
            assert hash_qr_credential(original_payload) == original_credential.token_hash
            assert len(replacement_payload) == 32
            assert "Synthetic" not in replacement_payload
            assert "Jordan" not in replacement_payload
            assert "Jessica" not in replacement_payload

        inspector = inspect(engine)
        assert {
            "template_presets",
            "template_preset_versions",
            "event_photos",
            "event_card_designs",
            "event_card_design_revisions",
            "event_card_design_approvals",
            "generated_guest_cards",
        } <= set(inspector.get_table_names())
        generated_indexes = {
            index["name"] for index in inspector.get_indexes("generated_guest_cards")
        }
        assert "uq_generated_guest_cards_one_current_guest" in generated_indexes
        assert "ix_generated_guest_cards_event_generated_id" in generated_indexes
        generated_checks = {
            check["name"] for check in inspector.get_check_constraints("generated_guest_cards")
        }
        assert {
            "ck_generated_guest_cards_storage_key",
            "ck_generated_guest_cards_status_timestamp",
            "ck_generated_guest_cards_dimensions",
        } <= generated_checks

        assert administrator_id is not None
        with Session(engine, expire_on_commit=False) as session:
            event = session.get(Event, event_id)
            guest = session.get(Guest, guest_id)
            revision = session.get(EventCardDesignRevision, revision_id)
            version = session.get(TemplatePresetVersion, template_version_id)
            current = session.get(GeneratedGuestCard, replacement_card_id)
            assert event is not None
            assert guest is not None
            assert revision is not None
            assert version is not None
            assert current is not None

            duplicate_id = uuid4()
            session.add(
                GeneratedGuestCard(
                    id=duplicate_id,
                    event_id=event_id,
                    guest_id=guest_id,
                    design_revision_id=current.design_revision_id,
                    approval_id=current.approval_id,
                    template_version_id=current.template_version_id,
                    qr_credential_id=current.qr_credential_id,
                    render_input_sha256="2" * 64,
                    storage_key=f"{event_id.hex}/{guest_id.hex}/{duplicate_id.hex}.png",
                    artifact_sha256="3" * 64,
                    media_type="image/png",
                    byte_size=1,
                    pixel_width=1240,
                    pixel_height=1748,
                    status=GeneratedCardStatus.CURRENT,
                    generated_by=administrator_id,
                    generated_at=datetime.now(UTC),
                    stale_at=None,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            invalid_qr_id = uuid4()
            session.add(
                GeneratedGuestCard(
                    id=invalid_qr_id,
                    event_id=event_id,
                    guest_id=guest_id,
                    design_revision_id=current.design_revision_id,
                    approval_id=current.approval_id,
                    template_version_id=current.template_version_id,
                    qr_credential_id=original_credential_id,
                    render_input_sha256="4" * 64,
                    storage_key=f"{event_id.hex}/{guest_id.hex}/{invalid_qr_id.hex}.png",
                    artifact_sha256="5" * 64,
                    media_type="image/png",
                    byte_size=1,
                    pixel_width=1240,
                    pixel_height=1748,
                    status=GeneratedCardStatus.CURRENT,
                    generated_by=administrator_id,
                    generated_at=datetime.now(UTC),
                    stale_at=None,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            revision = session.get(EventCardDesignRevision, revision_id)
            assert revision is not None
            revision.photo_zoom = 2
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            preset = session.execute(select(TemplatePreset)).scalar_one()
            session.add(
                TemplatePresetVersion(
                    id=uuid4(),
                    preset_id=preset.id,
                    version=11,
                    raw_package_sha256="6" * 64,
                    logical_package_sha256="7" * 64,
                    manifest_sha256="8" * 64,
                    approved_preview_sha256="9" * 64,
                    package_storage_key=(
                        "wedding_floral/v10/wedding_floral_v10.template-package.zip"
                    ),
                    canvas_width=1240,
                    canvas_height=1748,
                    asset_count=6,
                    font_count=2,
                    status=TemplateVersionStatus.VERIFIED,
                    verified_by=administrator_id,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        # Query shape and concurrency are observed on one fresh active credential/card identity.
        with Session(engine, expire_on_commit=False) as session:
            concurrent_guest = Guest(
                id=uuid4(),
                event_id=event_id,
                full_name="Synthetic Concurrent Card Guest",
                phone_number=None,
                email=None,
                invitation_type="Individual",
                allowed_companions=0,
                table_number=None,
                category=None,
            )
            session.add(concurrent_guest)
            session.commit()
            QrCredentialService(QrCredentialRepository(session), QrImageStorage(qr_root)).issue(
                concurrent_guest.id, administrator_id=administrator_id
            )
            concurrent_guest_id = concurrent_guest.id

        barrier = Barrier(2)

        def concurrent_generate() -> UUID:
            with Session(engine, expire_on_commit=False, autoflush=False) as session:
                service = _service(settings, session)
                barrier.wait(timeout=10)
                return service.generate_guest_card(
                    event_id,
                    concurrent_guest_id,
                    administrator_id=administrator_id,
                ).id

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_ids = list(executor.map(lambda _: concurrent_generate(), range(2)))
        assert concurrent_ids[0] == concurrent_ids[1]
        with Session(engine) as session:
            records = list(
                session.execute(
                    select(GeneratedGuestCard).where(
                        GeneratedGuestCard.guest_id == concurrent_guest_id
                    )
                ).scalars()
            )
            assert len(records) == 1

        # Three-card generation remains bounded rather than issuing per-guest SELECTs.
        batch_guest_ids: list[UUID] = []
        with Session(engine, expire_on_commit=False) as session:
            qr_service = QrCredentialService(
                QrCredentialRepository(session), QrImageStorage(qr_root)
            )
            for index in range(3):
                guest = Guest(
                    id=uuid4(),
                    event_id=event_id,
                    full_name=f"Synthetic Batch Guest {index}",
                    phone_number=None,
                    email=None,
                    invitation_type="Individual",
                    allowed_companions=0,
                    table_number=None,
                    category=None,
                )
                session.add(guest)
                session.commit()
                qr_service.issue(guest.id, administrator_id=administrator_id)
                batch_guest_ids.append(guest.id)

        statements: list[str] = []

        def record_statement(*args) -> None:
            statements.append(str(args[2]))

        sqlalchemy_event.listen(engine, "before_cursor_execute", record_statement)
        try:
            with Session(engine, expire_on_commit=False, autoflush=False) as session:
                started = perf_counter()
                batch_cards = _service(settings, session).generate_event_cards(
                    event_id,
                    administrator_id=administrator_id,
                    guest_ids=batch_guest_ids,
                    limit=100,
                )
                batch_seconds = perf_counter() - started
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", record_statement)
        selects = sum(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert len(batch_cards) == 3
        assert selects <= 6
        assert batch_seconds < 15
        print(
            f"M6_BATCH_RENDER_SECONDS={batch_seconds:.4f} "
            f"cards={len(batch_cards)} sql={len(statements)} selects={selects}"
        )
    finally:
        runtime.close()
        if administrator_id is not None:
            with Session(engine) as session:
                session.execute(delete(Event).where(Event.created_by == administrator_id))
                session.execute(
                    delete(TemplatePresetVersion).where(
                        TemplatePresetVersion.verified_by == administrator_id
                    )
                )
                session.execute(delete(TemplatePreset))
                administrator = session.get(Administrator, administrator_id)
                if administrator is not None:
                    session.delete(administrator)
                session.commit()
        engine.dispose()
