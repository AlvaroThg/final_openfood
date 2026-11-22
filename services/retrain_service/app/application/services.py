"""Casos de uso de la API de reentrenamiento (solo admin).

register_dataset(nombre):
    nombre simple (sin rutas) y existente en DATASETS_DIR → si no, error
    leer el parquet y validarlo con nutriscore.cleaning.validate_clean_frame (mismo contrato
    que el entrenamiento) → InvalidDatasetError con la lista de problemas
    calcular SHA-256; si ese contenido ya está registrado → DatasetAlreadyRegisteredError
    registrar con la siguiente versión vN (inmutable: nunca se sobrescribe)

enqueue_job(dataset, config):
    la configuración debe estar permitida y existir en CONFIGS_DIR; el dataset debe existir
    insertar en retrain_jobs con estado 'queued' y responder: el worker lo toma después
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd

from nutriscore import schema
from nutriscore.artifacts import sha256_file
from nutriscore.cleaning import validate_clean_frame

from ..domain.model import (
    DATASET_FILE_NAME,
    DatasetFileNotFoundError,
    DatasetNotFoundError,
    DatasetRecord,
    InvalidDatasetError,
    JobNotFoundError,
    JobRecord,
    UnknownConfigError,
)
from ..infrastructure.pg_repositories import PgDatasetRepository, PgJobRepository
from ..settings import RetrainSettings

logger = logging.getLogger(__name__)


def dataset_path(settings: RetrainSettings, file_name: str) -> Path:
    """Ruta segura dentro de DATASETS_DIR (rechaza rutas y nombres raros)."""
    if not DATASET_FILE_NAME.fullmatch(file_name):
        raise DatasetFileNotFoundError(file_name)
    path = settings.datasets_dir / file_name
    if not path.is_file():
        raise DatasetFileNotFoundError(file_name)
    return path


def config_path(settings: RetrainSettings, config_name: str) -> Path:
    if config_name not in settings.training_configs:
        raise UnknownConfigError(config_name)
    path = settings.configs_dir / f"{config_name}.toml"
    if not path.is_file():
        raise UnknownConfigError(config_name)
    return path


class DatasetService:
    def __init__(self, *, settings: RetrainSettings, repo: PgDatasetRepository) -> None:
        self._settings = settings
        self._repo = repo

    def list(self) -> list[DatasetRecord]:
        return self._repo.list()

    def register(self, file_name: str, *, admin_id: UUID | None) -> DatasetRecord:
        path = dataset_path(self._settings, file_name)
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:  # parquet corrupto o no es parquet
            raise InvalidDatasetError(
                [f"No se pudo leer el parquet: {type(exc).__name__}"]
            ) from exc
        problems = validate_clean_frame(frame)
        if problems:
            raise InvalidDatasetError(problems)

        report_path = path.with_suffix(".report.json")
        report: dict[str, Any] = (
            json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        )
        distribution = {
            str(k): int(v) for k, v in frame[schema.TARGET].value_counts().sort_index().items()
        }
        record = self._repo.add(
            sha256=sha256_file(path),
            row_count=len(frame),
            file_name=file_name,
            cleaning_report=report,
            class_distribution=distribution,
            created_by=admin_id,
        )
        logger.info(
            "dataset_registered", extra={"dataset_version": record.version, "rows": len(frame)}
        )
        return record


class JobService:
    def __init__(
        self,
        *,
        settings: RetrainSettings,
        jobs: PgJobRepository,
        datasets: PgDatasetRepository,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._datasets = datasets

    def configs(self) -> list[str]:
        return [
            name
            for name in self._settings.training_configs
            if (self._settings.configs_dir / f"{name}.toml").is_file()
        ]

    def enqueue(self, *, dataset_version: str, config_name: str, admin_id: UUID) -> JobRecord:
        config_path(self._settings, config_name)
        dataset = self._datasets.get(dataset_version)
        if dataset is None:
            raise DatasetNotFoundError(dataset_version)
        job = self._jobs.add(dataset_id=dataset.id, config_name=config_name, requested_by=admin_id)
        logger.info("job_enqueued", extra={"job_id": str(job.id), "config_name": config_name})
        return job

    def get(self, job_id: UUID, *, admin_id: UUID) -> JobRecord:
        job = self._jobs.get(job_id, ctx={"user_id": admin_id, "role": "admin"})
        if job is None:
            raise JobNotFoundError(str(job_id))
        return job

    def list(self, *, admin_id: UUID) -> list[JobRecord]:
        return self._jobs.list(ctx={"user_id": admin_id, "role": "admin"})
