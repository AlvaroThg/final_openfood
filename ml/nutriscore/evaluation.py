"""Evaluación: métricas globales, por clase, matriz de confusión y rendimiento por grupo.

Los reportes son diccionarios serializables a JSON: se guardan en metrics.json, en la
tabla model_versions y los muestra la página de métricas del frontend.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.pipeline import Pipeline

from nutriscore import schema
from nutriscore.preprocessing import UNKNOWN_CATEGORY, normalize_categories

SLICE_COLUMN = "pnns_groups_1"


def classification_report(y_true: pd.Series, y_pred: pd.Series) -> dict[str, Any]:
    labels = list(schema.GRADES)
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    macro_p, macro_r, macro_f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, average="macro", zero_division=0
    )
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "f1_macro": float(macro_f1),
        "precision_macro": float(macro_p),
        "recall_macro": float(macro_r),
        "per_class": {
            label: {
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "f1": float(f1[i]),
                "support": int(support[i]),
            }
            for i, label in enumerate(labels)
        },
        "confusion_matrix": {
            "labels": labels,  # filas = real, columnas = predicho
            "matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        },
    }


def slice_report(
    y_true: pd.Series, y_pred: pd.Series, groups: pd.Series, *, min_support: int
) -> dict[str, dict[str, float | int]]:
    """F1 macro por grupo de alimento. Grupos con menos de `min_support` filas se omiten
    porque su métrica no es estadísticamente estable."""
    result: dict[str, dict[str, float | int]] = {}
    frame = pd.DataFrame({"y": y_true.to_numpy(), "p": y_pred.to_numpy(),
                          "g": groups.to_numpy()})
    for group, part in frame.groupby("g", sort=True):
        if len(part) < min_support:
            continue
        result[str(group)] = {
            "support": int(len(part)),
            "f1_macro": float(f1_score(part["y"], part["p"], labels=list(schema.GRADES),
                                       average="macro", zero_division=0)),
            "accuracy": float(accuracy_score(part["y"], part["p"])),
        }
    return result


def evaluate_pipeline(
    pipeline: Pipeline, frame: pd.DataFrame, *, min_slice_support: int = 200
) -> dict[str, Any]:
    """Evalúa un pipeline entrenado sobre un DataFrame canónico (con columna objetivo)."""
    y_true = frame[schema.TARGET]
    y_pred = pd.Series(pipeline.predict(frame[list(schema.FEATURE_NAMES)]), index=frame.index)
    report = classification_report(y_true, y_pred)
    groups = normalize_categories(frame[[SLICE_COLUMN]])[SLICE_COLUMN].fillna(UNKNOWN_CATEGORY)
    report["slices"] = {
        "column": SLICE_COLUMN,
        "min_support": min_slice_support,
        "groups": slice_report(y_true, y_pred, groups, min_support=min_slice_support),
    }
    return report
