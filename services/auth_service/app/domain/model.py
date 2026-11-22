"""Entidades y reglas de negocio de la autenticación. Sin dependencias de infraestructura."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from svc_common.tokens import Role

from .errors import InvalidEmailError, WeakPasswordError

EMAIL_MAX_LENGTH = 254
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128  # acota el costo de argon2 frente a entradas gigantes
OPAQUE_TOKEN_BYTES = 32

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    password_hash: str = field(repr=False)
    role: Role
    is_active: bool


class RevocationReason(StrEnum):
    ROTATED = "rotated"  # se cambió por uno nuevo en /auth/refresh
    LOGOUT = "logout"  # el usuario cerró la sesión
    REUSE_DETECTED = "reuse_detected"  # alguien presentó un token ya revocado


@dataclass(frozen=True)
class RefreshTokenRecord:
    """Fila de refresh_tokens. Nunca contiene el token en claro, solo su hash."""

    id: UUID
    user_id: UUID
    family_id: UUID
    expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: RevocationReason | None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def normalize_email(raw: str) -> str:
    """Quita espacios y pasa a minúsculas; valida un formato mínimo (algo@dominio.tld).

    No se valida que el buzón exista: eso exigiría enviar un correo y está fuera de alcance.
    """
    email = raw.strip().lower()
    if len(email) > EMAIL_MAX_LENGTH or not _EMAIL.fullmatch(email):
        raise InvalidEmailError("email inválido")
    return email


def check_password_policy(password: str) -> None:
    """Política: longitud entre 12 y 128 (NIST SP 800-63B prioriza la longitud)."""
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise WeakPasswordError(
            f"la contraseña debe tener entre {PASSWORD_MIN_LENGTH} y {PASSWORD_MAX_LENGTH}"
            " caracteres"
        )


def generate_opaque_token() -> str:
    """Token aleatorio de 32 bytes (256 bits) en base64url. Se entrega una sola vez."""
    return secrets.token_urlsafe(OPAQUE_TOKEN_BYTES)


def hash_opaque_token(raw: str) -> str:
    """SHA-256 en hex. Alcanza (sin sal ni hash lento) porque el token tiene 256 bits de
    entropía: no se puede adivinar por fuerza bruta, a diferencia de una contraseña."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def secret_matches(raw: str, expected_sha256: str) -> bool:
    """Compara en tiempo constante el hash de `raw` con el esperado."""
    return hmac.compare_digest(hash_opaque_token(raw), expected_sha256)
