"""API de ia_backend con piezas en memoria: autorización por rol y audiencia, validación de la
entrada generada desde nutriscore.schema, errores y rate limit."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from nutriscore import schema
from nutriscore.lifecycle import ModelStatus
from services.ia_backend.app.container import Container
from services.ia_backend.app.main import create_app
from svc_common.tokens import Role
from tests.helpers.ia_fakes import (
    FakePredictionRepository,
    FakeRegistry,
    FakeStore,
    ia_settings,
    jwks_resolver,
    model_record,
    service_token,
    user_token,
)
from tests.helpers.models import valid_features


def build(*records, health_check=lambda: None, **settings) -> tuple[TestClient, Container]:
    container = Container.create(
        ia_settings(**settings),
        registry=FakeRegistry(*records),
        store=FakeStore(),
        prediction_repo=FakePredictionRepository(),
        key_resolver=jwks_resolver(),
        health_check=health_check,
    )
    client = TestClient(create_app(container))
    client.__enter__()  # ejecuta el arranque (carga el modelo activo)
    return client, container


@pytest.fixture
def client() -> TestClient:
    return build(
        model_record("1.0.0", ModelStatus.ACTIVE), model_record("1.1.0", ModelStatus.APPROVED)
    )[0]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------------- autenticación
def test_predict_requires_token(client: TestClient) -> None:
    assert client.post("/predict", json=valid_features()).json()["error"]["code"] == "TOKEN_MISSING"


def test_service_token_is_not_valid_for_public_api(client: TestClient) -> None:
    response = client.post("/predict", json=valid_features(), headers=bearer(service_token()))
    assert response.status_code == 401


def test_user_cannot_use_admin_endpoints(client: TestClient) -> None:
    token, _ = user_token(Role.USER)
    assert client.get("/admin/models", headers=bearer(token)).status_code == 403
    assert client.post("/admin/models/1.1.0/activate", headers=bearer(token)).status_code == 403


def test_user_token_cannot_call_internal_endpoint(client: TestClient) -> None:
    token, _ = user_token(Role.ADMIN)
    response = client.post("/internal/models/1.1.0/activate", headers=bearer(token))
    assert response.status_code == 401  # audiencia equivocada


def test_internal_endpoint_only_for_allowed_clients(client: TestClient) -> None:
    other = client.post(
        "/internal/models/1.1.0/activate", headers=bearer(service_token("otro_svc"))
    )
    assert other.status_code == 403
    ok = client.post("/internal/models/1.1.0/activate", headers=bearer(service_token()))
    assert ok.json() == {"version": "1.1.0", "changed": True, "previous": "1.0.0"}


# ---------------------------------------------------------------------------- predicción
def test_predict_success(client: TestClient) -> None:
    token, user_id = user_token()
    response = client.post("/predict", json=valid_features(), headers=bearer(token))
    assert response.status_code == 201
    body = response.json()
    assert body["grade"] == "B"  # FixedPredictor: B tiene 0.6
    assert body["model_version"] == "1.0.0"
    assert set(body["probabilities"]) == set(schema.GRADES)
    assert body["input"]["sugars_100g"] == 12.0


@pytest.mark.parametrize(
    "change",
    [
        {"sugars_100g": None},  # obligatorio
        {"salt_100g": -1},  # fuera de rango
        {"energy_100g": 99_999},
        {"color": "rojo"},  # campo desconocido
        {"pnns_groups_1": "x" * 101},
    ],
)
def test_predict_validation(client: TestClient, change: dict) -> None:
    token, _ = user_token()
    payload = {**valid_features(), **change}
    payload = {k: v for k, v in payload.items() if v is not None}
    response = client.post("/predict", json=payload, headers=bearer(token))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_predict_rejects_physically_impossible_input(client: TestClient) -> None:
    token, _ = user_token()
    payload = {**valid_features(), "saturated_fat_100g": 20.0, "fat_100g": 10.0}
    response = client.post("/predict", json=payload, headers=bearer(token))
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INCONSISTENT_INPUT"
    assert [d["rule"] for d in error["details"]] == ["saturated_fat_gt_fat"]


def test_predict_without_active_model_is_503() -> None:
    client, _ = build(model_record("1.0.0", ModelStatus.APPROVED))
    token, _ = user_token()
    response = client.post("/predict", json=valid_features(), headers=bearer(token))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_predict_rate_limit_per_user() -> None:
    client, _ = build(model_record("1.0.0", ModelStatus.ACTIVE), predict_per_user_per_minute=2)
    token, _ = user_token()
    codes = [
        client.post("/predict", json=valid_features(), headers=bearer(token)).status_code
        for _ in range(3)
    ]
    assert codes == [201, 201, 429]
    other, _ = user_token()
    assert client.post("/predict", json=valid_features(), headers=bearer(other)).status_code == 201


# ----------------------------------------------------------------------------- historial
def test_history_is_per_user_and_all_requires_admin(client: TestClient) -> None:
    ana, _ = user_token()
    beto, _ = user_token()
    client.post("/predict", json=valid_features(), headers=bearer(ana))
    client.post("/predict", json=valid_features(), headers=bearer(beto))

    assert len(client.get("/predictions", headers=bearer(ana)).json()["items"]) == 1
    assert client.get("/predictions?all=true", headers=bearer(ana)).status_code == 403
    admin, _ = user_token(Role.ADMIN)
    assert len(client.get("/predictions?all=true", headers=bearer(admin)).json()["items"]) == 2


def test_history_pagination_bounds(client: TestClient) -> None:
    token, _ = user_token()
    assert client.get("/predictions?limit=101", headers=bearer(token)).status_code == 422
    assert client.get("/predictions?offset=-1", headers=bearer(token)).status_code == 422


# ------------------------------------------------------------------------- modelo y salud
def test_model_schema_is_public_and_matches_contract(client: TestClient) -> None:
    body = client.get("/model/schema").json()
    required = [f["name"] for f in body["numeric"] if f["required"]]
    assert tuple(required) == schema.REQUIRED_FEATURES
    assert body["grades"] == list(schema.GRADES)


def test_model_info_and_metrics(client: TestClient) -> None:
    token, _ = user_token()
    assert client.get("/model/info", headers=bearer(token)).json()["version"] == "1.0.0"
    metrics = client.get("/model/metrics", headers=bearer(token)).json()
    assert metrics["metrics"]["validation"]["f1_macro"] == 0.9


def test_admin_lists_and_activates(client: TestClient) -> None:
    token, _ = user_token(Role.ADMIN)
    versions = client.get("/admin/models", headers=bearer(token)).json()
    assert {v["version"] for v in versions} == {"1.0.0", "1.1.0"}
    activated = client.post("/admin/models/1.1.0/activate", headers=bearer(token)).json()
    assert activated["changed"] is True
    again = client.post("/admin/models/1.1.0/activate", headers=bearer(token)).json()
    assert again["changed"] is False


def test_activation_errors(client: TestClient) -> None:
    token, _ = user_token(Role.ADMIN)
    assert client.post("/admin/models/9.9.9/activate", headers=bearer(token)).status_code == 404


def test_health_states() -> None:
    client, _ = build(model_record("1.0.0", ModelStatus.ACTIVE))
    assert client.get("/health").json() == {"status": "ok", "model_version": "1.0.0"}

    def db_down() -> None:
        raise psycopg.OperationalError("sin conexión")

    down, _ = build(health_check=db_down)
    assert down.get("/health").json()["error"]["code"] == "DB_UNAVAILABLE"

    broken_client, container = build(model_record("1.0.0", ModelStatus.ACTIVE))
    container.models._load_error = "hash no coincide"
    assert broken_client.get("/health").json()["error"]["code"] == "MODEL_LOAD_FAILED"
