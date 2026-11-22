"""Modelo activo en memoria y su activación.

Invariantes:
- Solo se sirve un artefacto verificado (hash antes de deserializar, dependencias, golden).
- La referencia al modelo en uso se reemplaza de una sola vez, DESPUÉS de que la base de datos
  confirmó la activación. Si la carga o la transacción fallan, sigue el modelo anterior.
- Las activaciones se serializan con un lock; las predicciones no esperan (leen la referencia).
- Activar la versión que ya está activa y cargada no hace nada (idempotente).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from uuid import UUID

from nutriscore.lifecycle import ACTIVATABLE, ModelStatus
from svc_common.tokens import Role

from ..domain.model import (
    ModelLoadError,
    ModelNotActivatableError,
    ModelVersionNotFoundError,
)
from .ports import ArtifactStore, LoadedModel, ModelRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivationResult:
    version: str
    changed: bool
    previous: str | None


class ModelManager:
    def __init__(self, *, registry: ModelRegistry, store: ArtifactStore) -> None:
        self._registry = registry
        self._store = store
        self._current: LoadedModel | None = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    @property
    def current(self) -> LoadedModel | None:
        return self._current

    @property
    def load_error(self) -> str | None:
        """Motivo por el que el modelo activo de la base no se pudo cargar (para /health)."""
        return self._load_error

    def load_active(self) -> LoadedModel | None:
        """Al arrancar: carga la versión marcada como activa, si hay una.

        Sin versión activa (instalación nueva) → None, sin error: /predict responde 503 hasta
        que se active una. Con versión activa que no se puede cargar → se registra el motivo y
        /health queda en 503; se puede activar otra versión (rollback) por la API.
        """
        with self._lock:
            record = self._registry.active()
            if record is None or record.artifact_sha256 is None:
                logger.warning("no_active_model")
                return None
            try:
                self._current = self._store.load(record.version, record.artifact_sha256)
                self._load_error = None
            except ModelLoadError as exc:
                self._load_error = str(exc)
                logger.error("active_model_load_failed", extra={"model_version": record.version})
                return None
            logger.info("model_loaded", extra={"model_version": record.version})
            return self._current

    def activate(self, version: str, *, actor_user_id: UUID | None, role: Role) -> ActivationResult:
        """verificar estado → cargar y verificar artefacto → transacción en BD → cambiar
        referencia. Cualquier fallo deja todo como estaba."""
        with self._lock:
            record = self._registry.get(version)
            if record is None:
                raise ModelVersionNotFoundError(version)
            loaded_version = self._current.version if self._current else None

            if record.status is ModelStatus.ACTIVE and loaded_version == version:
                return ActivationResult(version=version, changed=False, previous=version)
            if record.status is not ModelStatus.ACTIVE and record.status not in ACTIVATABLE:
                raise ModelNotActivatableError(f"{version} está en estado {record.status}")
            if record.artifact_sha256 is None:
                raise ModelLoadError(f"{version} no tiene artefacto registrado")

            candidate = self._store.load(version, record.artifact_sha256)
            previous = loaded_version
            if record.status is not ModelStatus.ACTIVE:
                previous = self._registry.activate(version, actor_user_id=actor_user_id, role=role)
            self._current = candidate
            self._load_error = None

        logger.info(
            "model_activated",
            extra={"model_version": version, "previous_version": previous, "by_role": str(role)},
        )
        return ActivationResult(version=version, changed=True, previous=previous)
