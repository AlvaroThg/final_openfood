"""Endpoints de auth_service.

    POST /auth/register          crea una cuenta con rol user                      → 201
    POST /auth/login             access token en el cuerpo + refresh en cookie    → 200
    POST /auth/refresh           rota el refresh (cookie) y entrega access nuevo  → 200
    POST /auth/logout            revoca la sesión y borra la cookie               → 204
    GET  /auth/me                datos del usuario del access token               → 200
    POST /auth/token             token de servicio (client credentials, interno)  → 200
    GET  /.well-known/jwks.json  clave pública para verificar los JWT             → 200
    GET  /health                 vivo y con base de datos                         → 200 | 503

Cookie del refresh token: httpOnly (JavaScript no la lee), SameSite=Strict (no viaja en
peticiones de otros sitios, lo que evita CSRF), Path=/auth (solo viaja a estos endpoints) y
Secure (solo HTTPS o localhost).

Las respuestas con tokens llevan Cache-Control: no-store.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from svc_common.http import AppError
from svc_common.rate_limit import SlidingWindowRateLimiter

from ..application.use_cases import IssuedSession
from ..container import Container
from ..domain.errors import RefreshTokenInvalidError, RefreshTokenReusedError
from ..schemas.auth import (
    ErrorResponse,
    HealthResponse,
    JwksResponse,
    LoginRequest,
    RegisterRequest,
    ServiceTokenRequest,
    TokenResponse,
    UserResponse,
)
from ..settings import AuthSettings
from .errors import domain_error_response

REFRESH_COOKIE = "refresh_token"  # noqa: S105 (nombre de la cookie, no un secreto)
REFRESH_COOKIE_PATH = "/auth"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

router = APIRouter()
_errors: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError(
            401,
            "TOKEN_MISSING",
            "Falta el token de acceso.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """IP del cliente. Sin proxies de confianza, la del par TCP. Con N proxies, cada uno agrega
    a X-Forwarded-For la IP de quien le habló: el cliente real es el N-ésimo desde el final
    (lo anterior pudo escribirlo el propio cliente)."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
    forwarded = [p for p in forwarded if p]
    return forwarded[-trusted_proxy_hops] if len(forwarded) >= trusted_proxy_hops else peer


def enforce_rate_limit(limiter: SlidingWindowRateLimiter, key: str) -> None:
    decision = limiter.hit(key)
    if not decision.allowed:
        raise AppError(
            429,
            "RATE_LIMITED",
            "Demasiados intentos. Espera un momento antes de volver a intentar.",
            headers={"Retry-After": str(decision.retry_after_s)},
        )


def set_refresh_cookie(response: Response, session: IssuedSession, settings: AuthSettings) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=settings.refresh_token_ttl_s,
        path=REFRESH_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response, settings: AuthSettings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.post(
    "/auth/register",
    status_code=201,
    response_model=UserResponse,
    responses={409: {"model": ErrorResponse}, **_errors},
)
def register(body: RegisterRequest, request: Request, c: ContainerDep) -> UserResponse:
    enforce_rate_limit(c.register_ip_limiter, client_ip(request, c.settings.trusted_proxy_hops))
    user = c.auth.register(body.email, body.password)
    return UserResponse(id=user.id, email=user.email, role=user.role.value)


@router.post("/auth/login", response_model=TokenResponse, responses=_errors)
def login(
    body: LoginRequest, request: Request, response: Response, c: ContainerDep
) -> TokenResponse:
    enforce_rate_limit(c.login_ip_limiter, client_ip(request, c.settings.trusted_proxy_hops))
    enforce_rate_limit(c.login_email_limiter, body.email.strip().lower())
    session = c.auth.login(body.email, body.password)
    set_refresh_cookie(response, session, c.settings)
    response.headers.update(NO_STORE)
    return TokenResponse(access_token=session.access_token, expires_in=session.expires_in)


@router.post("/auth/refresh", response_model=TokenResponse, responses=_errors)
def refresh(
    response: Response, c: ContainerDep, refresh_token: RefreshCookie = None
) -> TokenResponse | JSONResponse:
    try:
        session = c.auth.refresh(refresh_token)
    except (RefreshTokenInvalidError, RefreshTokenReusedError) as exc:
        # La cookie ya no sirve: se borra para que el navegador deje de enviarla.
        error = domain_error_response(exc)
        clear_refresh_cookie(error, c.settings)
        return error
    set_refresh_cookie(response, session, c.settings)
    response.headers.update(NO_STORE)
    return TokenResponse(access_token=session.access_token, expires_in=session.expires_in)


@router.post("/auth/logout", status_code=204, response_class=Response)
def logout(c: ContainerDep, refresh_token: RefreshCookie = None) -> Response:
    c.auth.logout(refresh_token)
    response = Response(status_code=204)
    clear_refresh_cookie(response, c.settings)
    return response


@router.get("/auth/me", response_model=UserResponse, responses=_errors)
def me(c: ContainerDep, token: Annotated[str, Depends(bearer_token)]) -> UserResponse:
    user = c.auth.current_user(token)
    return UserResponse(id=user.id, email=user.email, role=user.role.value)


@router.post("/auth/token", response_model=TokenResponse, responses=_errors)
def service_token(body: ServiceTokenRequest, response: Response, c: ContainerDep) -> TokenResponse:
    issued = c.auth.issue_service_token(body.client_id, body.client_secret)
    response.headers.update(NO_STORE)
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in)


@router.get("/.well-known/jwks.json", response_model=JwksResponse)
def jwks(response: Response, c: ContainerDep) -> JwksResponse:
    # Los verificadores pueden cachearla 5 minutos.
    response.headers["Cache-Control"] = "public, max-age=300"
    return JwksResponse.model_validate(c.issuer.jwks())


@router.get("/health", response_model=HealthResponse, responses={503: {"model": ErrorResponse}})
def health(c: ContainerDep) -> HealthResponse:
    c.health_check()
    return HealthResponse(status="ok")
