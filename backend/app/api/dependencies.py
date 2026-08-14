"""Shared FastAPI dependencies."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session


def get_database_session(request: Request) -> Generator[Session, None, None]:
    """Yield a request-scoped database session from the application runtime."""
    session = request.app.state.database.session_factory()
    try:
        yield session
    finally:
        session.close()
