from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nutriscore import schema
from nutriscore.training import (
    ConfigError,
    TrainingConfig,
    TrainingDataError,
    fit,
    train_and_evaluate,
)

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "ml" / "configs"


@pytest.mark.parametrize("path", sorted(CONFIGS_DIR.glob("*.toml")), ids=lambda p: p.stem)
def test_every_versioned_config_is_valid(path: Path) -> None:
    config = TrainingConfig.from_toml(path)
    assert config.name == path.stem


def test_experiment_2_excludes_all_formula_variables() -> None:
    config = TrainingConfig.from_toml(CONFIGS_DIR / "lightgbm_sin_formula.toml")
    assert set(config.features).isdisjoint(schema.FORMULA_FEATURES)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"algorithm": "svm"}, "Algoritmo desconocido"),
        ({"numeric_features": ("sodium_100g",)}, "fuera del contrato"),
        ({"numeric_features": ("fat_100g", "fat_100g")}, "repetidas"),
        ({"numeric_features": (), "categorical_features": ()}, "no tiene features"),
        ({"params": {"random_state": 1}}, "seed"),
    ],
)
def test_invalid_config_fails_fast(
    small_lgbm_config: TrainingConfig, change: dict, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        dataclasses.replace(small_lgbm_config, **change)


def test_fit_rejects_training_data_without_all_classes(
    small_lgbm_config: TrainingConfig, clean_frame: pd.DataFrame
) -> None:
    without_e = clean_frame[clean_frame[schema.TARGET] != "E"]
    with pytest.raises(TrainingDataError, match="E"):
        fit(small_lgbm_config, without_e)


def test_train_and_evaluate_learns_and_keeps_test_out_unless_asked(
    small_lgbm_config: TrainingConfig, splits: dict[str, pd.DataFrame]
) -> None:
    result = train_and_evaluate(
        small_lgbm_config, splits, evaluate_test=False, min_slice_support=50
    )
    assert result.test is None
    assert result.validation["n"] == len(splits["val"])
    assert result.validation["f1_macro"] > 0.6
    assert result.train_rows == len(splits["train"])


def test_training_is_reproducible(
    small_lgbm_config: TrainingConfig, splits: dict[str, pd.DataFrame]
) -> None:
    features = splits["val"][list(schema.FEATURE_NAMES)]
    first = fit(small_lgbm_config, splits["train"]).predict_proba(features)
    second = fit(small_lgbm_config, splits["train"]).predict_proba(features)
    assert np.array_equal(first, second)


@pytest.mark.parametrize("algorithm", ["decision_tree", "random_forest"])
def test_baseline_algorithms_train_on_the_same_pipeline(
    algorithm: str, splits: dict[str, pd.DataFrame]
) -> None:
    config = TrainingConfig(
        name=algorithm,
        algorithm=algorithm,
        seed=1,
        numeric_features=schema.NUMERIC_NAMES,
        categorical_features=schema.CATEGORICAL_NAMES,
        params={"class_weight": "balanced", "max_depth": 6},
        min_category_frequency=10,
    )
    pipeline = fit(config, splits["train"])
    assert list(pipeline.classes_) == list(schema.GRADES)
