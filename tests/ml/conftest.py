from __future__ import annotations

import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from nutriscore import schema
from nutriscore.splits import split_frame
from nutriscore.training import TrainingConfig, fit
from tests.helpers.synthetic import make_clean_frame


@pytest.fixture(scope="session")
def clean_frame() -> pd.DataFrame:
    return make_clean_frame(n_rows=3_000, seed=0)


@pytest.fixture(scope="session")
def splits(clean_frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return split_frame(clean_frame)


@pytest.fixture(scope="session")
def small_lgbm_config() -> TrainingConfig:
    """LightGBM pequeño y determinista para que las pruebas sean rápidas."""
    return TrainingConfig(
        name="test_lightgbm",
        algorithm="lightgbm",
        seed=7,
        numeric_features=schema.NUMERIC_NAMES,
        categorical_features=schema.CATEGORICAL_NAMES,
        params={"n_estimators": 60, "num_leaves": 15, "learning_rate": 0.1,
                "class_weight": "balanced", "n_jobs": 1, "deterministic": True,
                "force_row_wise": True, "verbose": -1},
        min_category_frequency=10,
    )


@pytest.fixture(scope="session")
def trained_pipeline(small_lgbm_config: TrainingConfig,
                     splits: dict[str, pd.DataFrame]) -> Pipeline:
    return fit(small_lgbm_config, splits["train"])
