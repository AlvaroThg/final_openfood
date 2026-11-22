"""Puertos de ia_backend. infrastructure/ los implementa; las pruebas usan versiones en memoria."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from svc_common.tokens import Role

from ..domain.model import ModelVersionRecord, PredictionRecord


class Predictor(Protocol):
    """Lo mínimo que se usa del pipeline de scikit-learn."""

    @property
    def classes_(self) -> Any: ...

    def predict_proba(self, features: Any) -> Any: ...


@dataclass(frozen=True)
class LoadedModel:
    version: str
    pipeline: Predictor = field(repr=False)
    metadata: dict[str, Any] = field(repr=False)


class ArtifactStore(Protocol):
    def load(self, version: str, sha256: str) -> LoadedModel:
        """Carga y verifica el artefacto. Lanza ModelLoadError si algo no coincide."""
        ...


class ModelRegistry(Protocol):
    def get(self, version: str) -> ModelVersionRecord | None: ...

    def active(self) -> ModelVersionRecord | None: ...

    def list(self) -> list[ModelVersionRecord]: ...

    def activate(self, version: str, *, actor_user_id: UUID | None, role: Role) -> str | None:
        """En UNA transacción: archiva el activo actual y activa `version` (solo si está
        approved/archived). Devuelve la versión que estaba activa. ActivationConflictError si
        el estado cambió entre la lectura y la escritura."""
        ...


class PredictionRepository(Protocol):
    def add(
        self,
        *,
        user_id: UUID,
        role: Role,
        model_version: str,
        input: dict[str, Any],
        grade: str,
        probabilities: dict[str, float],
        inference_ms: float,
    ) -> PredictionRecord: ...

    def list_for(
        self, *, user_id: UUID, role: Role, limit: int, offset: int, include_all: bool = False
    ) -> list[PredictionRecord]:
        """Historial propio, del más reciente al más viejo. `include_all` solo tiene efecto
        para un admin (ve el de todos)."""
        ...
