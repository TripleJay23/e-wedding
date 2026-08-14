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


def test_health_is_the_only_foundation_route(development_settings: Settings) -> None:
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

    assert set(app.openapi()["paths"]) == {"/api/v1/health"}
