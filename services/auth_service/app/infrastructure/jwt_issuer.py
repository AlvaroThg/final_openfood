"""Firma de JWT con RS256 y publicación de la clave pública (JWKS).

- La clave privada se carga una vez al arrancar desde un archivo PEM (secreto montado solo en
  este contenedor). Si falta, es inválida o tiene menos de 2048 bits, el servicio no arranca.
- `kid` = huella RFC 7638 de la clave pública. Los verificadores eligen la clave por `kid`, lo
  que permite rotar claves publicando la nueva junto a la anterior.
- La verificación usa el mismo código que el resto de los servicios (svc_common.tokens).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from svc_common.config import ConfigError
from svc_common.tokens import (
    ALGORITHM,
    API_AUDIENCE,
    INTERNAL_AUDIENCE,
    ISSUER,
    Role,
    TokenClaims,
    TokenError,
    decode_token,
    jwk_thumbprint,
)

from ..domain.model import User

MIN_RSA_BITS = 2048


class RsaJwtIssuer:
    def __init__(
        self,
        private_key_pem: bytes,
        *,
        access_token_ttl_s: int,
        service_token_ttl_s: int,
        now: Callable[[], float] = time.time,
    ) -> None:
        try:
            key = serialization.load_pem_private_key(private_key_pem, password=None)
        except (ValueError, TypeError) as exc:
            raise ConfigError("JWT_PRIVATE_KEY_PATH no contiene una clave PEM válida") from exc
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < MIN_RSA_BITS:
            raise ConfigError(f"La clave del JWT debe ser RSA de al menos {MIN_RSA_BITS} bits")

        self._private_key = key
        self._public_key = key.public_key()
        self.access_token_ttl_s = access_token_ttl_s
        self.service_token_ttl_s = service_token_ttl_s
        self._now = now

        jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(self._public_key))
        self.kid = jwk_thumbprint(jwk)
        # Campos explícitos: solo la parte pública. No se copia `key_ops` (RFC 7517 desaconseja
        # usarlo junto con `use`).
        self._jwk = {
            "kty": jwk["kty"],
            "n": jwk["n"],
            "e": jwk["e"],
            "kid": self.kid,
            "alg": ALGORITHM,
            "use": "sig",
        }

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        """Documento JWKS: solo la parte pública (kty, n, e) más kid, alg y use."""
        return {"keys": [dict(self._jwk)]}

    def issue_access_token(self, user: User) -> str:
        return self._sign(str(user.id), user.role, API_AUDIENCE, self.access_token_ttl_s)

    def issue_service_token(self, client_id: str) -> str:
        return self._sign(client_id, Role.SERVICE, INTERNAL_AUDIENCE, self.service_token_ttl_s)

    def verify_access_token(self, token: str) -> TokenClaims:
        return decode_token(token, resolve_key=self._resolve_key, audience=API_AUDIENCE)

    def _resolve_key(self, kid: str | None) -> rsa.RSAPublicKey:
        if kid != self.kid:
            raise TokenError("kid desconocido")
        return self._public_key

    def _sign(self, subject: str, role: Role, audience: str, ttl_s: int) -> str:
        issued_at = int(self._now())
        payload = {
            "iss": ISSUER,
            "aud": audience,
            "sub": subject,
            "role": str(role),
            "iat": issued_at,
            "exp": issued_at + ttl_s,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(
            payload, self._private_key, algorithm=ALGORITHM, headers={"kid": self.kid}
        )
