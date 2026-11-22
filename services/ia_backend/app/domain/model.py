"""Registros y errores de negocio de ia_backend."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from nutriscore.lifecycle import ModelStatus


@dataclass(frozen=True)
class ModelVersionRecord:
    """Fila de model_versions (lo que ia_backend necesita de ella)."""

    version: str
    status: ModelStatus
    algorithm: str
    dataset_version: str
    artifact_sha256: str | None
    metrics: dict[str, Any] | None
    gate: dict[str, Any] | None
    quality_policy_version: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PredictionRecord:
    id: UUID
    user_id: UUID
    model_version: str
    input: dict[str, Any]
    grade: str
    probabilities: dict[str, float]
    inference_ms: float
    created_at: datetime


class IaError(Exception):
    """Base de los errores de negocio de ia_backend."""


class ModelUnavailableError(IaError):
    """No hay un modelo activo cargado: no se puede predecir."""


class ModelVersionNotFoundError(IaError):
    pass


class ModelNotActivatableError(IaError):
    """La versión no está en un estado desde el que se pueda activar (approved/archived)."""


class ModelLoadError(IaError):
    """El artefacto no pasó la verificación (hash, dependencias o golden)."""


class ActivationConflictError(IaError):
    """Otro proceso cambió el estado de la versión durante la activación."""


class InconsistentInputError(IaError):
    """La entrada viola una regla física (p. ej. grasas saturadas > grasas)."""

    def __init__(self, violations: list[dict[str, str]]) -> None:
        super().__init__("entrada inconsistente")
        self.violations = violations
