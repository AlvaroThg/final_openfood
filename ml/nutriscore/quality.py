"""Política de aprobación de modelos (centralizada y versionada).

Un modelo se aprueba solo si cumple TODOS los criterios bloqueantes. Nunca por una única
métrica global. La evaluación usa el conjunto de VALIDACIÓN: el de test queda congelado y
solo se reporta (no se usa para decidir ni ajustar).

Cambiar un umbral = nueva versión de política (qp-2, ...) + justificación en
docs/ml-lifecycle.md. Cada modelo registra con qué versión de política fue evaluado.

Umbrales de qp-1: PROVISIONALES hasta calibrarlos con la línea base del notebook
(árbol de decisión). Ver docs/ml-lifecycle.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    version: str
    min_f1_macro: float  # suelo absoluto
    max_f1_regression: float  # caída máxima permitida frente al modelo activo
    min_class_recall: float  # ninguna clase puede quedar "abandonada"
    min_class_precision: float
    max_artifact_bytes: int  # el modelo debe caber en un contenedor modesto
    slice_tolerance: float  # advertencia de sesgo: F1 de un grupo < suelo - tolerancia
    min_slice_support: int


QP_1 = QualityPolicy(
    version="qp-1",
    min_f1_macro=0.80,
    max_f1_regression=0.01,
    min_class_recall=0.70,
    min_class_precision=0.70,
    max_artifact_bytes=20 * 1024 * 1024,
    slice_tolerance=0.10,
    min_slice_support=200,
)
CURRENT_POLICY = QP_1


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    blocking: bool
    value: float | int | None
    threshold: float | int | None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GateResult:
    policy_version: str
    passed: bool
    checks: tuple[Check, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"policy_version": self.policy_version, "passed": self.passed,
                "checks": [asdict(check) for check in self.checks]}

    @property
    def failed_blocking(self) -> list[Check]:
        return [c for c in self.checks if c.blocking and not c.passed]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.blocking and not c.passed]


def evaluate_gate(
    candidate: dict[str, Any],
    *,
    active: dict[str, Any] | None,
    artifact_bytes: int,
    parity_ok: bool,
    policy: QualityPolicy = CURRENT_POLICY,
) -> GateResult:
    """Aplica la política al reporte de VALIDACIÓN del candidato (evaluation.evaluate_pipeline).

    `active` es el reporte de validación del modelo activo sobre el mismo conjunto, o None si
    todavía no hay modelo activo (primer despliegue: solo aplican los umbrales absolutos).
    """
    f1 = candidate["f1_macro"]
    checks: list[Check] = [
        Check("f1_macro_min", f1 >= policy.min_f1_macro, True, f1, policy.min_f1_macro),
    ]
    if active is not None:
        floor = active["f1_macro"] - policy.max_f1_regression
        checks.append(Check("f1_macro_no_regression", f1 >= floor, True, f1, round(floor, 6),
                            f"F1 del modelo activo: {active['f1_macro']:.4f}"))

    for label, metrics in candidate["per_class"].items():
        checks.append(Check(f"recall_{label}", metrics["recall"] >= policy.min_class_recall,
                            True, metrics["recall"], policy.min_class_recall))
        checks.append(Check(f"precision_{label}",
                            metrics["precision"] >= policy.min_class_precision, True,
                            metrics["precision"], policy.min_class_precision))

    checks.append(Check("artifact_size", artifact_bytes <= policy.max_artifact_bytes, True,
                        artifact_bytes, policy.max_artifact_bytes, "bytes"))
    checks.append(Check("reload_parity", parity_ok, True, int(parity_ok), 1,
                        "predicciones idénticas tras guardar y recargar"))

    slice_floor = policy.min_f1_macro - policy.slice_tolerance
    for group, metrics in candidate.get("slices", {}).get("groups", {}).items():
        checks.append(Check(f"slice_{group}", metrics["f1_macro"] >= slice_floor, False,
                            metrics["f1_macro"], round(slice_floor, 6),
                            f"advertencia de sesgo; soporte={metrics['support']}"))

    passed = all(c.passed for c in checks if c.blocking)
    return GateResult(policy.version, passed, tuple(checks))
