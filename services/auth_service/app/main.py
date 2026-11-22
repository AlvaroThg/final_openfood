"""Punto de entrada de auth_service.

En el contenedor:  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
En las pruebas:    create_app(container) con piezas en memoria o con Postgres de prueba.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from svc_common.http import REQUEST_ID_HEADER, RequestContextMiddleware, install_error_handlers
from svc_common.logging import configure_logging

from .api.errors import install_auth_error_handlers
from .api.routes import router
from .container import Container, build_container
from .settings import AuthSettings

VERSION = "1.0.0"


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        settings = AuthSettings.from_env()
        configure_logging("auth_service", settings.log_level)
        container = build_container(settings)
    resolved = container

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved.startup()
        try:
            yield
        finally:
            resolved.shutdown()

    app = FastAPI(
        title="auth_service",
        version=VERSION,
        summary="Registro, login, sesiones y emisión de JWT RS256 (JWKS).",
        lifespan=lifespan,
    )
    app.state.container = resolved
    install_error_handlers(app)
    install_auth_error_handlers(app)
    app.include_router(router)

    if resolved.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
        )
    # El último middleware agregado es el más externo: el request_id envuelve todo.
    app.add_middleware(RequestContextMiddleware)
    return app
