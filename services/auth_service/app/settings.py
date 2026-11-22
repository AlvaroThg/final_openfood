"""Configuración de auth_service, leída del entorno una sola vez al arrancar (fail fast).

Variables:
    DATABASE_URL           obligatoria. postgresql://app_auth:<clave>@postgres:5432/nutriscore
    JWT_PRIVATE_KEY_PATH   obligatoria. PEM RSA >= 2048 bits (scripts/gen_keys.sh)
    SERVICE_CLIENTS        "client_id:sha256hex[,otro:sha256hex]"; sha256 del client_secret
    ADMIN_EMAIL / ADMIN_PASSWORD   primer administrador (ambas o ninguna)
    ACCESS_TOKEN_TTL_S (900) · REFRESH_TOKEN_TTL_S (604800) · SERVICE_TOKEN_TTL_S (300)
    COOKIE_SECURE (true)   false solo si el navegador no acepta cookies Secure en http://localhost
    CORS_ORIGINS           lista separada por comas; vacío = sin CORS (el frontend hace de proxy)
    TRUSTED_PROXY_HOPS (0) proxies de confianza delante (para leer la IP de X-Forwarded-For)
    LOG_LEVEL (INFO)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from svc_common import config
from svc_common.config import ConfigError

_CLIENT_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AuthSettings:
    database_url: str = field(repr=False)
    private_key_pem: bytes = field(repr=False)
    service_clients: Mapping[str, str] = field(default_factory=dict, repr=False)
    admin_email: str | None = None
    admin_password: str | None = field(default=None, repr=False)
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 7 * 24 * 3600
    service_token_ttl_s: int = 300
    cookie_secure: bool = True
    cors_origins: tuple[str, ...] = ()
    trusted_proxy_hops: int = 0
    log_level: str = "INFO"
    # Rate limit (ver docs/seguridad.md): login por email y por IP, registro por IP.
    login_per_email_per_minute: int = 5
    login_per_ip_per_minute: int = 20
    register_per_ip_per_hour: int = 10

    @classmethod
    def from_env(cls) -> AuthSettings:
        key_path = Path(config.require("JWT_PRIVATE_KEY_PATH"))
        try:
            private_key_pem = key_path.read_bytes()
        except OSError as exc:
            raise ConfigError(f"No se pudo leer JWT_PRIVATE_KEY_PATH ({key_path})") from exc

        admin_email = config.optional("ADMIN_EMAIL")
        admin_password = config.optional("ADMIN_PASSWORD")
        if (admin_email is None) != (admin_password is None):
            raise ConfigError("ADMIN_EMAIL y ADMIN_PASSWORD van juntas: define ambas o ninguna")

        return cls(
            database_url=config.require("DATABASE_URL"),
            private_key_pem=private_key_pem,
            service_clients=parse_service_clients(config.optional("SERVICE_CLIENTS") or ""),
            admin_email=admin_email,
            admin_password=admin_password,
            access_token_ttl_s=config.as_int("ACCESS_TOKEN_TTL_S", 900, minimum=60),
            refresh_token_ttl_s=config.as_int("REFRESH_TOKEN_TTL_S", 7 * 24 * 3600, minimum=300),
            service_token_ttl_s=config.as_int("SERVICE_TOKEN_TTL_S", 300, minimum=30),
            cookie_secure=config.as_bool("COOKIE_SECURE", True),
            cors_origins=config.as_list("CORS_ORIGINS"),
            trusted_proxy_hops=config.as_int("TRUSTED_PROXY_HOPS", 0),
            log_level=config.optional("LOG_LEVEL") or "INFO",
        )


def parse_service_clients(raw: str) -> dict[str, str]:
    """'retrain_service:<sha256 hex>,otro:<hex>' → {client_id: sha256}. Formato estricto."""
    clients: dict[str, str] = {}
    for entry in (item.strip() for item in raw.split(",")):
        if not entry:
            continue
        client_id, sep, digest = entry.partition(":")
        if not sep or not _CLIENT_ID.fullmatch(client_id) or not _SHA256_HEX.fullmatch(digest):
            raise ConfigError(
                "SERVICE_CLIENTS debe tener el formato client_id:sha256hex (sha256 del secreto)"
            )
        if client_id in clients:
            raise ConfigError(f"SERVICE_CLIENTS repite el cliente {client_id}")
        clients[client_id] = digest
    return clients
