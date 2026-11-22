"""Configuración de ia_backend (fail fast al arrancar).

DATABASE_URL        obligatoria. postgresql://app_ia:<clave>@postgres:5432/nutriscore
JWKS_URL            obligatoria. http://auth_service:8000/.well-known/jwks.json (red interna)
MODELS_DIR          (/models) volumen de modelos, montado en solo lectura
INTERNAL_CLIENTS    (retrain_service) clientes de servicio que pueden pedir una activación
PREDICT_PER_USER_PER_MINUTE (30)
CORS_ORIGINS        vacío = sin CORS (el frontend hace de proxy)
LOG_LEVEL           (INFO)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from svc_common import config
from svc_common.config import ConfigError


@dataclass(frozen=True)
class IaSettings:
    database_url: str = field(repr=False)
    jwks_url: str
    models_dir: Path = Path("/models")
    internal_clients: frozenset[str] = frozenset({"retrain_service"})
    predict_per_user_per_minute: int = 30
    cors_origins: tuple[str, ...] = ()
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> IaSettings:
        models_dir = Path(config.optional("MODELS_DIR") or "/models")
        if not models_dir.is_dir():
            raise ConfigError(f"MODELS_DIR no existe o no es una carpeta: {models_dir}")
        return cls(
            database_url=config.require("DATABASE_URL"),
            jwks_url=config.require("JWKS_URL"),
            models_dir=models_dir,
            internal_clients=frozenset(config.as_list("INTERNAL_CLIENTS") or ("retrain_service",)),
            predict_per_user_per_minute=config.as_int("PREDICT_PER_USER_PER_MINUTE", 30, minimum=1),
            cors_origins=config.as_list("CORS_ORIGINS"),
            log_level=config.optional("LOG_LEVEL") or "INFO",
        )
