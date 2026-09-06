"""Health endpoint tests."""

import asyncio

import httpx

from app.core.settings import Settings
from app.main import create_app


class FakeDatabaseRuntime:
    """Connection-free runtime used by HTTP unit tests."""

    closed = False

    def close(self) -> None:
        self.closed = True


def test_health_remains_available_with_m9_route_inventory(development_settings: Settings) -> None:
    runtime = FakeDatabaseRuntime()
    app = create_app(development_settings, database_runtime=runtime)  # type: ignore[arg-type]

    async def exercise_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://synthetic.test",
            ) as client,
        ):
            return await client.get("/api/v1/health")

    response = asyncio.run(exercise_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert runtime.closed is True

    assert set(app.openapi()["paths"]) == {
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/attendance/admit",
        "/api/v1/attendance/inspect",
        "/api/v1/attendance/scan",
        "/api/v1/events",
        "/api/v1/events/{event_id}",
        "/api/v1/events/{event_id}/guest-fields",
        "/api/v1/events/{event_id}/attendance",
        "/api/v1/events/{event_id}/attendance/corrections/{correction_id}",
        "/api/v1/events/{event_id}/attendance/corrections/{correction_id}/audit-detail",
        "/api/v1/events/{event_id}/attendance/effective",
        "/api/v1/events/{event_id}/attendance/guests/{guest_id}/corrections",
        "/api/v1/events/{event_id}/attendance/guests/{guest_id}/effective",
        "/api/v1/events/{event_id}/card-design",
        "/api/v1/events/{event_id}/card-design/approvals/{approval_id}/revoke",
        "/api/v1/events/{event_id}/card-design/preview",
        "/api/v1/events/{event_id}/card-design/revisions",
        "/api/v1/events/{event_id}/card-design/revisions/{revision_id}/approve",
        "/api/v1/events/{event_id}/cards",
        "/api/v1/events/{event_id}/cards/export",
        "/api/v1/events/{event_id}/cards/generate",
        "/api/v1/events/{event_id}/cards/guests/{guest_id}",
        "/api/v1/events/{event_id}/cards/{card_id}",
        "/api/v1/events/{event_id}/cards/{card_id}/content",
        "/api/v1/events/{event_id}/guests",
        "/api/v1/events/{event_id}/guests/{guest_id}",
        "/api/v1/events/{event_id}/imports",
        "/api/v1/events/{event_id}/imports/{batch_id}",
        "/api/v1/events/{event_id}/imports/{batch_id}/progress",
        "/api/v1/events/{event_id}/imports/{batch_id}/reconciliation",
        "/api/v1/events/{event_id}/imports/{batch_id}/revalidate",
        "/api/v1/events/{event_id}/imports/{batch_id}/rows",
        "/api/v1/events/{event_id}/imports/{batch_id}/rows/{row_id}",
        "/api/v1/events/{event_id}/imports/{batch_id}/rows/{row_id}/revalidate",
        "/api/v1/events/{event_id}/qr-codes",
        "/api/v1/events/{event_id}/reports/attendance",
        "/api/v1/events/{event_id}/reports/export/{output_format}",
        "/api/v1/events/{event_id}/reports/summary",
        "/api/v1/events/{event_id}/scan-attempts",
        "/api/v1/events/{event_id}/photos",
        "/api/v1/events/{event_id}/photos/{photo_id}/content",
        "/api/v1/events/{event_id}/photos/{photo_id}/select",
        "/api/v1/guests/{guest_id}/qr-code",
        "/api/v1/guests/{guest_id}/attendance",
        "/api/v1/guests/{guest_id}/qr-code/activate",
        "/api/v1/guests/{guest_id}/qr-code/deactivate",
        "/api/v1/guests/{guest_id}/qr-code/regenerate",
        "/api/v1/health",
        "/api/v1/templates",
        "/api/v1/templates/wedding_floral/versions/10/approved-preview",
        "/api/v1/templates/wedding_floral/versions/10/register",
        "/api/v1/templates/{template_key}/versions/{template_version}",
    }
