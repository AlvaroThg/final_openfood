"""Puertos: lo que los casos de uso necesitan del exterior, sin saber cómo se implementa.

infrastructure/ los implementa con PostgreSQL, argon2 y RS256; las pruebas, con versiones en
memoria (tests/helpers/auth_fakes.py).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from svc_common.tokens import Role, TokenClaims

from ..domain.model import RefreshTokenRecord, RevocationReason, User

Clock = Callable[[], datetime]  # devuelve la hora actual con zona horaria (UTC)


class UserRepository(Protocol):
    def get_by_email(self, email: str) -> User | None: ...

    def get_by_id(self, user_id: UUID) -> User | None: ...

    def add(self, *, email: str, password_hash: str, role: Role) -> User:
        """Crea el usuario. Lanza EmailTakenError si el email ya existe."""
        ...

    def update_password_hash(self, user_id: UUID, password_hash: str) -> None: ...

    def admin_exists(self) -> bool: ...


class RefreshTokenRepository(Protocol):
    def add(
        self, *, user_id: UUID, family_id: UUID, token_hash: str, expires_at: datetime
    ) -> UUID: ...

    def get_for_update(self, token_hash: str) -> RefreshTokenRecord | None:
        """Busca por hash y BLOQUEA la fila hasta el fin de la transacción, para que dos
        refrescos simultáneos con el mismo token no puedan rotarlo dos veces."""
        ...

    def revoke(
        self,
        token_id: UUID,
        *,
        reason: RevocationReason,
        at: datetime,
        replaced_by: UUID | None = None,
    ) -> None: ...

    def revoke_family(self, family_id: UUID, *, reason: RevocationReason, at: datetime) -> int:
        """Revoca todos los tokens aún vigentes de la familia; devuelve cuántos."""
        ...


class UnitOfWork(Protocol):
    """Una transacción. Al salir sin excepción confirma; con excepción, deshace."""

    @property
    def users(self) -> UserRepository: ...

    @property
    def refresh_tokens(self) -> RefreshTokenRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, password_hash: str) -> tuple[bool, str | None]:
        """Devuelve (coincide, hash_nuevo). hash_nuevo no es None cuando el hash guardado usa
        parámetros viejos y conviene reemplazarlo."""
        ...

    def verify_dummy(self, password: str) -> None:
        """Hace el mismo trabajo que verify() contra un hash ficticio. Se usa cuando el email
        no existe, para que el tiempo de respuesta no revele qué emails están registrados."""
        ...


class TokenIssuer(Protocol):
    access_token_ttl_s: int
    service_token_ttl_s: int

    def issue_access_token(self, user: User) -> str: ...

    def issue_service_token(self, client_id: str) -> str: ...

    def verify_access_token(self, token: str) -> TokenClaims:
        """Lanza svc_common.tokens.TokenError (o TokenExpiredError) si no es válido."""
        ...
