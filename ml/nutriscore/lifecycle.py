"""Ciclo de vida de las versiones de modelo: estados y transiciones permitidas.

Fuente única de las reglas de transición. La usan retrain_service (candidate -> evaluating
-> approved/rejected) e ia_backend (approved/archived -> active, active -> archived).
La base de datos refuerza los invariantes que puede expresar (CHECK de estados, un único
modelo activo) y las actualizaciones son condicionales al estado esperado.

    candidate -> evaluating -> approved -> active -> archived
                      |                      ^          |
                      +-> rejected (final)   +----------+  (rollback)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum


class ModelStatus(StrEnum):
    CANDIDATE = "candidate"
    EVALUATING = "evaluating"
    APPROVED = "approved"
    ACTIVE = "active"
    REJECTED = "rejected"
    ARCHIVED = "archived"


ALLOWED_TRANSITIONS: dict[ModelStatus, frozenset[ModelStatus]] = {
    ModelStatus.CANDIDATE: frozenset({ModelStatus.EVALUATING}),
    ModelStatus.EVALUATING: frozenset({ModelStatus.APPROVED, ModelStatus.REJECTED}),
    ModelStatus.APPROVED: frozenset({ModelStatus.ACTIVE}),
    ModelStatus.ACTIVE: frozenset({ModelStatus.ARCHIVED}),
    # Un modelo archivado solo pudo llegar ahí desde 'active' (ya fue aprobado): rollback.
    ModelStatus.ARCHIVED: frozenset({ModelStatus.ACTIVE}),
    ModelStatus.REJECTED: frozenset(),
}

# Estados desde los que ia_backend puede activar una versión.
ACTIVATABLE: frozenset[ModelStatus] = frozenset({ModelStatus.APPROVED, ModelStatus.ARCHIVED})


class InvalidTransitionError(ValueError):
    def __init__(self, current: ModelStatus, target: ModelStatus) -> None:
        super().__init__(f"Transición no permitida: {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: ModelStatus, target: ModelStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: ModelStatus, target: ModelStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


# --- Versionado semántico -------------------------------------------------------------

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
INITIAL_VERSION = "1.0.0"


def parse_version(version: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(version)
    if match is None:
        raise ValueError(f"Versión inválida (se espera MAJOR.MINOR.PATCH): {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def next_minor_version(existing: Iterable[str]) -> str:
    """Un reentrenamiento con el mismo contrato de features incrementa MINOR."""
    versions = [parse_version(v) for v in existing]
    if not versions:
        return INITIAL_VERSION
    major, minor, _ = max(versions)
    return f"{major}.{minor + 1}.0"
