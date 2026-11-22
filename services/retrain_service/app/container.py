"""Raíz de composición de la API de reentrenamiento."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from psycopg_pool import ConnectionPool

from svc_common.config import ConfigError
from svc_common.db import DbConnection, create_pool, transaction
from svc_common.jwks import JwksClient
from svc_common.tokens import KeyResolver

from .application.services import DatasetService, JobService
from .infrastructure.pg_repositories import PgDatasetRepository, PgJobRepository
from .settings import RetrainSettings


@dataclass
class Container:
    settings: RetrainSettings
    datasets: DatasetService
    jobs: JobService
    key_resolver: KeyResolver
    health_check: Callable[[], None]
    startup: Callable[[], None]
    shutdown: Callable[[], None]


def build_container(
    settings: RetrainSettings,
    *,
    pool: ConnectionPool[DbConnection] | None = None,
    key_resolver: KeyResolver | None = None,
) -> Container:
    if key_resolver is None:
        if not settings.jwks_url:
            raise ConfigError("Falta JWKS_URL: la API debe verificar el JWT del administrador")
        key_resolver = JwksClient(settings.jwks_url).resolve
    owns_pool = pool is None
    resolved_pool = pool or create_pool(settings.database_url, name="retrain_api")
    datasets = PgDatasetRepository(resolved_pool)

    def health_check() -> None:
        with transaction(resolved_pool) as conn:
            conn.execute("SELECT 1")

    def startup() -> None:
        if owns_pool:
            resolved_pool.open(wait=True, timeout=30)

    def shutdown() -> None:
        if owns_pool:
            resolved_pool.close()

    return Container(
        settings=settings,
        datasets=DatasetService(settings=settings, repo=datasets),
        jobs=JobService(settings=settings, jobs=PgJobRepository(resolved_pool), datasets=datasets),
        key_resolver=key_resolver,
        health_check=health_check,
        startup=startup,
        shutdown=shutdown,
    )
