"""Top-level API router."""

from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.events import router as events_router
from app.api.routes.guests import router as guests_router
from app.api.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(events_router)
api_router.include_router(guests_router)
api_router.include_router(health_router)
