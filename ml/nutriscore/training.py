"""Configuración reproducible, construcción y entrenamiento de modelos.

Una configuración (ml/configs/*.toml) define TODO lo necesario para reconstruir un modelo:
algoritmo, hiperparámetros, semilla y features. Se guarda completa en metadata.json.
"""

from __future__ import annotations

import time
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.base import ClassifierMixin
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier

from nutriscore import schema
from nutriscore.evaluation import evaluate_pipeline
from nutriscore.preprocessing import build_preprocessor
from nutriscore.splits import SPLIT_SALT, TRAIN_CUTOFF, VAL_CUTOFF

ESTIMATORS: dict[str, type[ClassifierMixin]] = {
    "decision_tree": DecisionTreeClassifier,
    "random_forest": RandomForestClassifier,
    "lightgbm": LGBMClassifier,
}


class ConfigError(ValueError):
    """Configuración de entrenamiento inválida (se detecta antes de entrenar)."""


class TrainingDataError(ValueError):
    """Datos de entrenamiento no aptos (p. ej. falta alguna clase)."""


@dataclass(frozen=True)
class TrainingConfig:
    name: str
    algorithm: str
    seed: int
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    min_category_frequency: int = 50
    description: str = ""

    def __post_init__(self) -> None:
        if self.algorithm not in ESTIMATORS:
            raise ConfigError(f"Algoritmo desconocido {self.algorithm!r}; "
                              f"opciones: {sorted(ESTIMATORS)}")
        unknown_num = sorted(set(self.numeric_features) - set(schema.NUMERIC_NAMES))
        unknown_cat = sorted(set(self.categorical_features) - set(schema.CATEGORICAL_NAMES))
        if unknown_num or unknown_cat:
            raise ConfigError(f"Features fuera del contrato: {unknown_num + unknown_cat}")
        if len(set(self.features)) != len(self.features):
            raise ConfigError("Hay features repetidas")
        if not self.features:
            raise ConfigError("La configuración no tiene features")
        if "random_state" in self.params:
            raise ConfigError("Use 'seed' (fuente única de aleatoriedad), no random_state")

    @property
    def features(self) -> tuple[str, ...]:
        return self.numeric_features + self.categorical_features

    @classmethod
    def from_toml(cls, path: Path) -> TrainingConfig:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        try:
            features = data["features"]
            return cls(
                name=data["name"],
                description=data.get("description", ""),
                algorithm=data["algorithm"],
                seed=int(data["seed"]),
                numeric_features=tuple(features["numeric"]),
                categorical_features=tuple(features.get("categorical", [])),
                min_category_frequency=int(features.get("min_category_frequency", 50)),
                params=dict(data.get("params", {})),
            )
        except KeyError as exc:
            raise ConfigError(f"{path.name}: falta la clave {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_estimator(config: TrainingConfig) -> ClassifierMixin:
    return ESTIMATORS[config.algorithm](random_state=config.seed, **config.params)


def build_pipeline(config: TrainingConfig) -> Pipeline:
    return Pipeline(steps=[
        ("preprocess", build_preprocessor(config.numeric_features, config.categorical_features,
                                          min_category_frequency=config.min_category_frequency)),
        ("model", build_estimator(config)),
    ])


def fit(config: TrainingConfig, train: pd.DataFrame) -> Pipeline:
    """Entrena. Recibe el DataFrame canónico completo; el pipeline elige sus columnas."""
    present = set(train[schema.TARGET].unique())
    missing = sorted(set(schema.GRADES) - present)
    if missing:
        raise TrainingDataError(f"Faltan clases en el entrenamiento: {missing}")
    pipeline = build_pipeline(config)
    pipeline.fit(train[list(schema.FEATURE_NAMES)], train[schema.TARGET])
    return pipeline


@dataclass(frozen=True)
class TrainingResult:
    pipeline: Pipeline
    validation: dict[str, Any]
    test: dict[str, Any] | None
    train_rows: int
    duration_s: float


def train_and_evaluate(
    config: TrainingConfig,
    splits: Mapping[str, pd.DataFrame],
    *,
    evaluate_test: bool,
    min_slice_support: int = 200,
) -> TrainingResult:
    """Entrena con 'train', evalúa con 'val' (decide la aprobación) y, si se pide, reporta
    'test'. El test nunca se usa para elegir modelos ni hiperparámetros."""
    start = time.perf_counter()
    pipeline = fit(config, splits["train"])
    duration = time.perf_counter() - start
    validation = evaluate_pipeline(pipeline, splits["val"], min_slice_support=min_slice_support)
    test = (evaluate_pipeline(pipeline, splits["test"], min_slice_support=min_slice_support)
            if evaluate_test else None)
    return TrainingResult(pipeline, validation, test, len(splits["train"]), duration)


def build_metadata(
    config: TrainingConfig,
    result: TrainingResult,
    splits: Mapping[str, pd.DataFrame],
    *,
    dataset_version: str,
    dataset_sha256: str,
    quality_policy_version: str,
) -> dict[str, Any]:
    """Contexto completo para reconstruir el entrenamiento (AGENTS.md §5, reproducibilidad).
    artifacts.save_artifact le agrega versión, fecha, hash, tamaño y dependencias."""
    return {
        "algorithm": config.algorithm,
        "config": config.to_dict(),  # incluye hiperparámetros, semilla y features
        "classes": list(schema.GRADES),
        "dataset": {
            "version": dataset_version,
            "sha256": dataset_sha256,
            "rows": {name: len(frame) for name, frame in splits.items()},
        },
        "split": {"salt": SPLIT_SALT, "train_cutoff": TRAIN_CUTOFF, "val_cutoff": VAL_CUTOFF},
        "training": {"duration_s": round(result.duration_s, 3), "train_rows": result.train_rows},
        "quality_policy_version": quality_policy_version,
    }
