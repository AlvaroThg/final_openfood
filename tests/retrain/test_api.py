"""API de reentrenamiento (solo admin) contra PostgreSQL real."""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from services.retrain_service.app.container import build_container
from services.retrain_service.app.main import create_app
from svc_common.tokens import Role
from tests.helpers.ia_fakes import jwks_resolver, user_token
from tests.retrain.conftest import RetrainEnv

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env: RetrainEnv) -> Iterator[TestClient]:
    container = build_container(env.settings, pool=env.pool, key_resolver=jwks_resolver())
    with TestClient(create_app(container)) as test_client:
        yield test_client


@pytest.fixture
def admin(env: RetrainEnv) -> dict[str, str]:
    token, _ = user_token(Role.ADMIN, env.admin_id)
    return {"Authorization": f"Bearer {token}"}


def test_only_admins(client: TestClient) -> None:
    token, _ = user_token(Role.USER)
    headers = {"Authorization": f"Bearer {token}"}
    for method, path in [
        ("get", "/retrain/datasets"),
        ("get", "/retrain/jobs"),
        ("get", "/retrain/configs"),
    ]:
        assert getattr(client, method)(path, headers=headers).status_code == 403
    assert client.get("/retrain/jobs").status_code == 401


def test_register_dataset_and_enqueue_job(client: TestClient, admin: dict) -> None:
    assert client.get("/retrain/configs", headers=admin).json() == ["test_tree"]

    created = client.post(
        "/retrain/datasets", json={"file_name": "clean_v1.parquet"}, headers=admin
    )
    assert created.status_code == 201
    assert created.json()["version"] == "v1"
    assert set(created.json()["class_distribution"]) == {"A", "B", "C", "D", "E"}

    again = client.post("/retrain/datasets", json={"file_name": "clean_v1.parquet"}, headers=admin)
    assert again.status_code == 409

    job = client.post(
        "/retrain/jobs", json={"dataset_version": "v1", "config_name": "test_tree"}, headers=admin
    )
    assert job.status_code == 202
    assert job.json()["status"] == "queued"
    fetched = client.get(f"/retrain/jobs/{job.json()['id']}", headers=admin).json()
    assert fetched["id"] == job.json()["id"]
    assert len(client.get("/retrain/jobs", headers=admin).json()) == 1


@pytest.mark.parametrize("name", ["../secrets.parquet", "/etc/passwd", "x.csv", "a/b.parquet"])
def test_dataset_name_cannot_escape_folder(client: TestClient, admin: dict, name: str) -> None:
    response = client.post("/retrain/datasets", json={"file_name": name}, headers=admin)
    assert response.status_code == 422


def test_missing_and_invalid_datasets(client: TestClient, admin: dict, env: RetrainEnv) -> None:
    missing = client.post("/retrain/datasets", json={"file_name": "nada.parquet"}, headers=admin)
    assert missing.json()["error"]["code"] == "DATASET_FILE_NOT_FOUND"

    pd.DataFrame({"x": [1, 2]}).to_parquet(env.settings.datasets_dir / "malo.parquet")
    invalid = client.post("/retrain/datasets", json={"file_name": "malo.parquet"}, headers=admin)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_DATASET"
    assert invalid.json()["error"]["details"]


def test_job_validation(client: TestClient, admin: dict) -> None:
    client.post("/retrain/datasets", json={"file_name": "clean_v1.parquet"}, headers=admin)
    unknown_config = client.post(
        "/retrain/jobs",
        json={"dataset_version": "v1", "config_name": "lightgbm_sin_formula"},
        headers=admin,
    )
    assert unknown_config.json()["error"]["code"] == "UNKNOWN_CONFIG"
    unknown_dataset = client.post(
        "/retrain/jobs", json={"dataset_version": "v9", "config_name": "test_tree"}, headers=admin
    )
    assert unknown_dataset.json()["error"]["code"] == "DATASET_NOT_FOUND"
