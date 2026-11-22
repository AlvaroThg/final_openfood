"""Entorno de Alembic: aplica las migraciones con el usuario app_migrator.

URL de conexión, en este orden:
1. config.attributes["database_url"] (lo usan las pruebas);
2. variable de entorno MIGRATIONS_DATABASE_URL (contenedor `migrate`).
Si no hay ninguna, falla de inmediato.

Cada migración corre en su propia transacción (transaction_per_migration): si una falla, las
anteriores quedan aplicadas y la que falló no deja cambios a medias (en PostgreSQL el DDL es
transaccional).
"""

from __future__ import annotations

import logging
import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


def database_url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("MIGRATIONS_DATABASE_URL", "")
    if not url:
        raise RuntimeError("Falta MIGRATIONS_DATABASE_URL (conexión con el usuario app_migrator)")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("El modo offline no está soportado: las migraciones se aplican en línea.")
run_migrations_online()
