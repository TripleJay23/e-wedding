"""Administrator authentication routes."""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.dependencies import get_authentication_service, get_current_administrator
from app.models.administrator import Administrator
from app.schemas.auth import AccessTokenResponse, CurrentAdministratorResponse, LoginRequest
from app.services.authentication import AuthenticationRejectedError, AuthenticationService

router = APIRouter(prefix="/auth", tags=["auth"])


def _prevent_sensitive_response_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _reject_login() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/login", response_model=AccessTokenResponse)
def login(
    payload: LoginRequest,
    response: Response,
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> AccessTokenResponse:
    """Authenticate the administrator and issue a short-lived bearer token."""
    try:
        result = service.login(payload.username, payload.password.get_secret_value())
    except AuthenticationRejectedError:
        _reject_login()
    _prevent_sensitive_response_caching(response)
    return AccessTokenResponse(
        access_token=result.access_token.value,
        expires_in=result.access_token.expires_in_seconds,
    )


@router.get("/me", response_model=CurrentAdministratorResponse)
def current_administrator(
    response: Response,
    administrator: Annotated[Administrator, Depends(get_current_administrator)],
) -> CurrentAdministratorResponse:
    """Return the minimal persisted identity for the current administrator."""
    _prevent_sensitive_response_caching(response)
    return CurrentAdministratorResponse.model_validate(administrator)
