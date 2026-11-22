"""Registros, estados y errores de negocio de retrain_service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

# Un nombre de archivo simple: sin rutas, para que nadie pueda leer fuera de la carpeta de
# datasets (path traversal).
DATASET_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.parquet$")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"  # el trabajo terminó (el modelo pudo quedar aprobado o rechazado)
    FAILED = "failed"  # error de ejecución (datos inválidos, excepción, worker interrumpido)


class Activation(StrEnum):
    NOT_REQUESTED = "not_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # el modelo quedó approved; un admin puede activarlo a mano
    SKIPPED = "skipped"  # el modelo fue rechazado por la política de calidad


@dataclass(frozen=True)
class DatasetRecord:
    id: UUID
    version: str
    sha256: str
    row_count: int
    file_name: str
    class_distribution: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class JobRecord:
    id: UUID
    dataset_id: UUID
    dataset_version: str
    config_name: str
    requested_by: UUID
    status: JobStatus
    model_version: str | None
    activation: Activation
    error: str | None
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RetrainError(Exception):
    """Base de los errores de negocio de retrain_service."""


class DatasetFileNotFoundError(RetrainError):
    pass


class InvalidDatasetError(RetrainError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class DatasetAlreadyRegisteredError(RetrainError):
    """Ese mismo contenido (mismo SHA-256) ya está registrado con otra versión."""


class DatasetNotFoundError(RetrainError):
    pass


class UnknownConfigError(RetrainError):
    pass


class JobNotFoundError(RetrainError):
    pass
