"""Contrato HTTP de auth_service (Pydantic). El frontend genera sus tipos desde el OpenAPI.

`extra="forbid"`: un campo desconocido es un error 422, no se ignora en silencio (por ejemplo,
un "role": "admin" en el registro no pasa desapercibido).
Los límites de longitud vienen del dominio, para que haya una sola fuente.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..domain.model import EMAIL_MAX_LENGTH, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(_Strict):
    email: str = Field(min_length=3, max_length=EMAIL_MAX_LENGTH, examples=["ana@ejemplo.com"])
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class LoginRequest(_Strict):
    email: str = Field(min_length=1, max_length=EMAIL_MAX_LENGTH, examples=["ana@ejemplo.com"])
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class ServiceTokenRequest(_Strict):
    client_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    client_secret: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (tipo de token OAuth, no un secreto)
    expires_in: int = Field(description="Segundos de validez del access token")


class UserResponse(BaseModel):
    id: UUID
    email: str
    role: Literal["user", "admin"]


class Jwk(BaseModel):
    kty: Literal["RSA"]
    kid: str
    alg: Literal["RS256"]
    use: Literal["sig"]
    n: str
    e: str


class JwksResponse(BaseModel):
    keys: list[Jwk]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None


class ErrorResponse(BaseModel):
    error: ErrorBody
