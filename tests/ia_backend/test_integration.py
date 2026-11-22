"""ia_backend completo: PostgreSQL real (como app_ia), artefactos reales en disco y tokens RS256.

Recorre el camino de producción: versión aprobada por retrain → activación por un admin →
predicción guardada con RLS → historial aislado → rollback → artefacto manipulado rechazado.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.ia_backend.app.container import Container
from services.ia_backend.app.infrastructure.artifact_store import FsArtifactStore
from services.ia_backend.app.infrastructure.pg_repositories import (
    PgModelRegistry,
    PgPredictionRepository,
)
from services.ia_backend.app.main import create_app
from svc_common.db import create_pool
from svc_common.tokens import Role
from tests.helpers import pg
from tests.helpers.ia_fakes import ia_settings, jwks_resolver, service_token, user_token
from tests.helpers.models import register_model, save_small_model, valid_features

pytestmark = pytest.mark.integration


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    path = tmp_path / "models"
    path.mkdir()
    return path


@pytest.fixture
def setup(db: str, models_dir: Path) -> dict:
    dataset_id = pg.insert_dataset(db)
    people = {
        "ana": pg.insert_user(db, "ana@ejemplo.com"),
        "beto": pg.insert_user(db, "beto@ejemplo.com"),
        "admin": pg.insert_user(db, "admin@ejemplo.com", role="admin"),
    }
    for version, seed in (("1.0.0", 0), ("1.1.0", 1)):
        info = save_small_model(models_dir, version, seed=seed)
        register_model(db, version, info, dataset_id=dataset_id)
    return people


def make_client(db: str, models_dir: Path) -> TestClient:
    pool = create_pool(pg.url_for("app_ia", db), name="test_ia_backend")
    container = Container.create(
        ia_settings(models_dir=models_dir),
        registry=PgModelRegistry(pool),
        store=FsArtifactStore(models_dir),
        prediction_repo=PgPredictionRepository(pool),
        key_resolver=jwks_resolver(),
        health_check=lambda: None,
        shutdown=pool.close,
    )
    original_startup = container.startup

    def startup() -> None:
        pool.open(wait=True)
        original_startup()

    container.startup = startup
    return TestClient(create_app(container))


@pytest.fixture
def client(db: str, models_dir: Path, setup: dict) -> Iterator[TestClient]:
    with make_client(db, models_dir) as test_client:
        yield test_client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def status_of(db: str, version: str) -> str:
    with pg.connect("app_migrator", db) as conn:
        row = conn.execute(
            "SELECT status FROM model_versions WHERE version = %s", (version,)
        ).fetchone()
    assert row is not None
    return str(row["status"])


def test_activation_prediction_and_isolated_history(
    client: TestClient, db: str, setup: dict
) -> None:
    admin, _ = user_token(Role.ADMIN, setup["admin"])
    ana, _ = user_token(Role.USER, setup["ana"])
    beto, _ = user_token(Role.USER, setup["beto"])

    # Sin modelo activo todavía.
    assert client.post("/predict", json=valid_features(), headers=bearer(ana)).status_code == 503

    activated = client.post("/admin/models/1.0.0/activate", headers=bearer(admin))
    assert activated.json() == {"version": "1.0.0", "changed": True, "previous": None}
    assert status_of(db, "1.0.0") == "active"

    prediction = client.post("/predict", json=valid_features(), headers=bearer(ana))
    assert prediction.status_code == 201
    assert prediction.json()["model_version"] == "1.0.0"
    client.post("/predict", json=valid_features(), headers=bearer(beto))

    ana_history = client.get("/predictions", headers=bearer(ana)).json()["items"]
    assert [item["id"] for item in ana_history] == [prediction.json()["id"]]
    admin_all = client.get("/predictions?all=true", headers=bearer(admin)).json()["items"]
    assert len(admin_all) == 2

    with pg.connect("app_migrator", db) as conn:
        event = conn.execute(
            "SELECT db_role, actor_user_id FROM model_version_events"
            " WHERE to_status = 'active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert event == {"db_role": "app_ia", "actor_user_id": setup["admin"]}


def test_service_activation_and_rollback(client: TestClient, db: str, setup: dict) -> None:
    admin, _ = user_token(Role.ADMIN, setup["admin"])
    client.post("/admin/models/1.0.0/activate", headers=bearer(admin))

    by_service = client.post("/internal/models/1.1.0/activate", headers=bearer(service_token()))
    assert by_service.json() == {"version": "1.1.0", "changed": True, "previous": "1.0.0"}
    assert (status_of(db, "1.0.0"), status_of(db, "1.1.0")) == ("archived", "active")

    rollback = client.post("/admin/models/1.0.0/activate", headers=bearer(admin))
    assert rollback.json()["previous"] == "1.1.0"
    assert (status_of(db, "1.0.0"), status_of(db, "1.1.0")) == ("active", "archived")

    ana, _ = user_token(Role.USER, setup["ana"])
    response = client.post("/predict", json=valid_features(), headers=bearer(ana))
    assert response.json()["model_version"] == "1.0.0"


def test_tampered_artifact_is_rejected_and_previous_keeps_serving(
    client: TestClient, db: str, models_dir: Path, setup: dict
) -> None:
    admin, _ = user_token(Role.ADMIN, setup["admin"])
    client.post("/admin/models/1.0.0/activate", headers=bearer(admin))

    model_file = models_dir / "v1.1.0" / "model.joblib"
    model_file.write_bytes(model_file.read_bytes() + b"manipulado")

    response = client.post("/admin/models/1.1.0/activate", headers=bearer(admin))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_ARTIFACT_INVALID"
    assert (status_of(db, "1.0.0"), status_of(db, "1.1.0")) == ("active", "approved")

    ana, _ = user_token(Role.USER, setup["ana"])
    assert (
        client.post("/predict", json=valid_features(), headers=bearer(ana)).json()["model_version"]
        == "1.0.0"
    )


def test_rejected_version_cannot_be_activated(
    client: TestClient, db: str, models_dir: Path, setup: dict
) -> None:
    dataset_id = pg.insert_dataset(db, "v2")
    info = save_small_model(models_dir, "2.0.0", seed=3)
    register_model(db, "2.0.0", info, dataset_id=dataset_id, approve=False)
    admin, _ = user_token(Role.ADMIN, setup["admin"])
    response = client.post("/admin/models/2.0.0/activate", headers=bearer(admin))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_NOT_ACTIVATABLE"


def test_restart_loads_active_model(db: str, models_dir: Path, setup: dict) -> None:
    admin, _ = user_token(Role.ADMIN, setup["admin"])
    with make_client(db, models_dir) as first:
        first.post("/admin/models/1.1.0/activate", headers=bearer(admin))
    with make_client(db, models_dir) as restarted:
        assert restarted.get("/health").json() == {"status": "ok", "model_version": "1.1.0"}


def test_restart_with_tampered_active_artifact_is_unhealthy(
    db: str, models_dir: Path, setup: dict
) -> None:
    admin, _ = user_token(Role.ADMIN, setup["admin"])
    with make_client(db, models_dir) as first:
        first.post("/admin/models/1.0.0/activate", headers=bearer(admin))
    model_file = models_dir / "v1.0.0" / "model.joblib"
    model_file.write_bytes(b"pickle malicioso")
    with make_client(db, models_dir) as restarted:
        assert restarted.get("/health").json()["error"]["code"] == "MODEL_LOAD_FAILED"
        ana, _ = user_token(Role.USER, setup["ana"])
        response = restarted.post("/predict", json=valid_features(), headers=bearer(ana))
        assert response.status_code == 503
