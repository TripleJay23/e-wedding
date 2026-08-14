"""Shared FastAPI dependencies."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import AuthenticationSecurity
from app.models.administrator import Administrator
from app.repositories.administrators import AdministratorRepository
from app.repositories.events import EventRepository
from app.repositories.guests import GuestRepository
from app.services.authentication import AuthenticationRejectedError, AuthenticationService
from app.services.events import EventService

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="AdministratorBearer")


def get_database_session(request: Request) -> Generator[Session, None, None]:
    """Yield a request-scoped database session from the application runtime."""
    session = request.app.state.database.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_authentication_service(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
) -> AuthenticationService:
    """Build a request-scoped service over the process-scoped security policy."""
    security: AuthenticationSecurity = request.app.state.authentication_security
    return AuthenticationService(
        AdministratorRepository(session),
        security.passwords,
        security.access_tokens,
    )


def get_event_service(
    session: Annotated[Session, Depends(get_database_session)],
) -> EventService:
    """Build one request-scoped M3 service over the shared transaction session."""
    return EventService(EventRepository(session), GuestRepository(session))


def get_current_administrator(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> Administrator:
    """Require a valid bearer token for an active persisted SUPER_ADMIN."""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return service.current_administrator(credentials.credentials)
    except AuthenticationRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
