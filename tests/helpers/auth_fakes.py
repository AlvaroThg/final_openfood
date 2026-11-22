"""Implementaciones en memoria de los puertos de auth_service, para probar los casos de uso sin
base de datos.

InMemoryUnitOfWork imita la semántica transaccional: si el bloque `with` termina con una
excepción, el estado vuelve a como estaba al entrar (rollback). Así una prueba detecta, por
ejemplo, si la revocación por reuso se lanzara dentro de la transacción y se perdiera.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from services.auth_service.app.application.use_cases import AuthService
from services.auth_service.app.domain.errors import EmailTakenError
from services.auth_service.app.domain.model import RefreshTokenRecord, RevocationReason, User
from services.auth_service.app.infrastructure.jwt_issuer import RsaJwtIssuer
from services.auth_service.app.infrastructure.passwords import Argon2PasswordHasher
from svc_common.tokens import Role
from tests.helpers.keys import rsa_private_pem


@dataclass
class StoredToken:
    record: RefreshTokenRecord
    token_hash: str
    replaced_by: UUID | None = None


@dataclass
class InMemoryStore:
    users: dict[UUID, User] = field(default_factory=dict)
    tokens: dict[UUID, StoredToken] = field(default_factory=dict)

    def token_by_hash(self, token_hash: str) -> StoredToken | None:
        return next((t for t in self.tokens.values() if t.token_hash == token_hash), None)

    def family(self, family_id: UUID) -> list[StoredToken]:
        return [t for t in self.tokens.values() if t.record.family_id == family_id]


class InMemoryUserRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def get_by_email(self, email: str) -> User | None:
        return next((u for u in self._store.users.values() if u.email == email), None)

    def get_by_id(self, user_id: UUID) -> User | None:
        return self._store.users.get(user_id)

    def add(self, *, email: str, password_hash: str, role: Role) -> User:
        if self.get_by_email(email) is not None:
            raise EmailTakenError
        user = User(
            id=uuid.uuid4(), email=email, password_hash=password_hash, role=role, is_active=True
        )
        self._store.users[user.id] = user
        return user

    def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        self._store.users[user_id] = replace(
            self._store.users[user_id], password_hash=password_hash
        )

    def admin_exists(self) -> bool:
        return any(u.role is Role.ADMIN for u in self._store.users.values())


class InMemoryRefreshTokenRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def add(self, *, user_id: UUID, family_id: UUID, token_hash: str, expires_at: datetime) -> UUID:
        record = RefreshTokenRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            family_id=family_id,
            expires_at=expires_at,
            revoked_at=None,
            revoked_reason=None,
        )
        self._store.tokens[record.id] = StoredToken(record=record, token_hash=token_hash)
        return record.id

    def get_for_update(self, token_hash: str) -> RefreshTokenRecord | None:
        stored = self._store.token_by_hash(token_hash)
        return stored.record if stored else None

    def revoke(
        self,
        token_id: UUID,
        *,
        reason: RevocationReason,
        at: datetime,
        replaced_by: UUID | None = None,
    ) -> None:
        stored = self._store.tokens[token_id]
        if stored.record.revoked_at is None:
            stored.record = replace(stored.record, revoked_at=at, revoked_reason=reason)
            stored.replaced_by = replaced_by

    def revoke_family(self, family_id: UUID, *, reason: RevocationReason, at: datetime) -> int:
        count = 0
        for stored in self._store.family(family_id):
            if stored.record.revoked_at is None:
                stored.record = replace(stored.record, revoked_at=at, revoked_reason=reason)
                count += 1
        return count


class InMemoryUnitOfWork:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self.users = InMemoryUserRepository(store)
        self.refresh_tokens = InMemoryRefreshTokenRepository(store)

    def __enter__(self) -> Self:
        self._snapshot = copy.deepcopy((self._store.users, self._store.tokens))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:  # rollback
            self._store.users, self._store.tokens = self._snapshot


class RecordingHasher(Argon2PasswordHasher):
    """argon2id con parámetros mínimos (rápido) que cuenta las verificaciones ficticias."""

    def __init__(self, *, time_cost: int = 1) -> None:
        super().__init__(time_cost=time_cost, memory_cost=1024, parallelism=1)
        self.dummy_calls = 0

    def verify_dummy(self, password: str) -> None:
        self.dummy_calls += 1
        super().verify_dummy(password)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 11, 19, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


SERVICE_SECRET = "s3cr3t-de-servicio-de-prueba-con-entropia"
REFRESH_TTL = timedelta(days=7)


@dataclass
class AuthHarness:
    service: AuthService
    store: InMemoryStore
    hasher: RecordingHasher
    issuer: RsaJwtIssuer
    clock: FakeClock


def make_issuer(*, now: float | None = None) -> RsaJwtIssuer:
    kwargs = {} if now is None else {"now": lambda: now}
    return RsaJwtIssuer(
        rsa_private_pem(), access_token_ttl_s=900, service_token_ttl_s=300, **kwargs
    )


def make_auth() -> AuthHarness:
    from services.auth_service.app.domain.model import hash_opaque_token

    store = InMemoryStore()
    hasher = RecordingHasher()
    issuer = make_issuer()
    clock = FakeClock()
    service = AuthService(
        uow_factory=lambda: InMemoryUnitOfWork(store),
        hasher=hasher,
        tokens=issuer,
        clock=clock,
        refresh_token_ttl=REFRESH_TTL,
        service_clients={"retrain_service": hash_opaque_token(SERVICE_SECRET)},
    )
    return AuthHarness(service=service, store=store, hasher=hasher, issuer=issuer, clock=clock)
