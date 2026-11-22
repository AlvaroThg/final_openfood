"""Repositorios PostgreSQL de ia_backend (usuario app_ia).

Cada método es una transacción con el contexto de RLS fijado por svc_common.db.transaction:
- predictions: la RLS filtra por app.user_id (o todo si app.role = 'admin');
- model_versions: sin RLS; app_ia solo puede actualizar la columna status, y el trigger de la
  base solo le permite pasar a active/archived. El trigger de auditoría registra cada cambio
  con el usuario (app.user_id) y el request_id.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from nutriscore.lifecycle import ModelStatus
from svc_common.db import DbConnection, transaction
from svc_common.tokens import Role

from ..domain.model import ActivationConflictError, ModelVersionRecord, PredictionRecord

_MODEL_SELECT = """
    SELECT m.version, m.status, m.algorithm, d.version AS dataset_version, m.artifact_sha256,
           m.metrics, m.gate, m.quality_policy_version, m.created_at, m.updated_at
    FROM model_versions m JOIN datasets d ON d.id = m.dataset_id
"""


def _to_model(row: dict[str, Any]) -> ModelVersionRecord:
    return ModelVersionRecord(
        version=row["version"],
        status=ModelStatus(row["status"]),
        algorithm=row["algorithm"],
        dataset_version=row["dataset_version"],
        artifact_sha256=row["artifact_sha256"],
        metrics=row["metrics"],
        gate=row["gate"],
        quality_policy_version=row["quality_policy_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_prediction(row: dict[str, Any]) -> PredictionRecord:
    return PredictionRecord(
        id=row["id"],
        user_id=row["user_id"],
        model_version=row["model_version"],
        input=row["input"],
        grade=row["grade"],
        probabilities=row["probabilities"],
        inference_ms=row["inference_ms"],
        created_at=row["created_at"],
    )


class PgModelRegistry:
    def __init__(self, pool: ConnectionPool[DbConnection]) -> None:
        self._pool = pool

    def get(self, version: str) -> ModelVersionRecord | None:
        with transaction(self._pool) as conn:
            row = conn.execute(_MODEL_SELECT + " WHERE m.version = %s", (version,)).fetchone()
        return _to_model(row) if row else None

    def active(self) -> ModelVersionRecord | None:
        with transaction(self._pool) as conn:
            row = conn.execute(_MODEL_SELECT + " WHERE m.status = 'active'").fetchone()
        return _to_model(row) if row else None

    def list(self) -> list[ModelVersionRecord]:
        with transaction(self._pool) as conn:
            rows = conn.execute(_MODEL_SELECT + " ORDER BY m.created_at DESC").fetchall()
        return [_to_model(row) for row in rows]

    def activate(self, version: str, *, actor_user_id: UUID | None, role: Role) -> str | None:
        with transaction(self._pool, user_id=actor_user_id, role=str(role)) as conn:
            archived = conn.execute(
                "UPDATE model_versions SET status = 'archived'"
                " WHERE status = 'active' AND version <> %s RETURNING version",
                (version,),
            ).fetchone()
            activated = conn.execute(
                "UPDATE model_versions SET status = 'active'"
                " WHERE version = %s AND status IN ('approved', 'archived') RETURNING version",
                (version,),
            ).fetchone()
            if activated is None:
                # Lanzar dentro de la transacción la deshace: el anterior sigue activo.
                raise ActivationConflictError(f"{version} cambió de estado durante la activación")
        return archived["version"] if archived else None


class PgPredictionRepository:
    def __init__(self, pool: ConnectionPool[DbConnection]) -> None:
        self._pool = pool

    def add(
        self,
        *,
        user_id: UUID,
        role: Role,
        model_version: str,
        input: dict[str, Any],
        grade: str,
        probabilities: dict[str, float],
        inference_ms: float,
    ) -> PredictionRecord:
        with transaction(self._pool, user_id=user_id, role=str(role)) as conn:
            row = conn.execute(
                "INSERT INTO predictions (user_id, model_version, input, grade, probabilities,"
                " inference_ms, request_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, app_current_request_id())"
                " RETURNING id, user_id, model_version, input, grade, probabilities, inference_ms,"
                " created_at",
                (user_id, model_version, Jsonb(input), grade, Jsonb(probabilities), inference_ms),
            ).fetchone()
        if row is None:
            raise RuntimeError("INSERT ... RETURNING no devolvió ninguna fila")
        return _to_prediction(row)

    def list_for(
        self, *, user_id: UUID, role: Role, limit: int, offset: int, include_all: bool = False
    ) -> list[PredictionRecord]:
        # Primera capa: el filtro explícito por usuario. Segunda capa: la RLS, que oculta las
        # filas ajenas aunque este filtro faltara. Solo un admin puede pedir todo.
        everyone = include_all and role is Role.ADMIN
        with transaction(self._pool, user_id=user_id, role=str(role)) as conn:
            rows = conn.execute(
                "SELECT id, user_id, model_version, input, grade, probabilities, inference_ms,"
                " created_at FROM predictions WHERE (%s OR user_id = %s)"
                " ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
                (everyone, user_id, limit, offset),
            ).fetchall()
        return [_to_prediction(row) for row in rows]
