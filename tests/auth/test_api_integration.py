"""auth_service completo contra PostgreSQL real, conectado como app_auth (como en producción)."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from services.auth_service.app.container import Container, build_container
from services.auth_service.app.domain.errors import RefreshTokenReusedError
from services.auth_service.app.main import create_app
from services.auth_service.app.settings import AuthSettings
from tests.helpers import pg
from tests.helpers.auth_fakes import RecordingHasher
from tests.helpers.keys import rsa_private_pem

pytestmark = pytest.mark.integration

PASSWORD = "una-clave-larga-y-segura"
ADMIN_PASSWORD = "clave-del-administrador"


def use_refresh_cookie(client: TestClient, value: str | None) -> None:
    """Deja en el cliente solo esa cookie de refresh (p. ej. la copia "robada")."""
    client.cookies.clear()
    if value is not None:
        client.cookies.set("refresh_token", value)


@pytest.fixture
def container(db: str) -> Container:
    settings = AuthSettings(
        database_url=pg.url_for("app_auth", db),
        private_key_pem=rsa_private_pem(),
        admin_email="admin@ejemplo.com",
        admin_password=ADMIN_PASSWORD,
    )
    return build_container(settings, hasher=RecordingHasher())


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    # El context manager ejecuta el arranque real: abre el pool y crea el administrador.
    with TestClient(create_app(container), base_url="https://testserver") as test_client:
        yield test_client


def _register_and_login(client: TestClient, email: str = "ana@ejemplo.com") -> str:
    assert (
        client.post("/auth/register", json={"email": email, "password": PASSWORD}).status_code
        == 201
    )
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return str(response.json()["access_token"])


def test_full_session_flow(client: TestClient, db: str) -> None:
    access = _register_and_login(client)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.json()["email"] == "ana@ejemplo.com"

    stolen = client.cookies.get("refresh_token")
    assert client.post("/auth/refresh").status_code == 200
    use_refresh_cookie(client, stolen)
    reused = client.post("/auth/refresh")
    assert reused.json()["error"]["code"] == "REFRESH_REUSED"

    with pg.connect("app_migrator", db) as conn:
        rows = conn.execute(
            "SELECT revoked_reason FROM refresh_tokens ORDER BY created_at"
        ).fetchall()
    assert [r["revoked_reason"] for r in rows] == ["rotated", "reuse_detected"]

    # Tras el reuso se puede volver a iniciar sesión normalmente.
    again = client.post("/auth/login", json={"email": "ana@ejemplo.com", "password": PASSWORD})
    assert again.status_code == 200


def test_database_stores_only_hashes(client: TestClient, db: str) -> None:
    _register_and_login(client)
    raw_refresh = client.cookies.get("refresh_token")
    with pg.connect("app_migrator", db) as conn:
        user = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'ana@ejemplo.com'"
        ).fetchone()
        token = conn.execute("SELECT token_hash FROM refresh_tokens").fetchone()
    assert user is not None and token is not None
    assert user["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in user["password_hash"]
    assert token["token_hash"] == hashlib.sha256(raw_refresh.encode()).hexdigest()


def test_initial_admin_created_on_startup(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"email": "admin@ejemplo.com", "password": ADMIN_PASSWORD}
    )
    access = response.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.json()["role"] == "admin"


def test_duplicate_email_uses_unique_constraint(client: TestClient) -> None:
    _register_and_login(client)
    response = client.post(
        "/auth/register", json={"email": "Ana@Ejemplo.com", "password": PASSWORD}
    )
    assert response.status_code == 409


def test_health_with_real_database(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_concurrent_refresh_with_same_token_rotates_only_once(
    client: TestClient, container: Container
) -> None:
    """FOR UPDATE serializa los dos refrescos: uno rota, el otro se detecta como reuso."""
    _register_and_login(client)
    token = client.cookies.get("refresh_token")
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt() -> None:
        barrier.wait()
        try:
            container.auth.refresh(token)
            results.append("rotated")
        except RefreshTokenReusedError:
            results.append("reused")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["reused", "rotated"]
