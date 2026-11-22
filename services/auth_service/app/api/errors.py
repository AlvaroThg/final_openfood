"""Traducción de errores de negocio y de token a respuestas HTTP con el formato común.

`code` es el contrato con el frontend (estable). Los mensajes no revelan detalles internos:
un login fallido nunca dice si el email existe.
"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from svc_common.http import error_response
from svc_common.tokens import TokenError, TokenExpiredError

from ..domain.errors import (
    AuthError,
    EmailTakenError,
    InitialAdminConflictError,
    InvalidClientError,
    InvalidCredentialsError,
    InvalidEmailError,
    RefreshTokenInvalidError,
    RefreshTokenReusedError,
    UserNotFoundError,
    WeakPasswordError,
)
from ..domain.model import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer error="invalid_token"'}

DOMAIN_ERRORS: dict[type[AuthError], tuple[int, str, str]] = {
    InvalidEmailError: (422, "INVALID_EMAIL", "El email no tiene un formato válido."),
    WeakPasswordError: (
        422,
        "WEAK_PASSWORD",
        f"La contraseña debe tener entre {PASSWORD_MIN_LENGTH} y {PASSWORD_MAX_LENGTH} caracteres.",
    ),
    EmailTakenError: (409, "EMAIL_TAKEN", "Ya existe una cuenta con ese email."),
    InvalidCredentialsError: (401, "INVALID_CREDENTIALS", "Email o contraseña incorrectos."),
    RefreshTokenInvalidError: (401, "REFRESH_INVALID", "La sesión expiró. Inicia sesión de nuevo."),
    RefreshTokenReusedError: (
        401,
        "REFRESH_REUSED",
        "La sesión se cerró por seguridad. Inicia sesión de nuevo.",
    ),
    InvalidClientError: (401, "INVALID_CLIENT", "Credenciales de servicio inválidas."),
    UserNotFoundError: (401, "TOKEN_INVALID", "Token inválido."),
    InitialAdminConflictError: (500, "INTERNAL_ERROR", "Error interno del servidor."),
}


def domain_error_response(exc: AuthError) -> JSONResponse:
    status, code, message = DOMAIN_ERRORS.get(
        type(exc), (500, "INTERNAL_ERROR", "Error interno del servidor.")
    )
    return error_response(status, code, message)


def install_auth_error_handlers(app: FastAPI) -> None:
    # Cada manejador se registra solo para su clase, por eso el cast es seguro.
    async def handle_domain(_: Request, exc: Exception) -> JSONResponse:
        return domain_error_response(cast(AuthError, exc))

    async def handle_token(_: Request, raw: Exception) -> JSONResponse:
        exc = cast(TokenError, raw)
        message = "El token expiró." if isinstance(exc, TokenExpiredError) else "Token inválido."
        return error_response(401, exc.code, message, headers=BEARER_CHALLENGE)

    app.add_exception_handler(AuthError, handle_domain)
    app.add_exception_handler(TokenError, handle_token)
