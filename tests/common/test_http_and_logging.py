"""Formato común de errores, X-Request-ID, logs JSON y configuración."""

from __future__ import annotations

import json
import logging

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from svc_common import config
from svc_common.config import ConfigError
from svc_common.http import AppError, RequestContextMiddleware, install_error_handlers
from svc_common.logging import JsonFormatter, request_id_var


class Body(BaseModel):
    password: str


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    @app.post("/echo")
    def echo(body: Body) -> dict[str, str]:
        return {"ok": "sí"}

    @app.get("/app-error")
    def app_error() -> None:
        raise AppError(409, "CONFLICTO", "Algo chocó.", headers={"X-Extra": "1"})

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("detalle interno que no debe salir")

    @app.get("/db")
    def db_down() -> None:
        raise psycopg.OperationalError("connection refused a 10.0.0.5")

    @app.get("/request-id")
    def current_request_id() -> dict[str, str | None]:
        return {"request_id": request_id_var.get()}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(make_app())


def test_request_id_is_generated_and_returned(client: TestClient) -> None:
    response = client.get("/request-id")
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    assert response.json() == {"request_id": request_id}


def test_valid_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/request-id", headers={"X-Request-ID": "frontend-abc.123"})
    assert response.headers["X-Request-ID"] == "frontend-abc.123"


@pytest.mark.parametrize("bad", ["x" * 65, "con espacios", "a;b=c", '{"json":1}', ""])
def test_invalid_incoming_request_id_is_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/request-id", headers={"X-Request-ID": bad})
    assert response.headers["X-Request-ID"] != bad
    assert len(response.headers["X-Request-ID"]) == 32


def test_app_error_format(client: TestClient) -> None:
    response = client.get("/app-error", headers={"X-Request-ID": "rid-1"})
    assert response.status_code == 409
    assert response.headers["X-Extra"] == "1"
    assert response.json() == {
        "error": {"code": "CONFLICTO", "message": "Algo chocó.", "request_id": "rid-1"}
    }


def test_validation_error_never_echoes_input(client: TestClient) -> None:
    response = client.post("/echo", json={"password": 12345678901234})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["loc"] == ["body", "password"]
    assert "12345678901234" not in response.text


def test_unexpected_error_is_generic_500_with_request_id(client: TestClient) -> None:
    response = client.get("/boom", headers={"X-Request-ID": "rid-500"})
    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Error interno del servidor.",
        "request_id": "rid-500",
    }
    assert "detalle interno" not in response.text


def test_database_down_is_503_without_details(client: TestClient) -> None:
    response = client.get("/db")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DB_UNAVAILABLE"
    assert "10.0.0.5" not in response.text


def test_unknown_route_uses_common_format(client: TestClient) -> None:
    response = client.get("/no-existe")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_json_formatter_redacts_sensitive_fields() -> None:
    record = logging.LogRecord("auth", logging.INFO, __file__, 1, "login_ok", None, None)
    record.user_id = "u-1"
    record.refresh_token = "valor-secreto"
    record.password = "otra-cosa"
    token = request_id_var.set("rid-log")
    try:
        line = json.loads(JsonFormatter("auth_service").format(record))
    finally:
        request_id_var.reset(token)
    assert line["msg"] == "login_ok"
    assert line["service"] == "auth_service"
    assert line["request_id"] == "rid-log"
    assert line["user_id"] == "u-1"
    assert line["refresh_token"] == "[redactado]"
    assert line["password"] == "[redactado]"


def test_config_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLAG", "yes")
    monkeypatch.setenv("NUM", "42")
    monkeypatch.setenv("LIST", " a, ,b ")
    assert config.as_bool("FLAG", False) is True
    assert config.as_int("NUM", 0) == 42
    assert config.as_list("LIST") == ("a", "b")
    assert config.as_int("NO_EXISTE", 7) == 7

    monkeypatch.setenv("FLAG", "quizás")
    with pytest.raises(ConfigError):
        config.as_bool("FLAG", False)
    monkeypatch.setenv("NUM", "-1")
    with pytest.raises(ConfigError):
        config.as_int("NUM", 0, minimum=0)
    with pytest.raises(ConfigError, match="OBLIGATORIA_X"):
        config.require("OBLIGATORIA_X")
