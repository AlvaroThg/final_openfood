"""Errores de negocio de ia_backend → respuestas HTTP con el formato común."""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from svc_common.http import error_response

from ..domain.model import (
    ActivationConflictError,
    IaError,
    InconsistentInputError,
    ModelLoadError,
    ModelNotActivatableError,
    ModelUnavailableError,
    ModelVersionNotFoundError,
)

logger = logging.getLogger(__name__)

ERRORS: dict[type[IaError], tuple[int, str, str]] = {
    ModelUnavailableError: (
        503,
        "MODEL_UNAVAILABLE",
        "Todavía no hay un modelo activo. Un administrador debe activar una versión aprobada.",
    ),
    ModelVersionNotFoundError: (404, "MODEL_NOT_FOUND", "No existe esa versión de modelo."),
    ModelNotActivatableError: (
        409,
        "MODEL_NOT_ACTIVATABLE",
        "Solo se puede activar una versión aprobada o archivada.",
    ),
    ModelLoadError: (
        422,
        "MODEL_ARTIFACT_INVALID",
        "El artefacto no pasó la verificación (hash, dependencias o golden). "
        "Se mantiene el modelo anterior.",
    ),
    ActivationConflictError: (
        409,
        "ACTIVATION_CONFLICT",
        "La versión cambió de estado durante la activación. Vuelve a intentarlo.",
    ),
    InconsistentInputError: (
        400,
        "INCONSISTENT_INPUT",
        "Los valores no son físicamente posibles. Revisa los nutrientes indicados.",
    ),
}


def install_ia_error_handlers(app: FastAPI) -> None:
    async def handle(_: Request, raw: Exception) -> JSONResponse:
        exc = cast(IaError, raw)
        status, code, message = ERRORS.get(
            type(exc), (500, "INTERNAL_ERROR", "Error interno del servidor.")
        )
        if isinstance(exc, ModelLoadError):
            # El detalle (qué verificación falló) va al log, no a la respuesta.
            logger.error("model_load_rejected", extra={"detail": str(exc)})
        details = exc.violations if isinstance(exc, InconsistentInputError) else None
        return error_response(status, code, message, details=details)

    app.add_exception_handler(IaError, handle)
