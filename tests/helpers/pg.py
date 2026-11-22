"""Utilidades para las pruebas de integración con PostgreSQL real.

Servidor: docker-compose.test.yml (mismo script de roles que producción).
Cada sesión de pytest crea una base nueva, le aplica las migraciones con app_migrator y la
borra al final. Las pruebas se conectan con el usuario de cada servicio, como en producción.

Variables (con valores por defecto que coinciden con docker-compose.test.yml):
    TEST_PG_ADMIN_URL           superusuario, solo para crear y borrar bases
    TEST_PG_<ROL>_PASSWORD      contraseña de app_migrator, app_auth, app_ia, app_retrain
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
ADMIN_URL = os.environ.get(
    "TEST_PG_ADMIN_URL", "postgresql://postgres:test-superuser@127.0.0.1:55432/postgres"
)
ROLES = ("app_migrator", "app_auth", "app_ia", "app_retrain")
PASSWORDS = {
    role: os.environ.get(
        f"TEST_PG_{role.removeprefix('app_').upper()}_PASSWORD", f"test-{role[4:]}"
    )
    for role in ROLES
}
# Orden compatible con las claves foráneas (TRUNCATE ... CASCADE igual lo resolvería).
APP_TABLES = (
    "predictions",
    "retrain_jobs",
    "model_version_events",
    "model_versions",
    "datasets",
    "refresh_tokens",
    "users",
)


def url_for(role: str, dbname: str) -> str:
    parts = urlsplit(ADMIN_URL)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 5432
    return f"postgresql://{role}:{quote(PASSWORDS[role])}@{host}:{port}/{dbname}"


def server_unavailable_reason() -> str | None:
    try:
        with psycopg.connect(ADMIN_URL, connect_timeout=3):
            return None
    except psycopg.OperationalError as exc:
        return str(exc).splitlines()[0]


def create_database(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER app_migrator").format(sql.Identifier(name)))


def drop_database(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def alembic_config(dbname: str) -> Config:
    config = Config(str(ROOT / "db" / "alembic.ini"))
    config.attributes["database_url"] = url_for("app_migrator", dbname)
    return config


def migrate(dbname: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(dbname), revision)


def downgrade(dbname: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(dbname), revision)


def connect(role: str, dbname: str) -> psycopg.Connection[dict[str, Any]]:
    """Conexión en autocommit con filas como diccionarios."""
    return psycopg.connect(url_for(role, dbname), autocommit=True, row_factory=dict_row)


def truncate_all(dbname: str) -> None:
    """Deja la base vacía entre pruebas. Lo hace el dueño (TRUNCATE no pasa por la RLS)."""
    with connect("app_migrator", dbname) as conn:
        conn.execute("TRUNCATE " + ", ".join(APP_TABLES) + " CASCADE")


# ----------------------------------------------------------------- datos de prueba
def insert_user(dbname: str, email: str, role: str = "user") -> UUID:
    with connect("app_auth", dbname) as conn:
        row = conn.execute(
            "INSERT INTO users (email, password_hash, role) VALUES (%s, 'x', %s) RETURNING id",
            (email, role),
        ).fetchone()
    assert row is not None
    user_id: UUID = row["id"]
    return user_id


def insert_dataset(dbname: str, version: str = "v1") -> UUID:
    with connect("app_retrain", dbname) as conn:
        row = conn.execute(
            "INSERT INTO datasets (version, sha256, row_count, file_name, cleaning_report,"
            " class_distribution) VALUES (%s, %s, 100, 'clean_v1.parquet', '{}', '{}')"
            " RETURNING id",
            (version, "a" * 64),
        ).fetchone()
    assert row is not None
    dataset_id: UUID = row["id"]
    return dataset_id


def insert_model_version(dbname: str, version: str, dataset_id: UUID) -> UUID:
    with connect("app_retrain", dbname) as conn:
        row = conn.execute(
            "INSERT INTO model_versions (version, dataset_id, algorithm, config)"
            " VALUES (%s, %s, 'lightgbm', '{}') RETURNING id",
            (version, dataset_id),
        ).fetchone()
    assert row is not None
    model_id: UUID = row["id"]
    return model_id


EVALUATION_FIELDS = (
    "artifact_sha256 = %(sha)s, artifact_size_bytes = 1024, metrics = '{}',"
    " dependencies = '{}', quality_policy_version = 'qp-1', gate = '{}'"
)
