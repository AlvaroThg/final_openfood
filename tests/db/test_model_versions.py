"""Integridad del ciclo de vida del modelo garantizada por la base de datos.

- Las transiciones del trigger coinciden EXACTAMENTE con nutriscore.lifecycle.
- Solo app_ia activa/archiva y solo app_retrain evalúa/aprueba/rechaza.
- Un único modelo activo; la evaluación es inmutable una vez cerrada.
- Cada cambio de estado queda auditado con rol, usuario y request_id.
"""

from __future__ import annotations

import itertools
from uuid import UUID

import psycopg
import pytest

from nutriscore.lifecycle import ModelStatus, can_transition
from tests.helpers import pg

pytestmark = pytest.mark.integration


def _set_context(conn: psycopg.Connection, user_id: UUID | None, request_id: str) -> None:
    conn.execute(
        "SELECT set_config('app.user_id', %s, true), set_config('app.request_id', %s, true)",
        (str(user_id) if user_id else "", request_id),
    )


def _update_status(
    db: str,
    role: str,
    model_id: UUID,
    status: str,
    *,
    evaluation: bool = False,
    user_id: UUID | None = None,
    request_id: str = "req-test",
) -> None:
    extra = ", " + pg.EVALUATION_FIELDS if evaluation else ""
    with pg.connect(role, db) as conn, conn.transaction():
        _set_context(conn, user_id, request_id)
        conn.execute(
            f"UPDATE model_versions SET status = %(status)s{extra} WHERE id = %(id)s",  # noqa: S608
            {"status": status, "id": model_id, "sha": "b" * 64},
        )


def _approved_model(db: str, version: str, dataset_id: UUID) -> UUID:
    model_id = pg.insert_model_version(db, version, dataset_id)
    _update_status(db, "app_retrain", model_id, "evaluating")
    _update_status(db, "app_retrain", model_id, "approved", evaluation=True)
    return model_id


def test_sql_transitions_match_python_lifecycle(migrated_db: str) -> None:
    statuses = list(ModelStatus)
    with pg.connect("app_migrator", migrated_db) as conn:
        for current, target in itertools.product(statuses, statuses):
            row = conn.execute(
                "SELECT model_status_transition_allowed(%s, %s) AS ok", (current, target)
            ).fetchone()
            assert row is not None
            assert row["ok"] == can_transition(current, target), (current, target)


def test_new_version_must_start_as_candidate(db: str) -> None:
    dataset_id = pg.insert_dataset(db)
    with pg.connect("app_retrain", db) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO model_versions (version, dataset_id, algorithm, config, status)"
            " VALUES ('1.0.0', %s, 'lightgbm', '{}', 'approved')",
            (dataset_id,),
        )


def test_invalid_transition_is_rejected(db: str) -> None:
    model_id = pg.insert_model_version(db, "1.0.0", pg.insert_dataset(db))
    with pytest.raises(psycopg.errors.CheckViolation, match="Transición"):
        _update_status(db, "app_retrain", model_id, "approved", evaluation=True)


def test_approval_requires_complete_evaluation(db: str) -> None:
    model_id = pg.insert_model_version(db, "1.0.0", pg.insert_dataset(db))
    _update_status(db, "app_retrain", model_id, "evaluating")
    with pytest.raises(psycopg.errors.CheckViolation, match="model_versions_evaluated"):
        _update_status(db, "app_retrain", model_id, "approved")


def test_retrain_cannot_activate(db: str) -> None:
    model_id = _approved_model(db, "1.0.0", pg.insert_dataset(db))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="app_ia"):
        _update_status(db, "app_retrain", model_id, "active")


def test_ia_cannot_approve(db: str) -> None:
    model_id = pg.insert_model_version(db, "1.0.0", pg.insert_dataset(db))
    _update_status(db, "app_retrain", model_id, "evaluating")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _update_status(db, "app_ia", model_id, "rejected")


def test_only_one_active_model(db: str) -> None:
    dataset_id = pg.insert_dataset(db)
    first = _approved_model(db, "1.0.0", dataset_id)
    second = _approved_model(db, "1.1.0", dataset_id)
    _update_status(db, "app_ia", first, "active")

    with pytest.raises(psycopg.errors.UniqueViolation):
        _update_status(db, "app_ia", second, "active")

    # Activación correcta: archivar el anterior y activar el nuevo en UNA transacción.
    with pg.connect("app_ia", db) as conn, conn.transaction():
        conn.execute("UPDATE model_versions SET status = 'archived' WHERE id = %s", (first,))
        conn.execute("UPDATE model_versions SET status = 'active' WHERE id = %s", (second,))
    # Rollback: el archivado puede volver a activarse.
    with pg.connect("app_ia", db) as conn, conn.transaction():
        conn.execute("UPDATE model_versions SET status = 'archived' WHERE id = %s", (second,))
        conn.execute("UPDATE model_versions SET status = 'active' WHERE id = %s", (first,))


def test_evaluation_is_immutable_after_approval(db: str) -> None:
    model_id = _approved_model(db, "1.0.0", pg.insert_dataset(db))
    with (
        pg.connect("app_retrain", db) as conn,
        pytest.raises(psycopg.errors.CheckViolation, match="inmutable"),
    ):
        conn.execute(
            "UPDATE model_versions SET artifact_sha256 = %s WHERE id = %s", ("c" * 64, model_id)
        )


def test_every_status_change_is_audited(db: str) -> None:
    admin_id = pg.insert_user(db, "admin@ejemplo.com", role="admin")
    model_id = _approved_model(db, "1.0.0", pg.insert_dataset(db))
    _update_status(db, "app_ia", model_id, "active", user_id=admin_id, request_id="req-activar")

    with pg.connect("app_migrator", db) as conn:
        events = conn.execute(
            "SELECT from_status, to_status, db_role, actor_user_id, request_id"
            " FROM model_version_events WHERE model_version_id = %s ORDER BY created_at, to_status",
            (model_id,),
        ).fetchall()

    transitions = [(e["from_status"], e["to_status"], e["db_role"]) for e in events]
    assert transitions == [
        (None, "candidate", "app_retrain"),
        ("candidate", "evaluating", "app_retrain"),
        ("evaluating", "approved", "app_retrain"),
        ("approved", "active", "app_ia"),
    ]
    assert events[-1]["actor_user_id"] == admin_id
    assert events[-1]["request_id"] == "req-activar"
