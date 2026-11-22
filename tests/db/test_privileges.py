"""Mínimo privilegio: cada servicio solo puede tocar lo suyo, y ninguno tiene superpoderes."""

from __future__ import annotations

import psycopg
import pytest

from tests.helpers import pg

pytestmark = pytest.mark.integration

SERVICE_ROLES = ("app_auth", "app_ia", "app_retrain")


def test_app_roles_have_no_special_attributes(db: str) -> None:
    with pg.connect("app_migrator", db) as conn:
        rows = conn.execute(
            "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication"
            " FROM pg_roles WHERE rolname = ANY(%s)",
            (list(pg.ROLES),),
        ).fetchall()
    assert {row["rolname"] for row in rows} == set(pg.ROLES)
    for row in rows:
        assert not any(
            row[attr]
            for attr in (
                "rolsuper",
                "rolbypassrls",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
            )
        ), row


def test_services_do_not_own_any_table(db: str) -> None:
    with pg.connect("app_migrator", db) as conn:
        owners = conn.execute(
            "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    assert {row["tableowner"] for row in owners} == {"app_migrator"}


def test_rls_is_enabled_and_forced(db: str) -> None:
    with pg.connect("app_migrator", db) as conn:
        rows = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE relname IN ('predictions', 'retrain_jobs')"
        ).fetchall()
    assert len(rows) == 2
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows)


DENIED = [
    ("app_ia", "SELECT * FROM users"),
    ("app_ia", "SELECT * FROM refresh_tokens"),
    ("app_ia", "UPDATE predictions SET grade = 'A'"),
    ("app_ia", "DELETE FROM predictions"),
    (
        "app_ia",
        "INSERT INTO model_version_events (model_version_id, to_status, db_role)"
        " VALUES (gen_random_uuid(), 'active', 'x')",
    ),
    ("app_ia", "UPDATE model_versions SET metrics = '{}'"),
    (
        "app_ia",
        "INSERT INTO datasets (version, sha256, row_count, file_name, cleaning_report,"
        " class_distribution) VALUES ('v9', repeat('a', 64), 1, 'f', '{}', '{}')",
    ),
    ("app_auth", "SELECT * FROM predictions"),
    ("app_auth", "SELECT * FROM model_versions"),
    ("app_auth", "DELETE FROM users"),
    ("app_retrain", "SELECT * FROM users"),
    ("app_retrain", "SELECT * FROM predictions"),
    ("app_retrain", "DELETE FROM retrain_jobs"),
    ("app_retrain", "UPDATE model_versions SET version = '9.9.9'"),
    ("app_retrain", "UPDATE datasets SET row_count = 1"),
    (
        "app_retrain",
        "INSERT INTO model_version_events (model_version_id, to_status, db_role)"
        " VALUES (gen_random_uuid(), 'active', 'x')",
    ),
] + [(role, "CREATE TABLE intruso (id int)") for role in SERVICE_ROLES]


@pytest.mark.parametrize(("role", "statement"), DENIED)
def test_statement_is_denied(db: str, role: str, statement: str) -> None:
    with pg.connect(role, db) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


ALLOWED = [
    ("app_auth", "SELECT count(*) FROM users"),
    ("app_auth", "SELECT count(*) FROM refresh_tokens"),
    ("app_ia", "SELECT count(*) FROM model_versions"),
    ("app_ia", "SELECT count(*) FROM predictions"),
    ("app_retrain", "SELECT count(*) FROM datasets"),
    ("app_retrain", "SELECT count(*) FROM retrain_jobs"),
]


@pytest.mark.parametrize(("role", "statement"), ALLOWED)
def test_statement_is_allowed(db: str, role: str, statement: str) -> None:
    with pg.connect(role, db) as conn:
        conn.execute(statement)
