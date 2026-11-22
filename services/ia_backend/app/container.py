"""Raíz de composición de ia_backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from svc_common.db import create_pool, transaction
from svc_common.jwks import JwksClient
from svc_common.rate_limit import SlidingWindowRateLimiter
from svc_common.tokens import KeyResolver

from .application.model_manager import ModelManager
from .application.ports import ArtifactStore, ModelRegistry, PredictionRepository
from .application.predict import PredictService
from .infrastructure.artifact_store import FsArtifactStore
from .infrastructure.pg_repositories import PgModelRegistry, PgPredictionRepository
from .settings import IaSettings


def _noop() -> None:
    return None


@dataclass
class Container:
    settings: IaSettings
    models: ModelManager
    predictions: PredictService
    registry: ModelRegistry
    key_resolver: KeyResolver
    health_check: Callable[[], None]
    predict_limiter: SlidingWindowRateLimiter
    startup: Callable[[], None] = _noop
    shutdown: Callable[[], None] = _noop

    @classmethod
    def create(
        cls,
        settings: IaSettings,
        *,
        registry: ModelRegistry,
        store: ArtifactStore,
        prediction_repo: PredictionRepository,
        key_resolver: KeyResolver,
        health_check: Callable[[], None],
        startup: Callable[[], None] | None = None,
        shutdown: Callable[[], None] = _noop,
    ) -> Container:
        models = ModelManager(registry=registry, store=store)

        def default_startup() -> None:
            models.load_active()

        return cls(
            settings=settings,
            models=models,
            predictions=PredictService(models=models, predictions=prediction_repo),
            registry=registry,
            key_resolver=key_resolver,
            health_check=health_check,
            predict_limiter=SlidingWindowRateLimiter(settings.predict_per_user_per_minute, 60),
            startup=startup or default_startup,
            shutdown=shutdown,
        )


def build_container(settings: IaSettings) -> Container:
    pool = create_pool(settings.database_url, name="ia_backend")
    jwks = JwksClient(settings.jwks_url)

    def health_check() -> None:
        with transaction(pool) as conn:
            conn.execute("SELECT 1")

    container = Container.create(
        settings,
        registry=PgModelRegistry(pool),
        store=FsArtifactStore(settings.models_dir),
        prediction_repo=PgPredictionRepository(pool),
        key_resolver=jwks.resolve,
        health_check=health_check,
        shutdown=pool.close,
    )

    def startup() -> None:
        pool.open(wait=True, timeout=30)
        container.models.load_active()

    container.startup = startup
    return container
