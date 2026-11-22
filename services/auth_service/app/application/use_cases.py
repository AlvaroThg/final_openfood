"""Casos de uso de la autenticación.

Sesión = una FAMILIA de refresh tokens. El login crea la familia; cada /auth/refresh entrega un
token nuevo de la misma familia y revoca el anterior (rotación). Si alguien presenta un token ya
revocado, alguien más lo tiene: se revoca toda la familia y ambos deben volver a iniciar sesión.

Detalle importante: cuando se detecta un reuso, la revocación se CONFIRMA (commit) y recién
después se lanza el error. Si se lanzara dentro de la transacción, el rollback desharía la
revocación justo cuando más importa.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from uuid import UUID

from svc_common.tokens import Role

from ..domain.errors import (
    InitialAdminConflictError,
    InvalidClientError,
    InvalidCredentialsError,
    InvalidEmailError,
    RefreshTokenInvalidError,
    RefreshTokenReusedError,
    UserNotFoundError,
)
from ..domain.model import (
    RefreshTokenRecord,
    RevocationReason,
    User,
    check_password_policy,
    generate_opaque_token,
    hash_opaque_token,
    normalize_email,
    secret_matches,
)
from .ports import Clock, PasswordHasher, TokenIssuer, UnitOfWork

logger = logging.getLogger(__name__)

# Hash de referencia para comparar cuando el client_id no existe (tiempo constante).
_NO_SECRET = "0" * 64


@dataclass(frozen=True)
class IssuedSession:
    user: User
    access_token: str = field(repr=False)
    expires_in: int
    refresh_token: str = field(repr=False)
    refresh_expires_at: datetime


@dataclass(frozen=True)
class IssuedServiceToken:
    access_token: str = field(repr=False)
    expires_in: int


class _RefreshOutcome(Enum):
    ROTATED = auto()
    INVALID = auto()
    REUSED = auto()


class AuthService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        hasher: PasswordHasher,
        tokens: TokenIssuer,
        clock: Clock,
        refresh_token_ttl: timedelta,
        service_clients: Mapping[str, str],
    ) -> None:
        self._uow = uow_factory
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock
        self._refresh_ttl = refresh_token_ttl
        self._service_clients = dict(service_clients)  # client_id -> sha256(secret)

    # ------------------------------------------------------------------ registro y login
    def register(self, email: str, password: str) -> User:
        """normalizar email → validar política → hash (fuera de la transacción, es lento)
        → insertar con rol user. EmailTakenError si ya existe."""
        normalized = normalize_email(email)
        check_password_policy(password)
        password_hash = self._hasher.hash(password)
        with self._uow() as uow:
            user = uow.users.add(email=normalized, password_hash=password_hash, role=Role.USER)
        logger.info("user_registered", extra={"user_id": str(user.id)})
        return user

    def login(self, email: str, password: str) -> IssuedSession:
        """Cualquier fallo → InvalidCredentialsError, con el mismo costo de tiempo:
        email mal formado o inexistente → verificación ficticia;
        contraseña incorrecta o cuenta desactivada → mismo error.
        Si el hash usa parámetros viejos, se reemplaza. Éxito → nueva familia de sesión."""
        try:
            normalized = normalize_email(email)
        except InvalidEmailError:
            self._hasher.verify_dummy(password)
            raise InvalidCredentialsError from None

        with self._uow() as uow:
            user = uow.users.get_by_email(normalized)
        if user is None:
            self._hasher.verify_dummy(password)
            raise InvalidCredentialsError
        matches, new_hash = self._hasher.verify(password, user.password_hash)
        if not matches or not user.is_active:
            raise InvalidCredentialsError

        with self._uow() as uow:
            if new_hash is not None:
                uow.users.update_password_hash(user.id, new_hash)
            session, _ = self._issue_session(uow, user, family_id=uuid.uuid4())
        logger.info("login_ok", extra={"user_id": str(user.id)})
        return session

    # ------------------------------------------------------------------ sesiones
    def refresh(self, raw_token: str | None) -> IssuedSession:
        """En UNA transacción, con la fila del token bloqueada:
        no existe → inválido
        revocado por ROTACIÓN → REUSO (alguien tiene una copia vieja): revocar toda la
            familia, confirmar y recién entonces lanzar el error; se revisa antes que la
            expiración para no perder esa señal
        revocado por logout o por un reuso anterior → inválido (la familia ya está revocada)
        expirado, o usuario inexistente/desactivado → inválido
        vigente → crear el sucesor en la misma familia y revocar el actual como 'rotated'."""
        if not raw_token:
            raise RefreshTokenInvalidError
        now = self._clock()
        outcome = _RefreshOutcome.INVALID
        session: IssuedSession | None = None
        record: RefreshTokenRecord | None = None

        with self._uow() as uow:
            record = uow.refresh_tokens.get_for_update(hash_opaque_token(raw_token))
            if record is None:
                outcome = _RefreshOutcome.INVALID
            elif record.revoked_at is not None:
                if record.revoked_reason is RevocationReason.ROTATED:
                    uow.refresh_tokens.revoke_family(
                        record.family_id, reason=RevocationReason.REUSE_DETECTED, at=now
                    )
                    outcome = _RefreshOutcome.REUSED
            elif record.is_expired(now):
                outcome = _RefreshOutcome.INVALID
            else:
                user = uow.users.get_by_id(record.user_id)
                if user is not None and user.is_active:
                    session, successor_id = self._issue_session(
                        uow, user, family_id=record.family_id
                    )
                    uow.refresh_tokens.revoke(
                        record.id,
                        reason=RevocationReason.ROTATED,
                        at=now,
                        replaced_by=successor_id,
                    )
                    outcome = _RefreshOutcome.ROTATED

        if outcome is _RefreshOutcome.REUSED and record is not None:
            logger.warning(
                "refresh_token_reuse_detected",
                extra={"user_id": str(record.user_id), "family_id": str(record.family_id)},
            )
            raise RefreshTokenReusedError
        if outcome is _RefreshOutcome.INVALID or session is None:
            raise RefreshTokenInvalidError
        return session

    def logout(self, raw_token: str | None) -> None:
        """Idempotente: revoca la familia del token si sigue vigente; si no, no hace nada."""
        if not raw_token:
            return
        now = self._clock()
        with self._uow() as uow:
            record = uow.refresh_tokens.get_for_update(hash_opaque_token(raw_token))
            if record is not None and record.revoked_at is None:
                uow.refresh_tokens.revoke_family(
                    record.family_id, reason=RevocationReason.LOGOUT, at=now
                )
                logger.info("logout", extra={"user_id": str(record.user_id)})

    def current_user(self, access_token: str) -> User:
        """Verifica el access token (TokenError si no es válido) y devuelve el usuario
        actual; UserNotFoundError si ya no existe o fue desactivado."""
        claims = self._tokens.verify_access_token(access_token)
        with self._uow() as uow:
            user = uow.users.get_by_id(UUID(claims.subject))
        if user is None or not user.is_active:
            raise UserNotFoundError
        return user

    # ------------------------------------------------------------------ servicios
    def issue_service_token(self, client_id: str, client_secret: str) -> IssuedServiceToken:
        """Client credentials. La comparación es en tiempo constante y se hace también si el
        client_id no existe, para no revelar qué clientes hay."""
        expected = self._service_clients.get(client_id)
        matches = secret_matches(client_secret, expected or _NO_SECRET)
        if expected is None or not matches:
            logger.warning("service_token_denied", extra={"client_id": client_id})
            raise InvalidClientError
        token = self._tokens.issue_service_token(client_id)
        logger.info("service_token_issued", extra={"client_id": client_id})
        return IssuedServiceToken(access_token=token, expires_in=self._tokens.service_token_ttl_s)

    # ------------------------------------------------------------------ arranque
    def ensure_initial_admin(self, email: str, password: str) -> bool:
        """Crea el primer administrador si todavía no hay ninguno. Devuelve si lo creó.

        Si ya existe algún admin, no hace nada (la variable se puede quitar después del primer
        arranque). Si el email pertenece a un usuario normal, falla: promoverlo en silencio
        daría privilegios a una cuenta que alguien más pudo registrar.
        """
        normalized = normalize_email(email)
        check_password_policy(password)
        with self._uow() as uow:
            if uow.users.admin_exists():
                return False
            if uow.users.get_by_email(normalized) is not None:
                raise InitialAdminConflictError
            user = uow.users.add(
                email=normalized, password_hash=self._hasher.hash(password), role=Role.ADMIN
            )
        logger.info("initial_admin_created", extra={"user_id": str(user.id)})
        return True

    # ------------------------------------------------------------------ interno
    def _issue_session(
        self, uow: UnitOfWork, user: User, *, family_id: UUID
    ) -> tuple[IssuedSession, UUID]:
        """Crea un refresh token en la familia y un access token. Devuelve la sesión y el id
        de la fila nueva (lo necesita la rotación para enlazar el token anterior)."""
        raw = generate_opaque_token()
        expires_at = self._clock() + self._refresh_ttl
        token_id = uow.refresh_tokens.add(
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_opaque_token(raw),
            expires_at=expires_at,
        )
        session = IssuedSession(
            user=user,
            access_token=self._tokens.issue_access_token(user),
            expires_in=self._tokens.access_token_ttl_s,
            refresh_token=raw,
            refresh_expires_at=expires_at,
        )
        return session, token_id
