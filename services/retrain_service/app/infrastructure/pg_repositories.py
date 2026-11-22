"""Repositorios PostgreSQL de retrain_service (usuario app_retrain).

Contexto de RLS:
- la API actúa como el admin que hizo la petición (role = 'admin', user_id = su id);
- el worker actúa como 'service', con user_id = el admin que pidió el trabajo, para que la
  auditoría de model_version_events registre quién lo originó.
retrain_jobs tiene RLS: fuera de esos dos contextos no es visible.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from svc_common.db import DbConnection, transaction

from ..domain.model import (
    Activation,
    DatasetAlreadyRegisteredError,
    DatasetRecord,
    JobRecord,
    JobStatus,
)

Context = dict[str, Any]  # {"user_id": UUID | None, "role": "admin" | "service"}

_DATASET_COLUMNS = "id, version, sha256, row_count, file_name, class_distribution, created_at"
_JOB_SELECT = """
    SELECT j.id, j.dataset_id, d.version AS dataset_version, j.config_name, j.requested_by,
           j.status, m.version AS model_version, j.activation, j.error, j.attempts,
           j.created_at, j.started_at, j.finished_at
    FROM retrain_jobs j
    JOIN datasets d ON d.id = j.dataset_id
    LEFT JOIN model_versions m ON m.id = j.model_version_id
"""


def _one(row: dict[str, Any] | None) -> dict[str, Any]:
    """Para INSERT/UPDATE ... RETURNING que siempre deben devolver una fila."""
    if row is None:
        raise RuntimeError("La consulta no devolvió la fila esperada")
    return row


def _dataset(row: dict[str, Any]) -> DatasetRecord:
    return DatasetRecord(
        id=row["id"],
        version=row["version"],
        sha256=row["sha256"],
        row_count=row["row_count"],
        file_name=row["file_name"],
        class_distribution=row["class_distribution"],
        created_at=row["created_at"],
    )


def _job(row: dict[str, Any]) -> JobRecord:
    return JobRecord(
        id=row["id"],
        dataset_id=row["dataset_id"],
        dataset_version=row["dataset_version"],
        config_name=row["config_name"],
        requested_by=row["requested_by"],
        status=JobStatus(row["status"]),
        model_version=row["model_version"],
        activation=Activation(row["activation"]),
        error=row["error"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


class PgDatasetRepository:
    def __init__(self, pool: ConnectionPool[DbConnection]) -> None:
        self._pool = pool

    def list(self) -> list[DatasetRecord]:
        with transaction(self._pool) as conn:
            rows = conn.execute(
                f"SELECT {_DATASET_COLUMNS} FROM datasets ORDER BY created_at DESC"  # noqa: S608
            ).fetchall()
        return [_dataset(r) for r in rows]

    def get(self, version: str) -> DatasetRecord | None:
        with transaction(self._pool) as conn:
            row = conn.execute(
                f"SELECT {_DATASET_COLUMNS} FROM datasets WHERE version = %s",  # noqa: S608
                (version,),
            ).fetchone()
        return _dataset(row) if row else None

    def get_by_id(self, dataset_id: UUID) -> DatasetRecord | None:
        with transaction(self._pool) as conn:
            row = conn.execute(
                f"SELECT {_DATASET_COLUMNS} FROM datasets WHERE id = %s",  # noqa: S608
                (dataset_id,),
            ).fetchone()
        return _dataset(row) if row else None

    def add(
        self,
        *,
        sha256: str,
        row_count: int,
        file_name: str,
        cleaning_report: dict[str, Any],
        class_distribution: dict[str, Any],
        created_by: UUID | None,
    ) -> DatasetRecord:
        """Asigna la siguiente versión (v1, v2, ...) y registra el dataset. Inmutable."""
        with transaction(self._pool, user_id=created_by, role="admin") as conn:
            # Serializa los registros concurrentes para que dos no tomen el mismo vN.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('datasets_version'))")
            if conn.execute("SELECT 1 FROM datasets WHERE sha256 = %s", (sha256,)).fetchone():
                raise DatasetAlreadyRegisteredError(sha256)
            row = conn.execute(
                "SELECT coalesce(max(substring(version FROM 2)::int), 0) + 1 AS n FROM datasets"
            ).fetchone()
            version = f"v{row['n'] if row else 1}"
            created = conn.execute(
                "INSERT INTO datasets (version, sha256, row_count, file_name, cleaning_report,"  # noqa: S608
                " class_distribution, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {_DATASET_COLUMNS}",  # columnas fijas, sin entrada del usuario
                (
                    version,
                    sha256,
                    row_count,
                    file_name,
                    Jsonb(cleaning_report),
                    Jsonb(class_distribution),
                    created_by,
                ),
            ).fetchone()
        return _dataset(_one(created))


class PgJobRepository:
    def __init__(self, pool: ConnectionPool[DbConnection]) -> None:
        self._pool = pool

    def add(self, *, dataset_id: UUID, config_name: str, requested_by: UUID) -> JobRecord:
        with transaction(self._pool, user_id=requested_by, role="admin") as conn:
            row = conn.execute(
                "INSERT INTO retrain_jobs (dataset_id, config_name, requested_by)"
                " VALUES (%s, %s, %s) RETURNING id",
                (dataset_id, config_name, requested_by),
            ).fetchone()
            job = conn.execute(_JOB_SELECT + " WHERE j.id = %s", (_one(row)["id"],)).fetchone()
        return _job(_one(job))

    def get(self, job_id: UUID, *, ctx: Context) -> JobRecord | None:
        with transaction(self._pool, **ctx) as conn:
            row = conn.execute(_JOB_SELECT + " WHERE j.id = %s", (job_id,)).fetchone()
        return _job(row) if row else None

    def list(self, *, ctx: Context, limit: int = 50) -> list[JobRecord]:
        with transaction(self._pool, **ctx) as conn:
            rows = conn.execute(
                _JOB_SELECT + " ORDER BY j.created_at DESC LIMIT %s", (limit,)
            ).fetchall()
        return [_job(r) for r in rows]

    # ------------------------------------------------------------------ worker
    def claim_next(self) -> JobRecord | None:
        """Toma el trabajo en cola más antiguo. SKIP LOCKED: dos workers nunca toman el mismo.
        El índice único parcial impide además dos trabajos 'running' a la vez."""
        with transaction(self._pool, role="service") as conn:
            row = conn.execute(
                "UPDATE retrain_jobs SET status = 'running', started_at = now(),"
                " heartbeat_at = now(), attempts = attempts + 1"
                " WHERE id = (SELECT id FROM retrain_jobs WHERE status = 'queued'"
                "             ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)"
                " RETURNING id"
            ).fetchone()
            if row is None:
                return None
            job = conn.execute(_JOB_SELECT + " WHERE j.id = %s", (row["id"],)).fetchone()
        return _job(job) if job else None

    def heartbeat(self, job_id: UUID) -> None:
        with transaction(self._pool, role="service") as conn:
            conn.execute(
                "UPDATE retrain_jobs SET heartbeat_at = now() WHERE id = %s AND status = 'running'",
                (job_id,),
            )

    def attach_model(self, job_id: UUID, model_id: UUID) -> None:
        with transaction(self._pool, role="service") as conn:
            conn.execute(
                "UPDATE retrain_jobs SET model_version_id = %s WHERE id = %s", (model_id, job_id)
            )

    def finish(
        self, job_id: UUID, *, status: JobStatus, activation: Activation, error: str | None = None
    ) -> None:
        with transaction(self._pool, role="service") as conn:
            conn.execute(
                "UPDATE retrain_jobs SET status = %s, activation = %s, error = %s,"
                " finished_at = now() WHERE id = %s AND status = 'running'",
                (str(status), str(activation), error[:2000] if error else None, job_id),
            )

    def fail_stale(self, older_than_s: int) -> int:
        """Trabajos 'running' sin heartbeat reciente: el worker murió. Se marcan como fallidos
        (no se reintentan solos: un admin decide si volver a encolarlos)."""
        with transaction(self._pool, role="service") as conn:
            cursor = conn.execute(
                "UPDATE retrain_jobs SET status = 'failed', finished_at = now(),"
                " error = 'El worker se interrumpió durante el entrenamiento'"
                " WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %s)",
                (older_than_s,),
            )
            return cursor.rowcount


class PgModelVersionRepository:
    """Lado retrain de model_versions: crear, evaluar y cerrar la evaluación.
    Nunca activa: el trigger de la base se lo impide a app_retrain."""

    def __init__(self, pool: ConnectionPool[DbConnection]) -> None:
        self._pool = pool

    def versions(self) -> list[str]:
        with transaction(self._pool) as conn:
            rows = conn.execute("SELECT version FROM model_versions").fetchall()
        return [r["version"] for r in rows]

    def active_validation_metrics(self) -> dict[str, Any] | None:
        with transaction(self._pool) as conn:
            row = conn.execute(
                "SELECT metrics FROM model_versions WHERE status = 'active'"
            ).fetchone()
        if row is None or not row["metrics"]:
            return None
        validation: dict[str, Any] | None = row["metrics"].get("validation")
        return validation

    def start_evaluation(
        self,
        *,
        version: str,
        dataset_id: UUID,
        algorithm: str,
        config: dict[str, Any],
        actor: UUID | None,
    ) -> UUID:
        """candidate → evaluating en una sola transacción."""
        with transaction(self._pool, user_id=actor, role="service") as conn:
            row = conn.execute(
                "INSERT INTO model_versions (version, dataset_id, algorithm, config)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (version, dataset_id, algorithm, Jsonb(config)),
            ).fetchone()
            model_id: UUID = _one(row)["id"]
            conn.execute(
                "UPDATE model_versions SET status = 'evaluating' WHERE id = %s", (model_id,)
            )
        return model_id

    def close_evaluation(
        self,
        model_id: UUID,
        *,
        approved: bool,
        sha256: str,
        size_bytes: int,
        metrics: dict[str, Any],
        dependencies: dict[str, Any],
        policy_version: str,
        gate: dict[str, Any],
        actor: UUID | None,
    ) -> None:
        with transaction(self._pool, user_id=actor, role="service") as conn:
            conn.execute(
                "UPDATE model_versions SET status = %s, artifact_sha256 = %s,"
                " artifact_size_bytes = %s, metrics = %s, dependencies = %s,"
                " quality_policy_version = %s, gate = %s"
                " WHERE id = %s AND status = 'evaluating'",
                (
                    "approved" if approved else "rejected",
                    sha256,
                    size_bytes,
                    Jsonb(metrics),
                    Jsonb(dependencies),
                    policy_version,
                    Jsonb(gate),
                    model_id,
                ),
            )

    def reject_after_error(self, model_id: UUID, *, reason: str, actor: UUID | None) -> None:
        with transaction(self._pool, user_id=actor, role="service") as conn:
            conn.execute(
                "UPDATE model_versions SET status = 'rejected', gate = %s"
                " WHERE id = %s AND status = 'evaluating'",
                (Jsonb({"passed": False, "error": reason[:500]}), model_id),
            )
