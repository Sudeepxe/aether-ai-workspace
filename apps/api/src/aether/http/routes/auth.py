"""Auth routes (Blueprint §4.3, Sprint 1 slice): register, login, refresh,
logout. Domain errors propagate to the exception handlers registered in
http/app.py — routes stay free of status-code mapping. /v1/me lives in
routes/me.py (it's a distinct resource, not an auth action).
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Response, status

from aether.app.auth.login_user import LoginUserCommand
from aether.app.auth.logout_user import LogoutUserCommand
from aether.app.auth.refresh_session import RefreshSessionCommand
from aether.app.auth.register_user import RegisterUserCommand
from aether.app.password_reset.request_password_reset import RequestPasswordResetCommand
from aether.app.password_reset.reset_password import ResetPasswordCommand
from aether.domain.errors import InvalidRefreshTokenError
from aether.http.authz import AuthRequirement, route_auth
from aether.http.composition import Container
from aether.http.cookies import REFRESH_COOKIE_NAME, clear_refresh_cookie, set_refresh_cookie
from aether.http.deps import (
    AuthenticatedSession,
    device_fingerprint,
    get_container,
    get_current_session,
)
from aether.http.schemas.auth import (
    AccessTokenResponse,
    ConfirmPasswordResetRequest,
    LoginRequest,
    RegisterRequest,
    RequestPasswordResetRequest,
    UserResponse,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=route_auth(AuthRequirement.PUBLIC),
)
async def register(
    body: RegisterRequest, container: Container = Depends(get_container)
) -> UserResponse:
    user = await container.register_user.execute(
        RegisterUserCommand(
            email=body.email, password=body.password, display_name=body.display_name
        )
    )
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name)


@router.post(
    "/login", response_model=AccessTokenResponse, openapi_extra=route_auth(AuthRequirement.PUBLIC)
)
async def login(
    body: LoginRequest,
    response: Response,
    container: Container = Depends(get_container),
    fingerprint: str = Depends(device_fingerprint),
) -> AccessTokenResponse:
    result = await container.login_user.execute(
        LoginUserCommand(email=body.email, password=body.password, device_fingerprint=fingerprint)
    )
    set_refresh_cookie(
        response, value=result.refresh_token, max_age_seconds=container.refresh_ttl_seconds
    )
    return AccessTokenResponse(access_token=result.access_token)


@router.post(
    "/refresh", response_model=AccessTokenResponse, openapi_extra=route_auth(AuthRequirement.PUBLIC)
)
async def refresh(
    response: Response,
    container: Container = Depends(get_container),
    fingerprint: str = Depends(device_fingerprint),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> AccessTokenResponse:
    if refresh_token is None:
        raise InvalidRefreshTokenError("no refresh cookie presented")

    result = await container.refresh_session.execute(
        RefreshSessionCommand(raw_refresh_token=refresh_token, device_fingerprint=fingerprint)
    )
    if result.refresh_token is not None:
        set_refresh_cookie(
            response, value=result.refresh_token, max_age_seconds=container.refresh_ttl_seconds
        )
    return AccessTokenResponse(access_token=result.access_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.AUTHENTICATED),
)
async def logout(
    response: Response,
    container: Container = Depends(get_container),
    session: AuthenticatedSession = Depends(get_current_session),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> None:
    await container.logout_user.execute(
        LogoutUserCommand(
            user_id=session.user_id,
            jti=session.jti,
            access_token_expires_at=session.expires_at,
            raw_refresh_token=refresh_token,
        )
    )
    clear_refresh_cookie(response)


@router.post(
    "/password-reset:request",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=route_auth(AuthRequirement.PUBLIC),
)
async def request_password_reset(
    body: RequestPasswordResetRequest, container: Container = Depends(get_container)
) -> None:
    # 202 unconditionally — RequestPasswordReset itself is what makes
    # this enumeration-safe (a no-op for an unknown/OAuth-only email);
    # the route never learns, let alone reports, which branch it took.
    await container.request_password_reset.execute(RequestPasswordResetCommand(email=body.email))


@router.post(
    "/password-reset:confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.PUBLIC),
)
async def confirm_password_reset(
    body: ConfirmPasswordResetRequest, container: Container = Depends(get_container)
) -> None:
    await container.reset_password.execute(
        ResetPasswordCommand(raw_token=body.token, new_password=body.new_password)
    )
