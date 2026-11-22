"""Casos de uso de auth_service con puertos en memoria (rápidos, sin base de datos).

El comportamiento con PostgreSQL real se prueba en test_api_integration.py.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from services.auth_service.app.domain.errors import (
    EmailTakenError,
    InitialAdminConflictError,
    InvalidClientError,
    InvalidCredentialsError,
    InvalidEmailError,
    RefreshTokenInvalidError,
    RefreshTokenReusedError,
    UserNotFoundError,
    WeakPasswordError,
)
from services.auth_service.app.domain.model import RevocationReason, hash_opaque_token
from services.auth_service.app.infrastructure.passwords import Argon2PasswordHasher
from svc_common.tokens import INTERNAL_AUDIENCE, Role, TokenError, decode_token
from tests.helpers.auth_fakes import SERVICE_SECRET, AuthHarness, make_auth

PASSWORD = "una-clave-larga-y-segura"


@pytest.fixture
def h() -> AuthHarness:
    return make_auth()


def register_and_login(h: AuthHarness, email: str = "ana@ejemplo.com"):
    h.service.register(email, PASSWORD)
    return h.service.login(email, PASSWORD)


# ----------------------------------------------------------------------------- registro
def test_register_normalizes_email_and_hashes_password(h: AuthHarness) -> None:
    user = h.service.register("  Ana@Ejemplo.com ", PASSWORD)
    assert user.email == "ana@ejemplo.com"
    assert user.role is Role.USER
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


def test_register_duplicate_email_case_insensitive(h: AuthHarness) -> None:
    h.service.register("ana@ejemplo.com", PASSWORD)
    with pytest.raises(EmailTakenError):
        h.service.register("ANA@ejemplo.com", PASSWORD)


def test_register_validates_input(h: AuthHarness) -> None:
    with pytest.raises(WeakPasswordError):
        h.service.register("ana@ejemplo.com", "corta")
    with pytest.raises(InvalidEmailError):
        h.service.register("no-es-email", PASSWORD)
    assert h.store.users == {}


# -------------------------------------------------------------------------------- login
def test_login_issues_verifiable_access_token_and_hashed_refresh(h: AuthHarness) -> None:
    session = register_and_login(h)
    claims = h.issuer.verify_access_token(session.access_token)
    assert claims.subject == str(session.user.id)
    assert claims.role is Role.USER
    assert session.expires_in == 900

    (stored,) = h.store.tokens.values()
    assert stored.token_hash == hash_opaque_token(session.refresh_token)
    assert stored.token_hash != session.refresh_token
    assert session.refresh_expires_at == h.clock.now + h.service._refresh_ttl


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("ana@ejemplo.com", "clave-incorrecta-123"),
        ("nadie@ejemplo.com", PASSWORD),
        ("no-es-email", PASSWORD),
    ],
)
def test_login_failures_are_indistinguishable(h: AuthHarness, email: str, password: str) -> None:
    h.service.register("ana@ejemplo.com", PASSWORD)
    with pytest.raises(InvalidCredentialsError):
        h.service.login(email, password)


def test_unknown_email_still_spends_a_hash_verification(h: AuthHarness) -> None:
    with pytest.raises(InvalidCredentialsError):
        h.service.login("nadie@ejemplo.com", PASSWORD)
    assert h.hasher.dummy_calls == 1


def test_inactive_user_cannot_login(h: AuthHarness) -> None:
    user = h.service.register("ana@ejemplo.com", PASSWORD)
    h.store.users[user.id] = replace(user, is_active=False)
    with pytest.raises(InvalidCredentialsError):
        h.service.login("ana@ejemplo.com", PASSWORD)


def test_login_upgrades_outdated_hash(h: AuthHarness) -> None:
    user = h.service.register("ana@ejemplo.com", PASSWORD)
    old_hash = Argon2PasswordHasher(time_cost=2, memory_cost=1024, parallelism=1).hash(PASSWORD)
    h.store.users[user.id] = replace(user, password_hash=old_hash)
    h.service.login("ana@ejemplo.com", PASSWORD)
    assert h.store.users[user.id].password_hash != old_hash
    assert h.store.users[user.id].password_hash.startswith("$argon2id$")


# ----------------------------------------------------------------------------- refresh
def test_refresh_rotates_within_the_same_family(h: AuthHarness) -> None:
    first = register_and_login(h)
    second = h.service.refresh(first.refresh_token)

    assert second.refresh_token != first.refresh_token
    old = h.store.token_by_hash(hash_opaque_token(first.refresh_token))
    new = h.store.token_by_hash(hash_opaque_token(second.refresh_token))
    assert old is not None and new is not None
    assert old.record.revoked_reason is RevocationReason.ROTATED
    assert old.replaced_by == new.record.id
    assert new.record.revoked_at is None
    assert new.record.family_id == old.record.family_id


def test_reuse_of_rotated_token_revokes_whole_family(h: AuthHarness) -> None:
    first = register_and_login(h)
    second = h.service.refresh(first.refresh_token)

    with pytest.raises(RefreshTokenReusedError):
        h.service.refresh(first.refresh_token)  # el "atacante" usa la copia vieja

    # La revocación sobrevivió al error (se confirmó antes de lanzarlo).
    family = h.store.family(h.store.tokens[next(iter(h.store.tokens))].record.family_id)
    assert all(t.record.revoked_at is not None for t in family)
    # El token legítimo más reciente tampoco sirve: hay que volver a iniciar sesión.
    with pytest.raises(RefreshTokenInvalidError):
        h.service.refresh(second.refresh_token)


def test_reuse_detection_does_not_affect_other_sessions(h: AuthHarness) -> None:
    laptop = register_and_login(h)
    phone = h.service.login("ana@ejemplo.com", PASSWORD)
    h.service.refresh(laptop.refresh_token)
    with pytest.raises(RefreshTokenReusedError):
        h.service.refresh(laptop.refresh_token)
    assert h.service.refresh(phone.refresh_token).user.email == "ana@ejemplo.com"


def test_expired_refresh_token_is_invalid(h: AuthHarness) -> None:
    session = register_and_login(h)
    h.clock.advance(days=7, seconds=1)
    with pytest.raises(RefreshTokenInvalidError):
        h.service.refresh(session.refresh_token)


@pytest.mark.parametrize("token", [None, "", "token-que-no-existe"])
def test_missing_or_unknown_refresh_token(h: AuthHarness, token: str | None) -> None:
    with pytest.raises(RefreshTokenInvalidError):
        h.service.refresh(token)


def test_refresh_for_deactivated_user_is_invalid(h: AuthHarness) -> None:
    session = register_and_login(h)
    user = session.user
    h.store.users[user.id] = replace(user, is_active=False)
    with pytest.raises(RefreshTokenInvalidError):
        h.service.refresh(session.refresh_token)


# ------------------------------------------------------------------------------ logout
def test_logout_revokes_family_and_is_idempotent(h: AuthHarness) -> None:
    session = register_and_login(h)
    h.service.logout(session.refresh_token)
    h.service.logout(session.refresh_token)
    h.service.logout(None)
    (stored,) = h.store.tokens.values()
    assert stored.record.revoked_reason is RevocationReason.LOGOUT
    with pytest.raises(RefreshTokenInvalidError):
        h.service.refresh(session.refresh_token)


# ------------------------------------------------------------------------- current_user
def test_current_user(h: AuthHarness) -> None:
    session = register_and_login(h)
    assert h.service.current_user(session.access_token).id == session.user.id


def test_current_user_rejects_deactivated_and_invalid(h: AuthHarness) -> None:
    session = register_and_login(h)
    with pytest.raises(TokenError):
        h.service.current_user("basura")
    h.store.users[session.user.id] = replace(session.user, is_active=False)
    with pytest.raises(UserNotFoundError):
        h.service.current_user(session.access_token)


# -------------------------------------------------------------------- token de servicio
def test_service_token_for_valid_client(h: AuthHarness) -> None:
    issued = h.service.issue_service_token("retrain_service", SERVICE_SECRET)
    claims = decode_token(
        issued.access_token,
        resolve_key=lambda _kid: h.issuer._public_key,
        audience=INTERNAL_AUDIENCE,
    )
    assert claims.subject == "retrain_service"
    assert claims.role is Role.SERVICE
    assert issued.expires_in == 300


@pytest.mark.parametrize(
    ("client_id", "secret"),
    [("retrain_service", "incorrecto"), ("desconocido", SERVICE_SECRET)],
)
def test_service_token_rejects_bad_credentials(h: AuthHarness, client_id: str, secret: str) -> None:
    with pytest.raises(InvalidClientError):
        h.service.issue_service_token(client_id, secret)


# ------------------------------------------------------------------- administrador inicial
def test_initial_admin_is_created_once(h: AuthHarness) -> None:
    assert h.service.ensure_initial_admin("admin@ejemplo.com", PASSWORD) is True
    assert h.service.ensure_initial_admin("otro@ejemplo.com", PASSWORD) is False
    admins = [u for u in h.store.users.values() if u.role is Role.ADMIN]
    assert [a.email for a in admins] == ["admin@ejemplo.com"]


def test_initial_admin_never_promotes_existing_user(h: AuthHarness) -> None:
    h.service.register("admin@ejemplo.com", PASSWORD)
    with pytest.raises(InitialAdminConflictError):
        h.service.ensure_initial_admin("admin@ejemplo.com", PASSWORD)
