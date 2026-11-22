"""Ejecución de UN trabajo de entrenamiento. Mismo flujo que ml/scripts/train.py, con registro.

run(job):
    dataset   = fila de datasets; el archivo debe tener el SHA-256 registrado (integridad)
    frame     = parquet validado con el contrato (validate_clean_frame)
    config    = ml/configs/<config>.toml (permitida)
    versión   = siguiente MINOR entre las registradas y las carpetas existentes (nunca pisa)
    model_versions: candidate → evaluating
    entrenar con 'train', evaluar con 'val' (decide) y reportar 'test' (congelado)
    guardar el artefacto inmutable (+ golden) en MODELS_DIR
    política de calidad (validación, no regresión frente al activo, tamaño, paridad)
    model_versions: evaluating → approved | rejected (con métricas, hash y decisión)
    si quedó approved → solicitar la activación a ia_backend (no activar aquí)
Si algo falla después de crear la versión, la versión queda 'rejected' con el motivo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import pandas as pd

from nutriscore.artifacts import (
    artifact_dir,
    dependency_versions,
    reload_parity,
    save_artifact,
    sha256_file,
)
from nutriscore.cleaning import validate_clean_frame
from nutriscore.lifecycle import next_minor_version
from nutriscore.quality import CURRENT_POLICY, QualityPolicy, evaluate_gate
from nutriscore.splits import split_frame
from nutriscore.training import TrainingConfig, build_metadata, train_and_evaluate

from ..app.application.services import config_path
from ..app.domain.model import Activation, JobRecord, JobStatus
from ..app.infrastructure.pg_repositories import (
    PgDatasetRepository,
    PgJobRepository,
    PgModelVersionRepository,
)
from ..app.settings import RetrainSettings

logger = logging.getLogger(__name__)
GOLDEN_ROWS = 50


class Activator(Protocol):
    def request_activation(self, version: str) -> bool: ...


class JobFailedError(Exception):
    """Error esperable de un trabajo (datos o configuración); se guarda en el job."""


@dataclass(frozen=True)
class Outcome:
    model_version: str | None
    approved: bool
    activation: Activation


class Trainer:
    def __init__(
        self,
        *,
        settings: RetrainSettings,
        jobs: PgJobRepository,
        datasets: PgDatasetRepository,
        models: PgModelVersionRepository,
        activator: Activator,
        policy: QualityPolicy = CURRENT_POLICY,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._datasets = datasets
        self._models = models
        self._activator = activator
        self._policy = policy

    def run(self, job: JobRecord) -> Outcome:
        """Ejecuta el trabajo y deja el job en succeeded o failed. Nunca propaga la excepción:
        un trabajo fallido no debe tumbar al worker."""
        try:
            outcome = self._run(job)
        except Exception as exc:
            message = (
                str(exc) if isinstance(exc, JobFailedError) else f"{type(exc).__name__}: {exc}"
            )
            logger.exception("job_failed", extra={"job_id": str(job.id)})
            self._jobs.finish(
                job.id, status=JobStatus.FAILED, activation=Activation.NOT_REQUESTED, error=message
            )
            return Outcome(model_version=None, approved=False, activation=Activation.NOT_REQUESTED)
        self._jobs.finish(job.id, status=JobStatus.SUCCEEDED, activation=outcome.activation)
        return outcome

    def _run(self, job: JobRecord) -> Outcome:
        dataset = self._datasets.get_by_id(job.dataset_id)
        if dataset is None:
            raise JobFailedError("El dataset del trabajo ya no existe")
        path = self._settings.datasets_dir / dataset.file_name
        if not path.is_file():
            raise JobFailedError(f"No se encuentra {dataset.file_name} en el volumen de datasets")
        if sha256_file(path) != dataset.sha256:
            raise JobFailedError(f"{dataset.file_name} cambió desde que se registró (SHA-256)")

        frame = pd.read_parquet(path)
        problems = validate_clean_frame(frame)
        if problems:
            raise JobFailedError("Dataset inválido: " + "; ".join(problems))
        config = TrainingConfig.from_toml(config_path(self._settings, job.config_name))

        version = self._next_version()
        model_id = self._models.start_evaluation(
            version=version,
            dataset_id=dataset.id,
            algorithm=config.algorithm,
            config=config.to_dict(),
            actor=job.requested_by,
        )
        self._jobs.attach_model(job.id, model_id)
        try:
            approved = self._train_and_evaluate(
                job, config, frame, version, model_id, dataset.version, dataset.sha256
            )
        except Exception as exc:
            self._models.reject_after_error(model_id, reason=str(exc), actor=job.requested_by)
            raise

        if not approved:
            return Outcome(version, approved=False, activation=Activation.SKIPPED)
        activated = self._activator.request_activation(version)
        return Outcome(
            version,
            approved=True,
            activation=Activation.SUCCEEDED if activated else Activation.FAILED,
        )

    def _train_and_evaluate(
        self,
        job: JobRecord,
        config: TrainingConfig,
        frame: pd.DataFrame,
        version: str,
        model_id: UUID,
        dataset_version: str,
        dataset_sha256: str,
    ) -> bool:
        splits = split_frame(frame)
        result = train_and_evaluate(
            config, splits, evaluate_test=True, min_slice_support=self._policy.min_slice_support
        )
        golden_frame = splits["val"].sample(
            n=min(GOLDEN_ROWS, len(splits["val"])), random_state=config.seed
        )
        metadata = build_metadata(
            config,
            result,
            splits,
            dataset_version=dataset_version,
            dataset_sha256=dataset_sha256,
            quality_policy_version=self._policy.version,
        )
        metrics = {"validation": result.validation, "test": result.test}
        info = save_artifact(
            result.pipeline,
            self._settings.models_dir,
            version,
            metadata=metadata,
            metrics=metrics,
            golden_frame=golden_frame,
        )
        gate = evaluate_gate(
            result.validation,
            active=self._models.active_validation_metrics(),
            artifact_bytes=info.size_bytes,
            parity_ok=reload_parity(result.pipeline, self._settings.models_dir, info, golden_frame),
            policy=self._policy,
        )
        self._models.close_evaluation(
            model_id,
            approved=gate.passed,
            sha256=info.sha256,
            size_bytes=info.size_bytes,
            metrics=metrics,
            dependencies=dependency_versions(),
            policy_version=self._policy.version,
            gate=gate.to_dict(),
            actor=job.requested_by,
        )
        logger.info(
            "model_evaluated",
            extra={
                "job_id": str(job.id),
                "model_version": version,
                "approved": gate.passed,
                "f1_macro": result.validation["f1_macro"],
                "duration_s": round(result.duration_s, 1),
            },
        )
        return gate.passed

    def _next_version(self) -> str:
        """Siguiente MINOR. Considera también carpetas huérfanas en MODELS_DIR (por ejemplo de
        un entrenamiento interrumpido) para no intentar escribir sobre ellas."""
        existing = set(self._models.versions())
        root: Path = self._settings.models_dir
        if root.is_dir():
            existing |= {
                p.name[1:] for p in root.iterdir() if p.is_dir() and p.name.startswith("v")
            }
        valid = [v for v in existing if _is_semver(v)]
        version = next_minor_version(valid)
        while artifact_dir(root, version).exists():  # defensa extra
            version = next_minor_version([*valid, version])
            valid.append(version)
        return version


def _is_semver(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(p.isdigit() for p in parts)
