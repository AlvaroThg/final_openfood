"""API HTTP de auth_service con puertos en memoria: contrato, cookies, errores y rate limit."""

from __future__ import annotations

from dataclasses import replace

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from services.auth_service.app.api.routes import client_ip
from services.auth_service.app.container import Container
from services.auth_service.app.main import create_app
from services.auth_service.app.settings import AuthSettings
from tests.helpers.auth_fakes import SERVICE_SECRET, AuthHarness, make_auth, make_issuer
from tests.helpers.keys import rsa_private_pem

PASSWORD = "una-clave-larga-y-segura"


def build_client(h: AuthHarness, *, health_check=lambda: None, **settings_overrides) -> TestClient:
    settings = replace(
        AuthSettings(database_url="postgresql://no-se-usa", private_key_pem=rsa_private_pem()),
        **settings_overrides,
    )
    container = Container.create(
        settings, auth=h.service, issuer=h.issuer, health_check=health_check
    )
    # https: el cliente solo reenvía cookies Secure por HTTPS, igual que un navegador.
    return TestClient(create_app(container), base_url="https://testserver")


def use_refresh_cookie(client: TestClient, value: str | None) -> None:
    """Deja en el cliente solo esa cookie de refresh (p. ej. la copia "robada")."""
    client.cookies.clear()
    if value is not None:
        client.cookies.set("refresh_token", value)


@pytest.fixture
def h() -> AuthHarness:
    return make_auth()


@pytest.fixture
def client(h: AuthHarness) -> TestClient:
    return build_client(h)


def login(client: TestClient, email: str = "ana@ejemplo.com"):
    client.post("/auth/register", json={"email": email, "password": PASSWORD})
    return client.post("/auth/login", json={"email": email, "password": PASSWORD})


# ---------------------------------------------------------------------------- registro
def test_register_returns_public_user(client: TestClient) -> None:
    response = client.post(
        "/auth/register", json={"email": "Ana@Ejemplo.com", "password": PASSWORD}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana@ejemplo.com"
    assert body["role"] == "user"
    assert set(body) == {"id", "email", "role"}


def test_register_rejects_role_escalation_attempt(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "ana@ejemplo.com", "password": PASSWORD, "role": "admin"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_register_short_password_is_not_echoed(client: TestClient) -> None:
    response = client.post(
        "/auth/register", json={"email": "ana@ejemplo.com", "password": "Secreta1"}
    )
    assert response.status_code == 422
    assert "Secreta1" not in response.text


def test_register_duplicate_is_409(client: TestClient) -> None:
    login(client)
    response = client.post(
        "/auth/register", json={"email": "ANA@ejemplo.com", "password": PASSWORD}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


# ------------------------------------------------------------------------------- login
def test_login_sets_secure_refresh_cookie(client: TestClient) -> None:
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert response.headers["Cache-Control"] == "no-store"

    cookie = response.headers["set-cookie"].lower()
    for attribute in (
        "refresh_token=",
        "httponly",
        "secure",
        "samesite=strict",
        "path=/auth",
        "max-age=604800",
    ):
        assert attribute in cookie
    assert body["access_token"] not in response.headers["set-cookie"]


def test_cookie_secure_can_be_disabled_for_plain_localhost(h: AuthHarness) -> None:
    client = build_client(h, cookie_secure=False)
    assert "secure" not in login(client).headers["set-cookie"].lower()


def test_failed_logins_look_identical(client: TestClient) -> None:
    login(client)
    wrong_password = client.post(
        "/auth/login", json={"email": "ana@ejemplo.com", "password": "x" * 12}
    )
    unknown_email = client.post(
        "/auth/login", json={"email": "nadie@ejemplo.com", "password": PASSWORD}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    strip = lambda r: {k: v for k, v in r.json()["error"].items() if k != "request_id"}  # noqa: E731
    assert (
        strip(wrong_password)
        == strip(unknown_email)
        == {
            "code": "INVALID_CREDENTIALS",
            "message": "Email o contraseña incorrectos.",
        }
    )
    assert "set-cookie" not in wrong_password.headers


# ----------------------------------------------------------------------------- refresh
def test_refresh_rotates_cookie_and_returns_new_access_token(client: TestClient) -> None:
    first = login(client)
    old_cookie = client.cookies.get("refresh_token")
    response = client.post("/auth/refresh")
    assert response.status_code == 200
    assert response.json()["access_token"] != first.json()["access_token"]
    assert client.cookies.get("refresh_token") not in (None, old_cookie)


def test_refresh_reuse_is_detected_and_cookie_cleared(client: TestClient) -> None:
    login(client)
    stolen = client.cookies.get("refresh_token")
    client.post("/auth/refresh")

    use_refresh_cookie(client, stolen)
    response = client.post("/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_REUSED"
    assert 'refresh_token=""' in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_refresh_without_cookie(client: TestClient) -> None:
    response = client.post("/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_INVALID"


def test_logout_clears_cookie_and_ends_session(client: TestClient) -> None:
    login(client)
    cookie = client.cookies.get("refresh_token")
    response = client.post("/auth/logout")
    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    use_refresh_cookie(client, cookie)
    again = client.post("/auth/refresh")
    assert again.json()["error"]["code"] == "REFRESH_INVALID"


# ---------------------------------------------------------------------------------- me
def test_me_with_bearer_token(client: TestClient) -> None:
    token = login(client).json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "ana@ejemplo.com"


@pytest.mark.parametrize(
    ("header", "code"),
    [(None, "TOKEN_MISSING"), ("Basic abc", "TOKEN_MISSING"), ("Bearer basura", "TOKEN_INVALID")],
)
def test_me_rejects_missing_or_invalid_token(client: TestClient, header, code: str) -> None:
    headers = {"Authorization": header} if header else {}
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == code
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


def test_me_with_expired_token(h: AuthHarness, client: TestClient) -> None:
    user = h.service.register("ana@ejemplo.com", PASSWORD)
    expired = make_issuer(now=1_000_000).issue_access_token(user)  # emitido en 1970
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


# -------------------------------------------------------------------- token de servicio
def test_service_token_endpoint(client: TestClient) -> None:
    ok = client.post(
        "/auth/token", json={"client_id": "retrain_service", "client_secret": SERVICE_SECRET}
    )
    assert ok.status_code == 200
    assert ok.headers["Cache-Control"] == "no-store"
    bad = client.post("/auth/token", json={"client_id": "retrain_service", "client_secret": "no"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_CLIENT"


# ------------------------------------------------------------------------- jwks/health
def test_jwks_is_public_and_cacheable(client: TestClient, h: AuthHarness) -> None:
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=300"
    assert response.json()["keys"][0]["kid"] == h.issuer.kid
    assert "d" not in response.json()["keys"][0]


def test_health_reports_database_down_as_503(h: AuthHarness) -> None:
    def failing() -> None:
        raise psycopg.OperationalError("sin conexión")

    assert build_client(h).get("/health").json() == {"status": "ok"}
    response = build_client(h, health_check=failing).get("/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DB_UNAVAILABLE"


# -------------------------------------------------------------------------- rate limit
def test_login_rate_limit_per_email(h: AuthHarness) -> None:
    client = build_client(h, login_per_email_per_minute=3)
    for _ in range(3):
        client.post("/auth/login", json={"email": "ana@ejemplo.com", "password": "x" * 12})
    response = client.post("/auth/login", json={"email": "ANA@ejemplo.com", "password": "x" * 12})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert int(response.headers["Retry-After"]) > 0


def test_login_rate_limit_per_ip(h: AuthHarness) -> None:
    client = build_client(h, login_per_ip_per_minute=2)
    statuses = [
        client.post(
            "/auth/login", json={"email": f"u{i}@ejemplo.com", "password": "x" * 12}
        ).status_code
        for i in range(3)
    ]
    assert statuses == [401, 401, 429]


def test_register_rate_limit_per_ip(h: AuthHarness) -> None:
    client = build_client(h, register_per_ip_per_hour=1)
    client.post("/auth/register", json={"email": "a@ejemplo.com", "password": PASSWORD})
    response = client.post("/auth/register", json={"email": "b@ejemplo.com", "password": PASSWORD})
    assert response.status_code == 429


# ----------------------------------------------------------------------------- client_ip
def _request(peer: str, forwarded: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_client_ip_ignores_forwarded_header_without_trusted_proxy() -> None:
    assert client_ip(_request("10.0.0.2", "1.2.3.4"), 0) == "10.0.0.2"


def test_client_ip_uses_address_appended_by_trusted_proxy() -> None:
    # El cliente intentó falsificar "6.6.6.6"; el proxy agregó la IP real al final.
    assert client_ip(_request("10.0.0.2", "6.6.6.6, 1.2.3.4"), 1) == "1.2.3.4"
    assert client_ip(_request("10.0.0.2", None), 1) == "10.0.0.2"
