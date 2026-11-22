"""Autorización en FastAPI para los servicios que VERIFICAN tokens (ia_backend, retrain_service).

Uso:
    app.state.key_resolver = JwksClient(url).resolve
    install_auth_error_handlers(app)

    @router.post("/predict")
    def predict(principal: Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]):

El rol sale SIEMPRE del token verificado; nunca de un campo que envíe el cliente.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from svc_common.http import AppError, error_response
from svc_common.jwks import AuthUnavailableError
from svc_common.tokens import (
    API_AUDIENCE,
    KeyResolver,
    Role,
    TokenError,
    TokenExpiredError,
    decode_token,
)

BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role

    @property
    def user_id(self) -> UUID:
        """UUID del usuario. Solo existe en tokens de usuario (user/admin)."""
        if self.role is Role.SERVICE:
            raise ValueError("Un token de servicio no tiene usuario")
        return UUID(self.subject)


def bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError(
            401,
            "TOKEN_MISSING",
            "Falta el token de acceso.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def require_roles(*roles: Role, audience: str = API_AUDIENCE) -> Callable[..., Principal]:
    """Dependencia: verifica el Bearer token con la audiencia indicada y exige uno de los roles.

    401 si falta o no es válido; 403 si es válido pero el rol no alcanza.
    """
    allowed = frozenset(roles)

    def dependency(
        request: Request, authorization: Annotated[str | None, Header()] = None
    ) -> Principal:
        resolver = cast(KeyResolver, request.app.state.key_resolver)
        claims = decode_token(bearer_token(authorization), resolve_key=resolver, audience=audience)
        if claims.role not in allowed:
            raise AppError(403, "FORBIDDEN", "No tienes permiso para esta operación.")
        return Principal(subject=claims.subject, role=claims.role)

    return dependency


def install_auth_error_handlers(app: FastAPI) -> None:
    async def handle_token(_: Request, raw: Exception) -> JSONResponse:
        exc = cast(TokenError, raw)
        message = "El token expiró." if isinstance(exc, TokenExpiredError) else "Token inválido."
        return error_response(401, exc.code, message, headers=BEARER_CHALLENGE)

    async def handle_unavailable(_: Request, __: Exception) -> JSONResponse:
        return error_response(
            503, "AUTH_UNAVAILABLE", "No se pudo verificar la sesión. Intenta en unos segundos."
        )

    app.add_exception_handler(TokenError, handle_token)
    app.add_exception_handler(AuthUnavailableError, handle_unavailable)
