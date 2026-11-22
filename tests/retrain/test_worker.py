"""Worker de entrenamiento contra PostgreSQL real: cola, entrenamiento, política de calidad,
registro de la versión, solicitud de activación y recuperación de trabajos huérfanos."""

from __future__ import annotations

import pytest

from nutriscore.artifacts import artifact_dir
from nutriscore.quality import QualityPolicy
from services.retrain_service.app.application.services import DatasetService, JobService
from services.retrain_service.app.domain.model import Activation, JobStatus
from services.retrain_service.app.infrastructure.pg_repositories import (
    PgDatasetRepository,
    PgJobRepository,
    PgModelVersionRepository,
)
from services.retrain_service.worker.main import run_once
from services.retrain_service.worker.trainer import Trainer
from tests.helpers import pg
from tests.retrain.conftest import IMPOSSIBLE, LENIENT, RetrainEnv

pytestmark = pytest.mark.integration


class FakeActivator:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.requested: list[str] = []

    def request_activation(self, version: str) -> bool:
        self.requested.append(version)
        return self.succeeds


def build(
    env: RetrainEnv, *, policy: QualityPolicy = LENIENT, activator: FakeActivator | None = None
):
    datasets, jobs = PgDatasetRepository(env.pool), PgJobRepository(env.pool)
    trainer = Trainer(
        settings=env.settings,
        jobs=jobs,
        datasets=datasets,
        models=PgModelVersionRepository(env.pool),
        activator=activator or FakeActivator(),
        policy=policy,
    )
    dataset_service = DatasetService(settings=env.settings, repo=datasets)
    job_service = JobService(settings=env.settings, jobs=jobs, datasets=datasets)
    return trainer, jobs, dataset_service, job_service


def enqueue(env: RetrainEnv, dataset_service: DatasetService, job_service: JobService):
    version = dataset_service.register("clean_v1.parquet", admin_id=env.admin_id).version
    return job_service.enqueue(
        dataset_version=version, config_name="test_tree", admin_id=env.admin_id
    )


def model_row(env: RetrainEnv, version: str) -> dict:
    with pg.connect("app_migrator", env.db) as conn:
        row = conn.execute(
            "SELECT status, artifact_sha256, metrics, gate FROM model_versions WHERE version = %s",
            (version,),
        ).fetchone()
    assert row is not None
    return row


def test_approved_job_registers_model_and_requests_activation(env: RetrainEnv) -> None:
    activator = FakeActivator()
    trainer, jobs, datasets, job_service = build(env, activator=activator)
    job = enqueue(env, datasets, job_service)

    assert run_once(trainer, jobs, env.settings) is True
    finished = job_service.get(job.id, admin_id=env.admin_id)
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.model_version == "1.0.0"
    assert finished.activation is Activation.SUCCEEDED
    assert activator.requested == ["1.0.0"]

    row = model_row(env, "1.0.0")
    assert row["status"] == "approved"
    assert row["gate"]["passed"] is True
    assert set(row["metrics"]) == {"validation", "test"}
    assert (artifact_dir(env.settings.models_dir, "1.0.0") / "golden.json").is_file()

    with pg.connect("app_migrator", env.db) as conn:
        events = conn.execute(
            "SELECT to_status, db_role, actor_user_id FROM model_version_events"
            " ORDER BY created_at, to_status"
        ).fetchall()
    assert [e["to_status"] for e in events] == ["candidate", "evaluating", "approved"]
    assert {e["db_role"] for e in events} == {"app_retrain"}
    assert {e["actor_user_id"] for e in events} == {env.admin_id}


def test_rejected_model_is_not_activated(env: RetrainEnv) -> None:
    activator = FakeActivator()
    trainer, jobs, datasets, job_service = build(env, policy=IMPOSSIBLE, activator=activator)
    job = enqueue(env, datasets, job_service)
    run_once(trainer, jobs, env.settings)

    finished = job_service.get(job.id, admin_id=env.admin_id)
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.activation is Activation.SKIPPED
    assert activator.requested == []
    assert model_row(env, "1.0.0")["status"] == "rejected"


def test_failed_activation_leaves_model_approved(env: RetrainEnv) -> None:
    trainer, jobs, datasets, job_service = build(env, activator=FakeActivator(succeeds=False))
    job = enqueue(env, datasets, job_service)
    run_once(trainer, jobs, env.settings)
    assert job_service.get(job.id, admin_id=env.admin_id).activation is Activation.FAILED
    assert model_row(env, "1.0.0")["status"] == "approved"


def test_tampered_dataset_fails_job_without_training(env: RetrainEnv) -> None:
    trainer, jobs, datasets, job_service = build(env)
    job = enqueue(env, datasets, job_service)
    path = env.settings.datasets_dir / "clean_v1.parquet"
    path.write_bytes(path.read_bytes() + b"x")

    run_once(trainer, jobs, env.settings)
    finished = job_service.get(job.id, admin_id=env.admin_id)
    assert finished.status is JobStatus.FAILED
    assert "SHA-256" in finished.error
    assert finished.model_version is None


def test_second_job_gets_next_minor_version(env: RetrainEnv) -> None:
    trainer, jobs, datasets, job_service = build(env)
    enqueue(env, datasets, job_service)
    run_once(trainer, jobs, env.settings)
    second = job_service.enqueue(
        dataset_version="v1", config_name="test_tree", admin_id=env.admin_id
    )
    run_once(trainer, jobs, env.settings)
    assert job_service.get(second.id, admin_id=env.admin_id).model_version == "1.1.0"


def test_empty_queue_and_stale_job_recovery(env: RetrainEnv) -> None:
    trainer, jobs, datasets, job_service = build(env)
    assert run_once(trainer, jobs, env.settings) is False

    job = enqueue(env, datasets, job_service)
    assert jobs.claim_next() is not None  # un worker lo tomó y "murió"
    # FORCE RLS: ni el dueño de la tabla ve retrain_jobs sin contexto admin/service.
    with pg.connect("app_retrain", env.db) as conn, conn.transaction():
        conn.execute("SELECT set_config('app.role', 'service', true)")
        conn.execute(
            "UPDATE retrain_jobs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s",
            (job.id,),
        )
    assert jobs.fail_stale(600) == 1
    recovered = job_service.get(job.id, admin_id=env.admin_id)
    assert recovered.status is JobStatus.FAILED
    assert "interrumpió" in recovered.error


def test_claim_is_exclusive(env: RetrainEnv) -> None:
    _, jobs, datasets, job_service = build(env)
    enqueue(env, datasets, job_service)
    job_service.enqueue(dataset_version="v1", config_name="test_tree", admin_id=env.admin_id)
    first = jobs.claim_next()
    assert first is not None
    # Un único trabajo 'running' a la vez (índice único parcial): el segundo no se toma.
    with pytest.raises(Exception, match="retrain_jobs_single_running"):
        jobs.claim_next()
