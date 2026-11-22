"""Implementación de los puertos con PostgreSQL (psycopg 3, SQL explícito y parametrizado).

auth_service se conecta como app_auth: solo puede leer, insertar y actualizar users y
refresh_tokens. Esas tablas no tienen RLS, porque solo este servicio accede a ellas; por eso la
transacción no fija usuario ni rol.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool

from svc_common.db import DbConnection, transaction
from svc_common.tokens import Role

from ..domain.errors import EmailTakenError
from ..domain.model import RefreshTokenRecord, RevocationReason, User

_USER_COLUMNS = "id, email, password_hash, role, is_active"


def _to_user(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        role=Role(row["role"]),
        is_active=row["is_active"],
    )


def _returned(row: dict[str, Any] | None) -> dict[str, Any]:
    """INSERT ... RETURNING siempre devuelve una fila; si no, es un error de programación."""
    if row is None:
        raise RuntimeError("INSERT ... RETURNING no devolvió ninguna fila")
    return row


class PgUserRepository:
    def __init__(self, conn: DbConnection) -> None:
        self._conn = conn

    def get_by_email(self, email: str) -> User | None:
        row = self._conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE email = %s",  # noqa: S608 (columnas fijas)
            (email,),
        ).fetchone()
        return _to_user(row) if row else None

    def get_by_id(self, user_id: UUID) -> User | None:
        row = self._conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s",  # noqa: S608 (columnas fijas)
            (user_id,),
        ).fetchone()
        return _to_user(row) if row else None

    def add(self, *, email: str, password_hash: str, role: Role) -> User:
        try:
            # Savepoint: si el email ya existe, solo se deshace este INSERT.
            with self._conn.transaction():
                row = self._conn.execute(
                    "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s)"  # noqa: S608
                    f" RETURNING {_USER_COLUMNS}",  # columnas fijas, sin entrada del usuario
                    (email, password_hash, str(role)),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise EmailTakenError from None
        return _to_user(_returned(row))

    def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        self._conn.execute(
            "UPDATE users SET password_hash = %s, updated_at = now() WHERE id = %s",
            (password_hash, user_id),
        )

    def admin_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT EXISTS (SELECT 1 FROM users WHERE role = 'admin') AS present"
        ).fetchone()
        return bool(row and row["present"])


class PgRefreshTokenRepository:
    def __init__(self, conn: DbConnection) -> None:
        self._conn = conn

    def add(
        self, *, user_id: UUID, family_id: UUID, token_hash: str, expires_at: datetime
    ) -> UUID:
        row = self._conn.execute(
            "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, family_id, token_hash, expires_at),
        ).fetchone()
        token_id: UUID = _returned(row)["id"]
        return token_id

    def get_for_update(self, token_hash: str) -> RefreshTokenRecord | None:
        row = self._conn.execute(
            "SELECT id, user_id, family_id, expires_at, revoked_at, revoked_reason"
            " FROM refresh_tokens WHERE token_hash = %s FOR UPDATE",
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        reason = row["revoked_reason"]
        return RefreshTokenRecord(
            id=row["id"],
            user_id=row["user_id"],
            family_id=row["family_id"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            revoked_reason=RevocationReason(reason) if reason else None,
        )

    def revoke(
        self,
        token_id: UUID,
        *,
        reason: RevocationReason,
        at: datetime,
        replaced_by: UUID | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE refresh_tokens SET revoked_at = %s, revoked_reason = %s, replaced_by = %s"
            " WHERE id = %s AND revoked_at IS NULL",
            (at, str(reason), replaced_by, token_id),
        )

    def revoke_family(self, family_id: UUID, *, reason: RevocationReason, at: datetime) -> int:
        cursor = self._conn.execute(
            "UPDATE refresh_tokens SET revoked_at = %s, revoked_reason = %s"
            " WHERE family_id = %s AND revoked_at IS NULL",
            (at, str(reason), family_id),
        )
        return cursor.rowcount


class PgUnitOfWork:
    """Una transacción de PostgreSQL con los repositorios ligados a esa conexión."""

    users: PgUserRepository
    refresh_tokens: PgRefreshTokenRepository

    def __init__(self, pool: ConnectionPool[DbConnection]) -> None:
        self._pool = pool

    def __enter__(self) -> Self:
        self._context = transaction(self._pool)
        conn = self._context.__enter__()
        self.users = PgUserRepository(conn)
        self.refresh_tokens = PgRefreshTokenRepository(conn)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._context.__exit__(exc_type, exc, tb)
