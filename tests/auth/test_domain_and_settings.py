from __future__ import annotations

import hashlib

import pytest

from services.auth_service.app.domain.errors import InvalidEmailError, WeakPasswordError
from services.auth_service.app.domain.model import (
    check_password_policy,
    generate_opaque_token,
    hash_opaque_token,
    normalize_email,
    secret_matches,
)
from services.auth_service.app.infrastructure.jwt_issuer import RsaJwtIssuer
from services.auth_service.app.settings import AuthSettings, parse_service_clients
from svc_common.config import ConfigError
from tests.helpers.keys import rsa_private_pem


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Ana@Ejemplo.COM ", "ana@ejemplo.com"), ("x@y.io", "x@y.io")],
)
def test_normalize_email(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "sin-arroba", "a@b", "a b@c.com", "@c.com", "a@" + "b" * 250 + ".com"]
)
def test_invalid_emails(raw: str) -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email(raw)


@pytest.mark.parametrize("password", ["corta", "x" * 11, "x" * 129])
def test_password_policy_rejects(password: str) -> None:
    with pytest.raises(WeakPasswordError):
        check_password_policy(password)


@pytest.mark.parametrize("password", ["x" * 12, "frase larga con espacios y ñ", "x" * 128])
def test_password_policy_accepts(password: str) -> None:
    check_password_policy(password)


def test_opaque_tokens_are_unique_and_hashed_with_sha256() -> None:
    tokens = {generate_opaque_token() for _ in range(100)}
    assert len(tokens) == 100
    token = tokens.pop()
    assert len(token) >= 43  # 32 bytes en base64url
    assert hash_opaque_token(token) == hashlib.sha256(token.encode()).hexdigest()
    assert secret_matches(token, hash_opaque_token(token))
    assert not secret_matches(token + "x", hash_opaque_token(token))


def test_parse_service_clients() -> None:
    digest = "a" * 64
    assert parse_service_clients(f"retrain_service:{digest}, otro_svc:{'b' * 64}") == {
        "retrain_service": digest,
        "otro_svc": "b" * 64,
    }
    assert parse_service_clients("") == {}


@pytest.mark.parametrize(
    "raw",
    [
        "retrain_service",
        "retrain_service:corto",
        "Mayus:" + "a" * 64,
        f"a1:{'a' * 64}",
        f"svc_x:{'a' * 64},svc_x:{'b' * 64}",
    ],
)
def test_parse_service_clients_rejects(raw: str) -> None:
    with pytest.raises(ConfigError):
        parse_service_clients(raw)


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    key = tmp_path / "jwt.pem"
    key.write_bytes(rsa_private_pem())
    monkeypatch.setenv("DATABASE_URL", "postgresql://app_auth:x@db/nutriscore")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("COOKIE_SECURE", "false")
    settings = AuthSettings.from_env()
    assert settings.cookie_secure is False
    assert settings.access_token_ttl_s == 900
    # Los secretos no aparecen al imprimir la configuración.
    assert "BEGIN PRIVATE KEY" not in repr(settings)
    assert "app_auth:x" not in repr(settings)


def test_settings_fail_fast(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(tmp_path / "no-existe.pem"))
    with pytest.raises(ConfigError, match="JWT_PRIVATE_KEY_PATH"):
        AuthSettings.from_env()

    key = tmp_path / "jwt.pem"
    key.write_bytes(rsa_private_pem())
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(key))
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        AuthSettings.from_env()

    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@ejemplo.com")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="ADMIN_PASSWORD"):
        AuthSettings.from_env()


def test_issuer_rejects_weak_or_invalid_keys() -> None:
    with pytest.raises(ConfigError, match="2048"):
        RsaJwtIssuer(rsa_private_pem(bits=1024), access_token_ttl_s=1, service_token_ttl_s=1)
    with pytest.raises(ConfigError):
        RsaJwtIssuer(b"no es un pem", access_token_ttl_s=1, service_token_ttl_s=1)


def test_jwks_exposes_only_public_parts() -> None:
    issuer = RsaJwtIssuer(rsa_private_pem(), access_token_ttl_s=900, service_token_ttl_s=300)
    (key,) = issuer.jwks()["keys"]
    assert set(key) == {"kty", "n", "e", "kid", "alg", "use"}
    assert key["kid"] == issuer.kid
    assert key["alg"] == "RS256"
