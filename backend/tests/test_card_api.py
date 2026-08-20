"""Authenticated M6 API inventory, privacy, protected content, and errors."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.dependencies import (
    get_authentication_service,
    get_card_service,
    get_current_administrator,
)
from app.main import create_app
from app.models.administrator import Administrator, AdministratorRole
from app.models.cards import (
    DesignApprovalStatus,
    EventCardDesignRevision,
    EventPhoto,
    GeneratedCardStatus,
    GeneratedGuestCard,
    PhotoFit,
    TemplatePreset,
    TemplatePresetVersion,
    TemplateVersionStatus,
)
from app.services.authentication import AuthenticationRejectedError
from app.services.cards import (
    CardConflictError,
    CardInputError,
    CardPersistenceError,
    CardResourceNotFoundError,
    CardStorageError,
)


class FakeDatabaseRuntime:
    def close(self) -> None:
        pass


class RejectAuthenticationService:
    def current_administrator(self, token: str) -> Administrator:
        raise AuthenticationRejectedError("synthetic rejection")


def _administrator() -> Administrator:
    now = datetime.now(UTC)
    return Administrator(
        id=uuid4(),
        username="admin",
        password_hash="synthetic",
        role=AdministratorRole.SUPER_ADMIN,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


class FakeCardService:
    def __init__(self, administrator_id: UUID) -> None:
        now = datetime.now(UTC)
        self.event_id = uuid4()
        self.guest_id = uuid4()
        self.preset = TemplatePreset(
            id=uuid4(), template_key="wedding_floral", display_name="Wedding Floral"
        )
        self.version = TemplatePresetVersion(
            id=uuid4(),
            preset_id=self.preset.id,
            version=10,
            raw_package_sha256="a" * 64,
            logical_package_sha256="b" * 64,
            manifest_sha256="c" * 64,
            approved_preview_sha256="d" * 64,
            package_storage_key="wedding_floral/v10/private.zip",
            canvas_width=1240,
            canvas_height=1748,
            asset_count=6,
            font_count=2,
            status=TemplateVersionStatus.VERIFIED,
            verified_by=administrator_id,
            verified_at=now,
            created_at=now,
        )
        self.photo = EventPhoto(
            id=uuid4(),
            event_id=self.event_id,
            storage_key="private/internal/photo.png",
            sha256="e" * 64,
            media_type="image/png",
            byte_size=1234,
            pixel_width=800,
            pixel_height=600,
            is_primary=True,
            uploaded_by=administrator_id,
            created_at=now,
            updated_at=now,
        )
        self.revision = EventCardDesignRevision(
            id=uuid4(),
            design_id=uuid4(),
            event_id=self.event_id,
            revision_number=1,
            template_version_id=self.version.id,
            event_photo_id=self.photo.id,
            photo_fit=PhotoFit.COVER,
            photo_focus_x=Decimal("0.500"),
            photo_focus_y=Decimal("0.500"),
            photo_zoom=Decimal("1.000"),
            created_by=administrator_id,
            created_at=now,
        )
        self.approval = self._approval(administrator_id, now)
        self.card = GeneratedGuestCard(
            id=uuid4(),
            event_id=self.event_id,
            guest_id=self.guest_id,
            design_revision_id=self.revision.id,
            approval_id=self.approval.id,
            template_version_id=self.version.id,
            qr_credential_id=uuid4(),
            render_input_sha256="f" * 64,
            storage_key="C:/private/internal/card.png",
            artifact_sha256="1" * 64,
            media_type="image/png",
            byte_size=54321,
            pixel_width=1240,
            pixel_height=1748,
            status=GeneratedCardStatus.CURRENT,
            generated_by=administrator_id,
            generated_at=now,
            stale_at=None,
        )
        self.calls: list[tuple[str, Any]] = []
        self.next_error: Exception | None = None

    def _approval(self, administrator_id: UUID, now: datetime):
        from app.models.cards import EventCardDesignApproval

        return EventCardDesignApproval(
            id=uuid4(),
            event_id=self.event_id,
            design_revision_id=self.revision.id,
            status=DesignApprovalStatus.ACTIVE,
            approved_by=administrator_id,
            approved_at=now,
            revoked_by=None,
            revoked_at=None,
        )

    def _check(self) -> None:
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error

    def register_verified_template(self, *, administrator_id: UUID):
        self._check()
        self.calls.append(("register", administrator_id))
        return self.preset, self.version

    def list_templates(self, *, limit: int, offset: int):
        self.calls.append(("list_templates", (limit, offset)))
        return [(self.preset, self.version)]

    def get_template(self, template_key: str, version: int):
        self._check()
        self.calls.append(("get_template", (template_key, version)))
        return self.preset, self.version

    def read_approved_preview(self):
        self._check()
        return b"synthetic-approved-preview"

    def upload_event_photo(
        self,
        event_id: UUID,
        content: bytes,
        *,
        administrator_id: UUID,
        select_as_primary: bool,
    ):
        self._check()
        self.calls.append(
            ("upload_photo", (event_id, content, administrator_id, select_as_primary))
        )
        return self.photo

    def list_event_photos(self, event_id: UUID, *, limit: int, offset: int):
        self._check()
        self.calls.append(("list_photos", (event_id, limit, offset)))
        return [self.photo]

    def select_event_photo(self, event_id: UUID, photo_id: UUID):
        self._check()
        self.calls.append(("select_photo", (event_id, photo_id)))
        return self.photo

    def read_event_photo(self, event_id: UUID, photo_id: UUID):
        self._check()
        self.calls.append(("read_photo", (event_id, photo_id)))
        return b"synthetic-photo-png"

    def create_design_revision(self, event_id: UUID, **kwargs: Any):
        self._check()
        self.calls.append(("create_revision", (event_id, kwargs)))
        return self.revision

    def get_latest_design_revision(self, event_id: UUID):
        self._check()
        return self.revision

    def list_design_revisions(self, event_id: UUID, *, limit: int, offset: int):
        self._check()
        return [self.revision]

    def approve_design_revision(self, event_id: UUID, revision_id: UUID, *, administrator_id: UUID):
        self._check()
        self.calls.append(("approve", (event_id, revision_id, administrator_id)))
        return self.approval

    def revoke_design_approval(self, event_id: UUID, approval_id: UUID, *, administrator_id: UUID):
        self._check()
        self.calls.append(("revoke", (event_id, approval_id, administrator_id)))
        self.approval.status = DesignApprovalStatus.REVOKED
        self.approval.revoked_by = administrator_id
        self.approval.revoked_at = datetime.now(UTC)
        return self.approval

    def render_design_preview(self, event_id: UUID, revision_id: UUID | None):
        self._check()
        self.calls.append(("preview", (event_id, revision_id)))
        return b"synthetic-design-preview"

    def generate_guest_card(self, event_id: UUID, guest_id: UUID, *, administrator_id: UUID):
        self._check()
        self.calls.append(("generate_one", (event_id, guest_id, administrator_id)))
        return self.card

    def generate_event_cards(
        self,
        event_id: UUID,
        *,
        administrator_id: UUID,
        guest_ids: list[UUID] | None,
        limit: int,
    ):
        self._check()
        self.calls.append(("generate_bulk", (event_id, administrator_id, guest_ids, limit)))
        return [self.card]

    def export_cards(self, event_id: UUID, card_ids: list[UUID]):
        self._check()
        self.calls.append(("export", (event_id, card_ids)))
        return b"synthetic-private-zip"

    def list_cards(self, event_id: UUID, *, status, limit: int, offset: int):
        self._check()
        self.calls.append(("list_cards", (event_id, status, limit, offset)))
        return [self.card]

    def get_card(self, event_id: UUID, card_id: UUID):
        self._check()
        self.calls.append(("get_card", (event_id, card_id)))
        return self.card

    def read_card_content(self, event_id: UUID, card_id: UUID):
        self._check()
        self.calls.append(("read_card", (event_id, card_id)))
        return b"synthetic-private-card"


def _build_app(settings, *, authenticated: bool = True):
    administrator = _administrator()
    service = FakeCardService(administrator.id)
    app = create_app(settings, database_runtime=FakeDatabaseRuntime())  # type: ignore[arg-type]
    app.dependency_overrides[get_card_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_administrator] = lambda: administrator
    else:
        app.dependency_overrides[get_authentication_service] = lambda: RejectAuthenticationService()
    return app, service, administrator


async def _request(app: object, method: str, path: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        return await client.request(method, path, **kwargs)


def test_template_and_photo_routes_are_authenticated_private_and_path_free(
    development_settings,
) -> None:
    app, service, administrator = _build_app(development_settings)
    event_id = service.event_id
    photo_id = service.photo.id
    calls = [
        ("POST", "/api/v1/templates/wedding_floral/versions/10/register", {}),
        ("GET", "/api/v1/templates?limit=10&offset=2", {}),
        ("GET", "/api/v1/templates/wedding_floral/versions/10", {}),
        ("GET", "/api/v1/templates/wedding_floral/versions/10/approved-preview", {}),
        (
            "POST",
            f"/api/v1/events/{event_id}/photos?select_as_primary=true",
            {"files": {"photo": ("private.jpg", b"photo-bytes", "image/jpeg")}},
        ),
        ("GET", f"/api/v1/events/{event_id}/photos?limit=5&offset=0", {}),
        ("POST", f"/api/v1/events/{event_id}/photos/{photo_id}/select", {}),
        ("GET", f"/api/v1/events/{event_id}/photos/{photo_id}/content", {}),
    ]
    responses = [
        asyncio.run(_request(app, method, path, **kwargs)) for method, path, kwargs in calls
    ]

    assert [response.status_code for response in responses] == [
        201,
        200,
        200,
        200,
        201,
        200,
        200,
        200,
    ]
    assert responses[3].headers["cache-control"] == "private, no-store"
    assert responses[7].headers["cache-control"] == "private, no-store"
    metadata = responses[4].json()
    assert "storage_key" not in metadata
    assert "C:/private" not in responses[4].text
    assert ("register", administrator.id) in service.calls
    assert any(call[0] == "upload_photo" for call in service.calls)


def test_design_and_generated_card_routes_cover_full_m6_contract(development_settings) -> None:
    app, service, administrator = _build_app(development_settings)
    event_id = service.event_id
    revision_id = service.revision.id
    approval_id = service.approval.id
    card_id = service.card.id
    payload = {
        "template_key": "wedding_floral",
        "template_version": 10,
        "event_photo_id": str(service.photo.id),
        "photo_fit": "COVER",
        "photo_focus_x": "0.5",
        "photo_focus_y": "0.5",
        "photo_zoom": "1.0",
    }
    calls = [
        ("PUT", f"/api/v1/events/{event_id}/card-design", {"json": payload}),
        ("GET", f"/api/v1/events/{event_id}/card-design", {}),
        ("GET", f"/api/v1/events/{event_id}/card-design/revisions", {}),
        ("POST", f"/api/v1/events/{event_id}/card-design/revisions/{revision_id}/approve", {}),
        ("POST", f"/api/v1/events/{event_id}/card-design/approvals/{approval_id}/revoke", {}),
        ("GET", f"/api/v1/events/{event_id}/card-design/preview?revision_id={revision_id}", {}),
        ("POST", f"/api/v1/events/{event_id}/cards/guests/{service.guest_id}", {}),
        (
            "POST",
            f"/api/v1/events/{event_id}/cards/generate",
            {"json": {"guest_ids": [str(service.guest_id)], "limit": 100}},
        ),
        (
            "POST",
            f"/api/v1/events/{event_id}/cards/export",
            {"json": {"card_ids": [str(card_id)]}},
        ),
        ("GET", f"/api/v1/events/{event_id}/cards?status=CURRENT&limit=5", {}),
        ("GET", f"/api/v1/events/{event_id}/cards/{card_id}", {}),
        ("GET", f"/api/v1/events/{event_id}/cards/{card_id}/content", {}),
    ]
    responses = [
        asyncio.run(_request(app, method, path, **kwargs)) for method, path, kwargs in calls
    ]

    assert [response.status_code for response in responses] == [
        201,
        200,
        200,
        200,
        200,
        200,
        201,
        200,
        200,
        200,
        200,
        200,
    ]
    generated = responses[6].json()
    assert generated["qr_credential_id"] == str(service.card.qr_credential_id)
    forbidden = ("storage_key", "C:/private", "token_hash", "Synthetic Guest")
    assert all(value not in responses[6].text for value in forbidden)
    assert responses[5].headers["cache-control"] == "private, no-store"
    assert responses[8].headers["content-type"] == "application/zip"
    assert responses[11].headers["cache-control"] == "private, no-store"
    assert ("generate_one", (event_id, service.guest_id, administrator.id)) in service.calls


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_all_m6_resources_reject_missing_or_invalid_authentication(
    development_settings,
    authorization: str | None,
) -> None:
    app, service, _ = _build_app(development_settings, authenticated=False)
    headers = {} if authorization is None else {"Authorization": authorization}
    response = asyncio.run(_request(app, "GET", "/api/v1/templates", headers=headers))
    card_response = asyncio.run(
        _request(
            app,
            "GET",
            f"/api/v1/events/{service.event_id}/cards/{service.card.id}/content",
            headers=headers,
        )
    )
    assert response.status_code == card_response.status_code == 401
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (CardResourceNotFoundError("card not found"), 404, "card not found"),
        (CardConflictError("inactive QR"), 409, "inactive QR"),
        (CardInputError("invalid photo"), 422, "invalid photo"),
        (CardStorageError("C:/private/secret.png"), 503, "storage is unavailable"),
        (CardPersistenceError("postgresql://secret"), 503, "service is unavailable"),
    ],
)
def test_m6_domain_errors_are_bounded_without_internal_details(
    development_settings,
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    app, service, _ = _build_app(development_settings)
    service.next_error = error
    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/templates/wedding_floral/versions/10/register",
        )
    )
    assert response.status_code == expected_status
    assert expected_detail in response.json()["detail"]
    assert "C:/private" not in response.text
    assert "postgresql://" not in response.text


def test_photo_upload_limit_and_bulk_request_bounds_are_enforced(development_settings) -> None:
    app, service, _ = _build_app(development_settings)
    oversized = b"x" * (development_settings.MAX_EVENT_PHOTO_UPLOAD_BYTES + 1)
    photo = asyncio.run(
        _request(
            app,
            "POST",
            f"/api/v1/events/{service.event_id}/photos",
            files={"photo": ("large.jpg", oversized, "image/jpeg")},
        )
    )
    duplicate = str(service.guest_id)
    bulk = asyncio.run(
        _request(
            app,
            "POST",
            f"/api/v1/events/{service.event_id}/cards/generate",
            json={"guest_ids": [duplicate, duplicate]},
        )
    )
    assert photo.status_code == 413
    assert bulk.status_code == 422
    assert not any(call[0] in {"upload_photo", "generate_bulk"} for call in service.calls)
