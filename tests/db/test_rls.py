"""RLS: aislamiento entre usuarios en predictions y visibilidad de retrain_jobs.

Se usa svc_common.db.transaction(), el mismo código que fijará el contexto en los servicios.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from svc_common.db import DbConnection, create_pool, transaction
from tests.helpers import pg

pytestmark = pytest.mark.integration


@pytest.fixture
def ia_pool(db: str) -> Iterator[ConnectionPool[DbConnection]]:
    # max_size=1: todas las transacciones reutilizan LA MISMA conexión, como en un pool real.
    pool = create_pool(pg.url_for("app_ia", db), name="test_ia", min_size=1, max_size=1)
    pool.open(wait=True)
    yield pool
    pool.close()


@pytest.fixture
def retrain_pool(db: str) -> Iterator[ConnectionPool[DbConnection]]:
    pool = create_pool(pg.url_for("app_retrain", db), name="test_retrain", max_size=1)
    pool.open(wait=True)
    yield pool
    pool.close()


@pytest.fixture
def people(db: str) -> dict[str, UUID]:
    pg.insert_model_version(db, "1.0.0", pg.insert_dataset(db))
    return {
        "ana": pg.insert_user(db, "ana@ejemplo.com"),
        "beto": pg.insert_user(db, "beto@ejemplo.com"),
        "admin": pg.insert_user(db, "admin@ejemplo.com", role="admin"),
    }


def _insert_prediction(conn: DbConnection, user_id: UUID, grade: str = "B") -> None:
    conn.execute(
        "INSERT INTO predictions (user_id, model_version, input, grade, probabilities,"
        " inference_ms) VALUES (%s, '1.0.0', %s, %s, %s, 1.5)",
        (user_id, json.dumps({"sugars": 1}), grade, json.dumps({grade: 0.9})),
    )


def _visible_owners(pool: ConnectionPool[DbConnection], **context: object) -> list[UUID]:
    with transaction(pool, **context) as conn:  # type: ignore[arg-type]
        rows = conn.execute("SELECT user_id FROM predictions").fetchall()
    return [row["user_id"] for row in rows]


def test_each_user_sees_only_their_predictions(ia_pool, people) -> None:
    for name in ("ana", "beto"):
        with transaction(ia_pool, user_id=people[name], role="user") as conn:
            _insert_prediction(conn, people[name])

    assert _visible_owners(ia_pool, user_id=people["ana"], role="user") == [people["ana"]]
    assert _visible_owners(ia_pool, user_id=people["beto"], role="user") == [people["beto"]]


def test_manipulated_id_in_where_clause_returns_nothing(ia_pool, people) -> None:
    with transaction(ia_pool, user_id=people["beto"], role="user") as conn:
        _insert_prediction(conn, people["beto"])
    with transaction(ia_pool, user_id=people["ana"], role="user") as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE user_id = %s", (people["beto"],)
        ).fetchall()
    assert rows == []


def test_admin_sees_all_predictions(ia_pool, people) -> None:
    for name in ("ana", "beto"):
        with transaction(ia_pool, user_id=people[name], role="user") as conn:
            _insert_prediction(conn, people[name])
    owners = _visible_owners(ia_pool, user_id=people["admin"], role="admin")
    assert sorted(owners) == sorted([people["ana"], people["beto"]])


def test_cannot_insert_prediction_for_another_user(ia_pool, people) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        transaction(ia_pool, user_id=people["ana"], role="user") as conn,
    ):
        _insert_prediction(conn, people["beto"])


def test_service_role_cannot_insert_predictions(ia_pool, people) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        transaction(ia_pool, user_id=people["ana"], role="service") as conn,
    ):
        _insert_prediction(conn, people["ana"])


def test_without_context_nothing_is_visible_or_writable(ia_pool, people) -> None:
    with transaction(ia_pool, user_id=people["ana"], role="user") as conn:
        _insert_prediction(conn, people["ana"])
    assert _visible_owners(ia_pool) == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege), transaction(ia_pool) as conn:
        _insert_prediction(conn, people["ana"])


def test_context_does_not_leak_to_next_transaction(ia_pool, people) -> None:
    """La conexión vuelve al pool sin el usuario anterior (set_config(..., true))."""
    with transaction(ia_pool, user_id=people["ana"], role="admin") as conn:
        _insert_prediction(conn, people["ana"])
    # Misma conexión física (max_size=1), sin usar transaction(): no hay contexto.
    with ia_pool.connection() as conn:
        row = conn.execute(
            "SELECT app_current_user_id() AS uid, app_current_role() AS role,"
            " (SELECT count(*) FROM predictions) AS visible"
        ).fetchone()
    assert row == {"uid": None, "role": None, "visible": 0}


def test_invalid_user_id_in_context_fails_loudly(ia_pool, people) -> None:
    with (
        pytest.raises(psycopg.errors.InvalidTextRepresentation),
        transaction(ia_pool, user_id="no-es-un-uuid", role="user") as conn,
    ):
        conn.execute("SELECT * FROM predictions")


# ------------------------------------------------------------------------- retrain_jobs
def _insert_job(conn: DbConnection, dataset_id: UUID, admin_id: UUID) -> None:
    conn.execute(
        "INSERT INTO retrain_jobs (dataset_id, config_name, requested_by)"
        " VALUES (%s, 'lightgbm', %s)",
        (dataset_id, admin_id),
    )


def test_retrain_jobs_visible_only_to_admin_and_worker(db, retrain_pool) -> None:
    dataset_id = pg.insert_dataset(db)
    admin_id = pg.insert_user(db, "admin@ejemplo.com", role="admin")
    with transaction(retrain_pool, user_id=admin_id, role="admin") as conn:
        _insert_job(conn, dataset_id, admin_id)

    def count(role: str) -> int:
        with transaction(retrain_pool, user_id=admin_id, role=role) as conn:
            row = conn.execute("SELECT count(*) AS n FROM retrain_jobs").fetchone()
        assert row is not None
        return int(row["n"])

    assert count("admin") == 1
    assert count("service") == 1
    assert count("user") == 0

    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        transaction(retrain_pool, user_id=admin_id, role="user") as conn,
    ):
        _insert_job(conn, dataset_id, admin_id)
