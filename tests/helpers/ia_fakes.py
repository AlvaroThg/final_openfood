"""Piezas en memoria y tokens para probar ia_backend sin Postgres."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import numpy as np

from nutriscore.lifecycle import ModelStatus
from services.auth_service.app.domain.model import User
from services.ia_backend.app.application.ports import LoadedModel
from services.ia_backend.app.domain.model import (
    ActivationConflictError,
    ModelLoadError,
    ModelVersionRecord,
    PredictionRecord,
)
from services.ia_backend.app.settings import IaSettings
from svc_common.jwks import JwksClient
from svc_common.tokens import Role
from tests.helpers.auth_fakes import make_issuer

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def model_record(
    version: str, status: ModelStatus, sha: str | None = "a" * 64
) -> ModelVersionRecord:
    return ModelVersionRecord(
        version=version,
        status=status,
        algorithm="lightgbm",
        dataset_version="v1",
        artifact_sha256=sha,
        metrics={"validation": {"f1_macro": 0.9}},
        gate={"passed": True},
        quality_policy_version="qp-1",
        created_at=NOW,
        updated_at=NOW,
    )


class FakeRegistry:
    def __init__(self, *records: ModelVersionRecord) -> None:
        self.records = {r.version: r for r in records}
        self.activations: list[tuple[str, UUID | None, Role]] = []
        self.fail_with: Exception | None = None

    def get(self, version: str) -> ModelVersionRecord | None:
        return self.records.get(version)

    def active(self) -> ModelVersionRecord | None:
        return next((r for r in self.records.values() if r.status is ModelStatus.ACTIVE), None)

    def list(self) -> list[ModelVersionRecord]:
        return list(self.records.values())

    def activate(self, version: str, *, actor_user_id: UUID | None, role: Role) -> str | None:
        if self.fail_with is not None:
            raise self.fail_with
        previous = self.active()
        if previous is not None:
            self.records[previous.version] = replace(previous, status=ModelStatus.ARCHIVED)
        target = self.records[version]
        if target.status not in (ModelStatus.APPROVED, ModelStatus.ARCHIVED):
            raise ActivationConflictError(version)
        self.records[version] = replace(target, status=ModelStatus.ACTIVE)
        self.activations.append((version, actor_user_id, role))
        return previous.version if previous else None


class FixedPredictor:
    """Siempre devuelve las mismas probabilidades (A..E)."""

    classes_ = np.array(["A", "B", "C", "D", "E"])

    def __init__(self, probabilities: list[float] | None = None) -> None:
        self._p = probabilities or [0.1, 0.6, 0.2, 0.05, 0.05]

    def predict_proba(self, features: Any) -> Any:
        return np.array([self._p] * len(features))


class FakeStore:
    def __init__(self) -> None:
        self.broken: set[str] = set()
        self.loads: list[str] = []

    def load(self, version: str, sha256: str) -> LoadedModel:
        self.loads.append(version)
        if version in self.broken:
            raise ModelLoadError(f"{version}: hash no coincide")
        return LoadedModel(version=version, pipeline=FixedPredictor(), metadata={"created_at": "x"})


@dataclass
class FakePredictionRepository:
    rows: list[PredictionRecord] = field(default_factory=list)

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
    ) -> PredictionRecord:
        record = PredictionRecord(
            uuid.uuid4(), user_id, model_version, input, grade, probabilities, inference_ms, NOW
        )
        self.rows.append(record)
        return record

    def list_for(
        self, *, user_id: UUID, role: Role, limit: int, offset: int, include_all: bool = False
    ) -> list[PredictionRecord]:
        visible = [
            r for r in self.rows if (include_all and role is Role.ADMIN) or r.user_id == user_id
        ]
        return visible[offset : offset + limit]


# ------------------------------------------------------------------------------ tokens
ISSUER = make_issuer()


def jwks_resolver() -> Any:
    """Resolver real (JwksClient) alimentado con el JWKS del emisor de prueba."""
    return JwksClient(
        "http://auth/.well-known/jwks.json", fetch=lambda _u, _t: ISSUER.jwks()
    ).resolve


def user_token(role: Role = Role.USER, user_id: UUID | None = None) -> tuple[str, UUID]:
    uid = user_id or uuid.uuid4()
    user = User(
        id=uid, email=f"{uid.hex[:6]}@ejemplo.com", password_hash="x", role=role, is_active=True
    )
    return ISSUER.issue_access_token(user), uid


def service_token(client_id: str = "retrain_service") -> str:
    return ISSUER.issue_service_token(client_id)


def ia_settings(**overrides: Any) -> IaSettings:
    base = IaSettings(database_url="postgresql://no-se-usa", jwks_url="http://auth/jwks")
    return replace(base, **overrides)
