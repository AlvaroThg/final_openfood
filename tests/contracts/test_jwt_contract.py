"""Contrato entre auth_service (emisor) y los demás servicios (verificadores).

Un verificador solo conoce el documento JWKS publicado: con él tiene que poder validar los
tokens de usuario y de servicio, y distinguir sus audiencias. También se fija la lista de
endpoints públicos de auth_service, para que un cambio de contrato no pase inadvertido.
"""

from __future__ import annotations

from dataclasses import replace

import jwt
import pytest
from fastapi.testclient import TestClient

from services.auth_service.app.container import Container
from services.auth_service.app.main import create_app
from services.auth_service.app.settings import AuthSettings
from svc_common.tokens import API_AUDIENCE, INTERNAL_AUDIENCE, Role, TokenError, decode_token
from tests.helpers.auth_fakes import SERVICE_SECRET, make_auth
from tests.helpers.keys import rsa_private_pem

PASSWORD = "una-clave-larga-y-segura"


@pytest.fixture
def client() -> TestClient:
    h = make_auth()
    settings = AuthSettings(
        database_url="postgresql://no-se-usa", private_key_pem=rsa_private_pem()
    )
    container = Container.create(
        replace(settings), auth=h.service, issuer=h.issuer, health_check=lambda: None
    )
    return TestClient(create_app(container), base_url="https://testserver")


def resolver_from_jwks(jwks: dict):
    keys = {key["kid"]: jwt.PyJWK(key).key for key in jwks["keys"]}

    def resolve(kid: str | None):
        if kid not in keys:
            raise TokenError("kid desconocido")
        return keys[kid]

    return resolve


def test_tokens_verify_with_published_jwks_only(client: TestClient) -> None:
    resolve = resolver_from_jwks(client.get("/.well-known/jwks.json").json())

    client.post("/auth/register", json={"email": "ana@ejemplo.com", "password": PASSWORD})
    user_token = client.post(
        "/auth/login", json={"email": "ana@ejemplo.com", "password": PASSWORD}
    ).json()["access_token"]
    service_token = client.post(
        "/auth/token", json={"client_id": "retrain_service", "client_secret": SERVICE_SECRET}
    ).json()["access_token"]

    user = decode_token(user_token, resolve_key=resolve, audience=API_AUDIENCE)
    service = decode_token(service_token, resolve_key=resolve, audience=INTERNAL_AUDIENCE)
    assert user.role is Role.USER
    assert service.role is Role.SERVICE and service.subject == "retrain_service"

    # Cada token sirve SOLO para su audiencia.
    with pytest.raises(TokenError):
        decode_token(user_token, resolve_key=resolve, audience=INTERNAL_AUDIENCE)
    with pytest.raises(TokenError):
        decode_token(service_token, resolve_key=resolve, audience=API_AUDIENCE)


def test_access_token_header_and_claims_shape(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "ana@ejemplo.com", "password": PASSWORD})
    token = client.post(
        "/auth/login", json={"email": "ana@ejemplo.com", "password": PASSWORD}
    ).json()["access_token"]
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "RS256" and "kid" in header
    assert set(claims) == {"iss", "aud", "sub", "role", "iat", "exp", "jti"}
    assert claims["exp"] - claims["iat"] == 900


def test_public_endpoints_contract(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    exposed = {(method.upper(), path) for path, ops in paths.items() for method in ops}
    assert exposed == {
        ("POST", "/auth/register"),
        ("POST", "/auth/login"),
        ("POST", "/auth/refresh"),
        ("POST", "/auth/logout"),
        ("GET", "/auth/me"),
        ("POST", "/auth/token"),
        ("GET", "/.well-known/jwks.json"),
        ("GET", "/health"),
    }
