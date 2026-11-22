"""Verificación del JWT (svc_common.tokens): lo que usan TODOS los servicios para aceptar o
rechazar un token. Incluye los ataques clásicos: alg=none y confusión RS256/HS256."""

from __future__ import annotations

import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from svc_common.tokens import (
    API_AUDIENCE,
    INTERNAL_AUDIENCE,
    ISSUER,
    Role,
    TokenError,
    TokenExpiredError,
    decode_token,
    jwk_thumbprint,
)
from tests.helpers.keys import public_pem, rsa_private_pem

PRIVATE = serialization.load_pem_private_key(rsa_private_pem(), password=None)
PUBLIC = PRIVATE.public_key()
KID = "kid-1"


def resolver(kid: str | None) -> object:
    if kid != KID:
        raise TokenError("kid desconocido")
    return PUBLIC


def make_token(*, key: object = PRIVATE, algorithm: str = "RS256", kid: str = KID, **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": API_AUDIENCE,
        "sub": "user-1",
        "role": "user",
        "iat": now,
        "exp": now + 60,
        "jti": "j-1",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def decode(token: str, audience: str = API_AUDIENCE):
    return decode_token(token, resolve_key=resolver, audience=audience)


def test_valid_token_returns_claims() -> None:
    claims = decode(make_token())
    assert claims.subject == "user-1"
    assert claims.role is Role.USER
    assert claims.audience == API_AUDIENCE


def test_expired_token() -> None:
    with pytest.raises(TokenExpiredError):
        decode(make_token(exp=int(time.time()) - 10))


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "otra-api"},
        {"iss": "otro-emisor"},
        {"jti": None},
        {"sub": None},
        {"role": "superadmin"},
        {"role": "service"},  # un token de servicio no sirve en la API pública
    ],
)
def test_invalid_claims_are_rejected(overrides: dict) -> None:
    with pytest.raises(TokenError) as info:
        decode(make_token(**overrides))
    assert not isinstance(info.value, TokenExpiredError)


def test_user_token_is_rejected_on_internal_audience() -> None:
    token = make_token(aud=INTERNAL_AUDIENCE, role="admin")
    with pytest.raises(TokenError):
        decode(token, audience=INTERNAL_AUDIENCE)


def test_service_token_on_internal_audience() -> None:
    claims = decode(make_token(aud=INTERNAL_AUDIENCE, role="service"), audience=INTERNAL_AUDIENCE)
    assert claims.role is Role.SERVICE


def test_signature_from_another_key_is_rejected() -> None:
    attacker = serialization.load_pem_private_key(rsa_private_pem(label="atacante"), password=None)
    with pytest.raises(TokenError):
        decode(make_token(key=attacker))


def test_tampered_payload_is_rejected() -> None:
    header, payload, signature = make_token().split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["role"] = "admin"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(TokenError):
        decode(f"{header}.{forged}.{signature}")


def test_alg_none_is_rejected() -> None:
    token = make_token(key=None, algorithm="none")
    with pytest.raises(TokenError, match="algoritmo"):
        decode(token)


def test_hs256_signed_with_public_key_is_rejected() -> None:
    """Confusión de algoritmo: el atacante firma con HS256 usando la clave PÚBLICA como secreto."""
    header = {"alg": "HS256", "typ": "JWT", "kid": KID}
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": API_AUDIENCE,
        "sub": "x",
        "role": "admin",
        "iat": now,
        "exp": now + 60,
        "jti": "j",
    }

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    import hashlib
    import hmac

    signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(payload).encode())}"
    signature = hmac.new(public_pem(rsa_private_pem()), signing_input.encode(), hashlib.sha256)
    with pytest.raises(TokenError, match="algoritmo"):
        decode(f"{signing_input}.{b64(signature.digest())}")


def test_unknown_kid_is_rejected() -> None:
    with pytest.raises(TokenError):
        decode(make_token(kid="otra"))


def test_garbage_is_rejected() -> None:
    with pytest.raises(TokenError):
        decode("esto.no.es-un-jwt")


def test_jwk_thumbprint_is_deterministic_and_key_specific() -> None:
    jwk_a = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(PUBLIC))
    other = serialization.load_pem_private_key(rsa_private_pem(label="otra"), password=None)
    jwk_b = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(other.public_key()))
    assert jwk_thumbprint(jwk_a) == jwk_thumbprint(dict(reversed(list(jwk_a.items()))))
    assert jwk_thumbprint(jwk_a) != jwk_thumbprint(jwk_b)
