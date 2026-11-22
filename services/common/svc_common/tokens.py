"""Contrato del JWT de la plataforma y su verificación.

Quién hace qué:
- auth_service FIRMA con la clave privada RSA (solo él la tiene).
- Todos los servicios VERIFICAN con `decode_token` y la clave pública (JWKS).

Reglas de verificación (todas obligatorias):
- algoritmo exactamente RS256. Se rechaza "none" y se rechaza HS256 firmado con la clave
  pública como secreto (ataque de confusión de algoritmo);
- firma válida con la clave del `kid` del header;
- `iss`, `aud`, `exp`, `iat`, `sub` y `jti` presentes; `iss` y `aud` con el valor esperado;
- el rol debe corresponder a la audiencia: tokens de usuario (user/admin) solo para la API
  pública y tokens de servicio (service) solo para los endpoints internos.

    Access token:  {"iss","aud":"nutriscore-api","sub":<uuid>,"role":"user|admin","iat","exp","jti"}
    Service token: {"iss","aud":"nutriscore-internal","sub":<client_id>,"role":"service",...}
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import jwt

ALGORITHM = "RS256"
ISSUER = "nutriscore-auth"
API_AUDIENCE = "nutriscore-api"
INTERNAL_AUDIENCE = "nutriscore-internal"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "jti")


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"
    SERVICE = "service"


ROLES_BY_AUDIENCE: Mapping[str, frozenset[Role]] = {
    API_AUDIENCE: frozenset({Role.USER, Role.ADMIN}),
    INTERNAL_AUDIENCE: frozenset({Role.SERVICE}),
}


class TokenError(Exception):
    """Token inválido. El mensaje es para logs; al cliente solo se le informa el código."""

    code = "TOKEN_INVALID"


class TokenExpiredError(TokenError):
    code = "TOKEN_EXPIRED"


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    role: Role
    audience: str
    token_id: str
    issued_at: int
    expires_at: int


# Recibe el `kid` del header y devuelve la clave pública con la que verificar.
# Debe lanzar TokenError si el kid es desconocido.
KeyResolver = Callable[[str | None], Any]


def decode_token(
    token: str,
    *,
    resolve_key: KeyResolver,
    audience: str,
    issuer: str = ISSUER,
    leeway_s: int = 0,
) -> TokenClaims:
    """Verifica el token y devuelve sus claims. Lanza TokenExpiredError o TokenError."""
    if audience not in ROLES_BY_AUDIENCE:
        raise ValueError(f"Audiencia desconocida: {audience}")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise TokenError("cabecera ilegible") from exc
    if header.get("alg") != ALGORITHM:
        raise TokenError(f"algoritmo no permitido: {header.get('alg')!r}")

    key = resolve_key(header.get("kid"))
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=issuer,
            leeway=leeway_s,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("token expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(str(exc)) from exc

    try:
        role = Role(str(payload.get("role")))
    except ValueError:
        raise TokenError("rol desconocido") from None
    if role not in ROLES_BY_AUDIENCE[audience]:
        raise TokenError(f"el rol {role} no es válido para la audiencia {audience}")

    return TokenClaims(
        subject=str(payload["sub"]),
        role=role,
        audience=audience,
        token_id=str(payload["jti"]),
        issued_at=int(payload["iat"]),
        expires_at=int(payload["exp"]),
    )


def jwk_thumbprint(jwk: Mapping[str, Any]) -> str:
    """Huella RFC 7638 de una clave RSA pública: se usa como `kid`.

    Depende solo de la clave (e, kty, n), así que cambia si y solo si cambia la clave.
    """
    canonical = json.dumps(
        {"e": jwk["e"], "kty": jwk["kty"], "n": jwk["n"]}, separators=(",", ":"), sort_keys=True
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
