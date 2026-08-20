"""FastAPI application factory through M5 secure QR credentials."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.router import api_router
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings, get_settings
from app.db.runtime import DatabaseRuntime


def create_app(
    settings: Settings | None = None,
    *,
    database_runtime: DatabaseRuntime | None = None,
    authentication_security: AuthenticationSecurity | None = None,
) -> FastAPI:
    """Build one application without connecting to PostgreSQL at import time."""

    resolved_settings = settings or get_settings()
    runtime = database_runtime or DatabaseRuntime(resolved_settings)
    security = authentication_security or AuthenticationSecurity.from_settings(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime.close()

    application = FastAPI(
        title=resolved_settings.APP_NAME,
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = runtime
    application.state.authentication_security = security
    application.include_router(api_router, prefix=resolved_settings.API_V1_PREFIX)
    return application
