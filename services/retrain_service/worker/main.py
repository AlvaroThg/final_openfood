"""Bucle del worker de entrenamiento.

    python -m retrain_service.worker.main

mientras no se pida detenerse:
    marcar como fallidos los trabajos 'running' sin heartbeat reciente (worker caído)
    tomar el siguiente trabajo en cola (SKIP LOCKED); si no hay, esperar POLL_INTERVAL_S
    ejecutarlo con un heartbeat en segundo plano (cada HEARTBEAT_S)

El trabajo no depende de ninguna petición HTTP abierta. Ante SIGTERM termina el trabajo en curso
si alcanza el tiempo; si Docker lo mata antes, el trabajo se marca fallido al reiniciar.
Archivo /tmp/worker.alive: el HEALTHCHECK de Docker verifica que se actualice.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import threading
from pathlib import Path
from types import FrameType

from svc_common.config import ConfigError
from svc_common.db import create_pool
from svc_common.logging import configure_logging, request_id_var

from ..app.domain.model import JobRecord
from ..app.infrastructure.pg_repositories import (
    PgDatasetRepository,
    PgJobRepository,
    PgModelVersionRepository,
)
from ..app.settings import RetrainSettings
from .activation import ActivationClient
from .trainer import Trainer

logger = logging.getLogger(__name__)
ALIVE_FILE = Path("/tmp/worker.alive")  # noqa: S108 (marca de vida, sin datos)


def touch_alive() -> None:
    # Fuera del contenedor (pruebas) puede no haber /tmp escribible: no es un error del trabajo.
    with contextlib.suppress(OSError):
        ALIVE_FILE.touch()


class Heartbeat:
    """Actualiza heartbeat_at del trabajo mientras dura el entrenamiento."""

    def __init__(self, jobs: PgJobRepository, job: JobRecord, interval_s: float) -> None:
        self._jobs = jobs
        self._job = job
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="heartbeat", daemon=True)

    def __enter__(self) -> Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _beat(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                self._jobs.heartbeat(self._job.id)
                touch_alive()
            except Exception:
                logger.exception("heartbeat_failed", extra={"job_id": str(self._job.id)})


def run_once(trainer: Trainer, jobs: PgJobRepository, settings: RetrainSettings) -> bool:
    """Una vuelta del bucle. Devuelve True si ejecutó un trabajo."""
    touch_alive()
    stale = jobs.fail_stale(settings.stale_after_s)
    if stale:
        logger.warning("stale_jobs_failed", extra={"count": stale})
    job = jobs.claim_next()
    if job is None:
        return False
    token = request_id_var.set(f"job-{job.id.hex[:16]}")
    try:
        logger.info("job_started", extra={"job_id": str(job.id), "config_name": job.config_name})
        with Heartbeat(jobs, job, settings.heartbeat_s):
            outcome = trainer.run(job)
        logger.info(
            "job_finished",
            extra={
                "job_id": str(job.id),
                "model_version": outcome.model_version,
                "approved": outcome.approved,
                "activation": str(outcome.activation),
            },
        )
    finally:
        request_id_var.reset(token)
    return True


def main() -> None:
    settings = RetrainSettings.from_env()
    configure_logging("retrain_worker", settings.log_level)
    if not settings.service_client_secret:
        raise ConfigError(
            "Falta SERVICE_CLIENT_SECRET: el worker lo necesita para pedir la activación"
        )

    pool = create_pool(settings.database_url, name="retrain_worker", max_size=3)
    pool.open(wait=True, timeout=30)
    jobs = PgJobRepository(pool)
    trainer = Trainer(
        settings=settings,
        jobs=jobs,
        datasets=PgDatasetRepository(pool),
        models=PgModelVersionRepository(pool),
        activator=ActivationClient(
            auth_url=settings.auth_url,
            ia_url=settings.ia_url,
            client_id=settings.service_client_id,
            client_secret=settings.service_client_secret,
        ),
    )

    stop = threading.Event()

    def request_stop(signum: int, _: FrameType | None) -> None:
        logger.info("worker_stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    logger.info("worker_started")
    try:
        while not stop.is_set():
            if not run_once(trainer, jobs, settings):
                stop.wait(settings.poll_interval_s)
    finally:
        pool.close()


if __name__ == "__main__":
    main()
