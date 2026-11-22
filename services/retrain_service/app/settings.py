"""Configuración de retrain_service (API y worker comparten la misma).

DATABASE_URL          obligatoria. postgresql://app_retrain:<clave>@postgres:5432/nutriscore
JWKS_URL              obligatoria para la API (verifica el JWT del admin)
DATASETS_DIR          (/datasets) parquet procesados (volumen)
IMPORT_DIR            (/import) carpeta ./data del host, solo lectura (TSV o parquet de Colab)
MODELS_DIR            (/models) artefactos (volumen; ia_backend lo monta en solo lectura)
CONFIGS_DIR           (/srv/ml/configs) configuraciones de entrenamiento versionadas
TRAINING_CONFIGS      (decision_tree,random_forest,lightgbm) las que cumplen el contrato
                      de la API; lightgbm_sin_formula es solo un experimento del notebook
AUTH_URL              (http://auth_service:8000)  ─┐ solo el worker: pedir el token de
IA_URL                (http://ia_backend:8000)     │ servicio y solicitar la activación
SERVICE_CLIENT_ID     (retrain_service)            │
SERVICE_CLIENT_SECRET                             ─┘
POLL_INTERVAL_S (5) · HEARTBEAT_S (15) · STALE_AFTER_S (600) · LOG_LEVEL (INFO)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from svc_common import config

DEFAULT_CONFIGS = ("decision_tree", "random_forest", "lightgbm")


@dataclass(frozen=True)
class RetrainSettings:
    database_url: str = field(repr=False)
    jwks_url: str | None = None
    datasets_dir: Path = Path("/datasets")
    import_dir: Path = Path("/import")
    models_dir: Path = Path("/models")
    configs_dir: Path = Path("/srv/ml/configs")
    training_configs: tuple[str, ...] = DEFAULT_CONFIGS
    auth_url: str = "http://auth_service:8000"
    ia_url: str = "http://ia_backend:8000"
    service_client_id: str = "retrain_service"
    service_client_secret: str | None = field(default=None, repr=False)
    poll_interval_s: int = 5
    heartbeat_s: int = 15
    stale_after_s: int = 600
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> RetrainSettings:
        return cls(
            database_url=config.require("DATABASE_URL"),
            jwks_url=config.optional("JWKS_URL"),
            datasets_dir=Path(config.optional("DATASETS_DIR") or "/datasets"),
            import_dir=Path(config.optional("IMPORT_DIR") or "/import"),
            models_dir=Path(config.optional("MODELS_DIR") or "/models"),
            configs_dir=Path(config.optional("CONFIGS_DIR") or "/srv/ml/configs"),
            training_configs=config.as_list("TRAINING_CONFIGS") or DEFAULT_CONFIGS,
            auth_url=config.optional("AUTH_URL") or "http://auth_service:8000",
            ia_url=config.optional("IA_URL") or "http://ia_backend:8000",
            service_client_id=config.optional("SERVICE_CLIENT_ID") or "retrain_service",
            service_client_secret=config.optional("SERVICE_CLIENT_SECRET"),
            poll_interval_s=config.as_int("POLL_INTERVAL_S", 5, minimum=1),
            heartbeat_s=config.as_int("HEARTBEAT_S", 15, minimum=1),
            stale_after_s=config.as_int("STALE_AFTER_S", 600, minimum=30),
            log_level=config.optional("LOG_LEVEL") or "INFO",
        )
