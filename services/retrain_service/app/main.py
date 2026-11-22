"""API de retrain_service.

En el contenedor:  uvicorn retrain_service.app.main:create_app --factory --host 0.0.0.0
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from svc_common.auth import install_auth_error_handlers
from svc_common.http import RequestContextMiddleware, install_error_handlers
from svc_common.logging import configure_logging

from .api.routes import install_retrain_error_handlers, router
from .container import Container, build_container
from .settings import RetrainSettings

VERSION = "1.0.0"


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        settings = RetrainSettings.from_env()
        configure_logging("retrain_api", settings.log_level)
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
        title="retrain_service",
        version=VERSION,
        summary="Datasets y trabajos de entrenamiento (solo administradores).",
        lifespan=lifespan,
    )
    app.state.container = resolved
    app.state.key_resolver = resolved.key_resolver
    install_error_handlers(app)
    install_auth_error_handlers(app)
    install_retrain_error_handlers(app)
    app.include_router(router)
    app.add_middleware(RequestContextMiddleware)
    return app
