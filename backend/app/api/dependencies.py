"""Shared FastAPI dependencies."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.cards.package import VerifiedTemplateStorage
from app.cards.renderer import ManifestRenderer
from app.cards.storage import EventPhotoStorage, GeneratedCardStorage
from app.core.security import AuthenticationSecurity
from app.imports.storage import ImportEvidenceStorage
from app.imports.workbook import WorkbookParser
from app.models.administrator import Administrator
from app.qr.storage import QrImageStorage
from app.repositories.administrators import AdministratorRepository
from app.repositories.attendance import AttendanceRepository
from app.repositories.attendance_corrections import AttendanceCorrectionRepository
from app.repositories.cards import CardRepository
from app.repositories.events import EventRepository
from app.repositories.guests import GuestRepository
from app.repositories.imports import ImportRepository
from app.repositories.qr import QrCredentialRepository
from app.services.attendance import AttendanceService
from app.services.attendance_corrections import AttendanceCorrectionService
from app.services.authentication import AuthenticationRejectedError, AuthenticationService
from app.services.cards import CardService
from app.services.events import EventService
from app.services.imports import ImportService
from app.services.qr import QrCredentialService

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


def get_attendance_service(
    session: Annotated[Session, Depends(get_database_session)],
) -> AttendanceService:
    """Build one request-scoped M7 service over a single atomic session."""

    return AttendanceService(AttendanceRepository(session))


def get_attendance_correction_service(
    session: Annotated[Session, Depends(get_database_session)],
) -> AttendanceCorrectionService:
    """Build one request-scoped M8 service over a single atomic session."""

    return AttendanceCorrectionService(AttendanceCorrectionRepository(session))


def get_import_service(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
) -> ImportService:
    """Build one request-scoped M4 import service with private storage controls."""
    settings = request.app.state.settings
    return ImportService(
        EventRepository(session),
        GuestRepository(session),
        ImportRepository(session),
        WorkbookParser(maximum_candidate_rows=settings.MAX_IMPORT_CANDIDATE_ROWS),
        ImportEvidenceStorage(settings.IMPORT_EVIDENCE_STORAGE_ROOT),
        maximum_workbook_bytes=settings.MAX_IMPORT_WORKBOOK_BYTES,
    )


def get_qr_credential_service(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
) -> QrCredentialService:
    """Build one request-scoped M5 service over protected QR image storage."""
    root = request.app.state.settings.QR_IMAGE_STORAGE_ROOT
    assert root is not None
    return QrCredentialService(QrCredentialRepository(session), QrImageStorage(root))


def get_card_service(
    request: Request,
    session: Annotated[Session, Depends(get_database_session)],
) -> CardService:
    """Build one request-scoped M6 service over protected private roots."""
    settings = request.app.state.settings
    template_root = settings.TEMPLATE_STORAGE_ROOT
    photo_root = settings.EVENT_PHOTO_STORAGE_ROOT
    generated_root = settings.GENERATED_CARD_STORAGE_ROOT
    qr_root = settings.QR_IMAGE_STORAGE_ROOT
    assert template_root is not None
    assert photo_root is not None
    assert generated_root is not None
    assert qr_root is not None
    qr_service = QrCredentialService(
        QrCredentialRepository(session),
        QrImageStorage(qr_root),
    )
    return CardService(
        CardRepository(session),
        VerifiedTemplateStorage(template_root),
        EventPhotoStorage(photo_root),
        GeneratedCardStorage(generated_root),
        qr_service,
        ManifestRenderer(),
        maximum_event_photo_bytes=settings.MAX_EVENT_PHOTO_UPLOAD_BYTES,
    )


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
