from __future__ import annotations

from typing import Any

from nutriscore import schema
from nutriscore.quality import QP_1, evaluate_gate

GOOD_SIZE = 5 * 1024 * 1024


def make_report(
    f1: float = 0.9,
    recall: float = 0.9,
    precision: float = 0.9,
    slices: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "f1_macro": f1,
        "per_class": {
            g: {"precision": precision, "recall": recall, "f1": f1, "support": 100}
            for g in schema.GRADES
        },
        "slices": {
            "groups": {
                name: {"f1_macro": value, "support": 500} for name, value in (slices or {}).items()
            }
        },
    }


def failed(result) -> set[str]:
    return {check.name for check in result.failed_blocking}


def test_good_model_passes_on_first_deployment() -> None:
    result = evaluate_gate(make_report(), active=None, artifact_bytes=GOOD_SIZE, parity_ok=True)
    assert result.passed
    assert result.policy_version == QP_1.version
    assert "f1_macro_no_regression" not in {c.name for c in result.checks}


def test_high_global_f1_is_not_enough_if_one_class_is_neglected() -> None:
    report = make_report(f1=0.95)
    report["per_class"]["B"]["recall"] = 0.40
    result = evaluate_gate(report, active=None, artifact_bytes=GOOD_SIZE, parity_ok=True)
    assert not result.passed
    assert failed(result) == {"recall_B"}


def test_regression_against_active_model_is_rejected() -> None:
    result = evaluate_gate(
        make_report(f1=0.88), active=make_report(f1=0.90), artifact_bytes=GOOD_SIZE, parity_ok=True
    )
    assert failed(result) == {"f1_macro_no_regression"}


def test_small_drop_within_tolerance_is_accepted() -> None:
    result = evaluate_gate(
        make_report(f1=0.895), active=make_report(f1=0.90), artifact_bytes=GOOD_SIZE, parity_ok=True
    )
    assert result.passed


def test_heavy_artifact_and_parity_failure_block_approval() -> None:
    result = evaluate_gate(
        make_report(), active=None, artifact_bytes=QP_1.max_artifact_bytes + 1, parity_ok=False
    )
    assert failed(result) == {"artifact_size", "reload_parity"}


def test_weak_food_group_warns_but_does_not_block() -> None:
    result = evaluate_gate(
        make_report(slices={"beverages": 0.5, "cheese": 0.95}),
        active=None,
        artifact_bytes=GOOD_SIZE,
        parity_ok=True,
    )
    assert result.passed
    assert [c.name for c in result.warnings] == ["slice_beverages"]


def test_gate_result_is_serializable() -> None:
    data = evaluate_gate(
        make_report(), active=None, artifact_bytes=GOOD_SIZE, parity_ok=True
    ).to_dict()
    assert data["passed"] is True
    assert {"name", "passed", "blocking", "value", "threshold", "detail"} <= set(data["checks"][0])
