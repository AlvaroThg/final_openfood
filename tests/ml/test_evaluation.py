from __future__ import annotations

import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from nutriscore import schema
from nutriscore.evaluation import classification_report, evaluate_pipeline, slice_report


def test_perfect_predictions_give_diagonal_confusion_matrix() -> None:
    y = pd.Series(list(schema.GRADES) * 4)
    report = classification_report(y, y)
    assert report["accuracy"] == 1.0
    assert report["f1_macro"] == 1.0
    matrix = report["confusion_matrix"]["matrix"]
    assert all(matrix[i][j] == (4 if i == j else 0) for i in range(5) for j in range(5))
    assert report["confusion_matrix"]["labels"] == list(schema.GRADES)


def test_class_never_predicted_scores_zero_without_crashing() -> None:
    y_true = pd.Series(["A", "B", "C", "D", "E"])
    y_pred = pd.Series(["A", "B", "C", "D", "D"])
    report = classification_report(y_true, y_pred)
    assert report["per_class"]["E"]["recall"] == 0.0
    assert report["per_class"]["E"]["support"] == 1
    assert report["f1_macro"] < 1.0


def test_slice_report_skips_groups_without_enough_support() -> None:
    y = pd.Series(["A"] * 10 + ["B"] * 3)
    groups = pd.Series(["big"] * 10 + ["tiny"] * 3)
    result = slice_report(y, y, groups, min_support=5)
    assert set(result) == {"big"}
    assert result["big"]["support"] == 10


def test_evaluate_pipeline_includes_slices(
    trained_pipeline: Pipeline, splits: dict[str, pd.DataFrame]
) -> None:
    report = evaluate_pipeline(trained_pipeline, splits["val"], min_slice_support=30)
    assert report["slices"]["column"] == "pnns_groups_1"
    assert "sugary snacks" in report["slices"]["groups"]
    assert sum(c["support"] for c in report["per_class"].values()) == len(splits["val"])
    assert report["accuracy"] == pytest.approx(
        sum(report["confusion_matrix"]["matrix"][i][i] for i in range(5)) / len(splits["val"])
    )
