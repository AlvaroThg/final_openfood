"""Raíz de composición: arma las piezas concretas a partir de la configuración.

Es el único lugar que conoce a la vez application/ e infrastructure/. Las pruebas construyen un
Container con piezas en memoria (Container.create) y reutilizan el resto de la aplicación.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from svc_common.db import create_pool, transaction
from svc_common.rate_limit import SlidingWindowRateLimiter

from .application.ports import PasswordHasher
from .application.use_cases import AuthService
from .infrastructure.jwt_issuer import RsaJwtIssuer
from .infrastructure.passwords import Argon2PasswordHasher
from .infrastructure.pg_repositories import PgUnitOfWork
from .settings import AuthSettings

logger = logging.getLogger(__name__)


def _noop() -> None:
    return None


@dataclass
class Container:
    settings: AuthSettings
    auth: AuthService
    issuer: RsaJwtIssuer
    health_check: Callable[[], None]
    login_email_limiter: SlidingWindowRateLimiter
    login_ip_limiter: SlidingWindowRateLimiter
    register_ip_limiter: SlidingWindowRateLimiter
    startup: Callable[[], None] = _noop
    shutdown: Callable[[], None] = _noop

    @classmethod
    def create(
        cls,
        settings: AuthSettings,
        *,
        auth: AuthService,
        issuer: RsaJwtIssuer,
        health_check: Callable[[], None],
        startup: Callable[[], None] = _noop,
        shutdown: Callable[[], None] = _noop,
    ) -> Container:
        return cls(
            settings=settings,
            auth=auth,
            issuer=issuer,
            health_check=health_check,
            login_email_limiter=SlidingWindowRateLimiter(settings.login_per_email_per_minute, 60),
            login_ip_limiter=SlidingWindowRateLimiter(settings.login_per_ip_per_minute, 60),
            register_ip_limiter=SlidingWindowRateLimiter(settings.register_per_ip_per_hour, 3600),
            startup=startup,
            shutdown=shutdown,
        )


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_container(settings: AuthSettings, *, hasher: PasswordHasher | None = None) -> Container:
    """Arma el servicio real: pool de Postgres (sin abrir), firmador RS256, argon2id."""
    pool = create_pool(settings.database_url, name="auth_service")
    issuer = RsaJwtIssuer(
        settings.private_key_pem,
        access_token_ttl_s=settings.access_token_ttl_s,
        service_token_ttl_s=settings.service_token_ttl_s,
    )
    auth = AuthService(
        uow_factory=lambda: PgUnitOfWork(pool),
        hasher=hasher or Argon2PasswordHasher(),
        tokens=issuer,
        clock=utc_now,
        refresh_token_ttl=timedelta(seconds=settings.refresh_token_ttl_s),
        service_clients=settings.service_clients,
    )

    def health_check() -> None:
        with transaction(pool) as conn:
            conn.execute("SELECT 1")

    def startup() -> None:
        # Si Postgres no responde en 30 s, el arranque falla y Docker reinicia el contenedor.
        pool.open(wait=True, timeout=30)
        if settings.admin_email and settings.admin_password:
            created = auth.ensure_initial_admin(settings.admin_email, settings.admin_password)
            # Ojo: "created" es un atributo reservado de LogRecord; no usarlo como clave.
            logger.info("initial_admin_checked", extra={"admin_created": created})
        else:
            logger.info("initial_admin_not_configured")

    return Container.create(
        settings,
        auth=auth,
        issuer=issuer,
        health_check=health_check,
        startup=startup,
        shutdown=pool.close,
    )
