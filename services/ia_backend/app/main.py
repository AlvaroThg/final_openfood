"""Punto de entrada de ia_backend.

En el contenedor:  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from svc_common.auth import install_auth_error_handlers
from svc_common.http import REQUEST_ID_HEADER, RequestContextMiddleware, install_error_handlers
from svc_common.logging import configure_logging

from .api.errors import install_ia_error_handlers
from .api.routes import router
from .container import Container, build_container
from .settings import IaSettings

VERSION = "1.0.0"


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        settings = IaSettings.from_env()
        configure_logging("ia_backend", settings.log_level)
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
        title="ia_backend",
        version=VERSION,
        summary="Predicción del Nutri-Score, historial y activación de modelos aprobados.",
        lifespan=lifespan,
    )
    app.state.container = resolved
    app.state.key_resolver = resolved.key_resolver
    install_error_handlers(app)
    install_auth_error_handlers(app)
    install_ia_error_handlers(app)
    app.include_router(router)

    if resolved.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
        )
    app.add_middleware(RequestContextMiddleware)
    return app
