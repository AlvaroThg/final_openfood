"""HTTP transversal: X-Request-ID, log de acceso y formato único de errores.

Formato de TODO error que sale de un servicio:

    {"error": {"code": "TOKEN_EXPIRED", "message": "texto para humanos", "request_id": "..."}}

- `code` es estable y es lo que interpreta el frontend; `message` puede cambiar.
- Los errores de validación (422) incluyen `details` con ubicación y motivo, pero NUNCA el valor
  recibido: FastAPI lo devuelve por defecto y podría reflejar una contraseña.
- Un error inesperado responde 500 genérico y se registra con traceback en el log, no en la
  respuesta.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any, cast

import psycopg
import psycopg_pool
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from svc_common.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}

access_logger = logging.getLogger("http.access")
error_logger = logging.getLogger("http.error")


class AppError(Exception):
    """Error que se traduce tal cual a una respuesta HTTP con el formato común."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: Mapping[str, str] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status_code, headers=dict(headers or {}))


class RequestContextMiddleware:
    """Middleware ASGI: asigna el request_id, lo devuelve en la respuesta y registra el acceso.

    - Reutiliza el X-Request-ID entrante si es válido (así se sigue una operación entre
      servicios); si no, genera uno nuevo.
    - Si una excepción escapa de la aplicación, responde 500 con el formato común (y el
      request_id) y la registra con traceback. No la vuelve a lanzar.
    - Registra método, ruta, estado y duración. Nunca la query string ni los headers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, REQUEST_ID_HEADER)
        valid = incoming is not None and _VALID_REQUEST_ID.fullmatch(incoming) is not None
        request_id = incoming if valid and incoming else uuid.uuid4().hex
        context_token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            error_logger.exception("unhandled_error")
            if not response_started:
                response = error_response(500, "INTERNAL_ERROR", "Error interno del servidor.")
                await response(scope, receive, send_with_request_id)
        finally:
            access_logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id_var.reset(context_token)


def install_error_handlers(app: FastAPI) -> None:
    """Registra los manejadores que producen el formato común de errores."""

    # Cada manejador se registra solo para su clase, por eso el cast es seguro.
    async def handle_app_error(_: Request, exc: Exception) -> JSONResponse:
        error = cast(AppError, exc)
        return error_response(error.status_code, error.code, error.message, headers=error.headers)

    async def handle_validation(_: Request, exc: Exception) -> JSONResponse:
        details = [
            {
                "loc": list(err.get("loc", ())),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in cast(RequestValidationError, exc).errors()
        ]
        return error_response(
            422, "VALIDATION_ERROR", "Datos de entrada inválidos.", details=details
        )

    async def handle_http(_: Request, raw: Exception) -> JSONResponse:
        exc = cast(StarletteHTTPException, raw)
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code
        return error_response(exc.status_code, code, message, headers=exc.headers)

    async def handle_db_unavailable(_: Request, exc: Exception) -> JSONResponse:
        error_logger.warning("db_unavailable", extra={"error_type": type(exc).__name__})
        return error_response(503, "DB_UNAVAILABLE", "La base de datos no está disponible.")

    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation)
    app.add_exception_handler(StarletteHTTPException, handle_http)
    app.add_exception_handler(psycopg.OperationalError, handle_db_unavailable)
    app.add_exception_handler(psycopg_pool.PoolTimeout, handle_db_unavailable)


def _header(scope: Scope, name: str) -> str | None:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key == target:
            decoded: str = value.decode("latin-1")
            return decoded
    return None
