from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nutriscore import schema
from nutriscore.preprocessing import build_preprocessor, normalize_category_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Sugary snacks", "sugary snacks"), ("sugary-snacks", "sugary snacks"),
     ("  Fruits_and_vegetables ", "fruits and vegetables"), ("   ", np.nan)],
)
def test_normalize_category_value(raw: str, expected: object) -> None:
    result = normalize_category_value(raw)
    if isinstance(expected, float):
        assert np.isnan(result)  # type: ignore[arg-type]
    else:
        assert result == expected


def test_preprocessor_handles_unseen_categories_and_missing_values(
    clean_frame: pd.DataFrame,
) -> None:
    preprocessor = build_preprocessor(schema.NUMERIC_NAMES, schema.CATEGORICAL_NAMES,
                                      min_category_frequency=10)
    fitted_width = preprocessor.fit_transform(clean_frame[list(schema.FEATURE_NAMES)]).shape[1]

    request = schema.to_feature_frame([{
        "energy_100g": 1000, "saturated_fat_100g": 2, "sugars_100g": 5,
        "proteins_100g": 3, "salt_100g": 0.5,
        "pnns_groups_1": "Comida espacial",  # categoría nunca vista
    }])
    transformed = preprocessor.transform(request)
    assert transformed.shape == (1, fitted_width)
    assert not transformed.isna().to_numpy().any()


def test_output_column_names_are_safe_for_lightgbm(clean_frame: pd.DataFrame) -> None:
    preprocessor = build_preprocessor(schema.NUMERIC_NAMES, schema.CATEGORICAL_NAMES,
                                      min_category_frequency=10)
    out = preprocessor.fit_transform(clean_frame[list(schema.FEATURE_NAMES)])
    assert "pnns_groups_1__sugary_snacks" in out.columns
    assert all(name.replace("_", "").isalnum() for name in out.columns)


def test_preprocessor_adds_missing_indicators(clean_frame: pd.DataFrame) -> None:
    preprocessor = build_preprocessor(schema.NUMERIC_NAMES, (), min_category_frequency=10)
    preprocessor.fit(clean_frame[list(schema.FEATURE_NAMES)])
    names = set(preprocessor.get_feature_names_out())
    assert "missingindicator_fiber_100g" in names
    assert "missingindicator_energy_100g" not in names  # sin faltantes en entrenamiento


def test_same_category_written_differently_maps_to_same_column(
    clean_frame: pd.DataFrame,
) -> None:
    preprocessor = build_preprocessor((), ("pnns_groups_1",), min_category_frequency=10)
    preprocessor.fit(clean_frame[list(schema.FEATURE_NAMES)])
    variants = schema.to_feature_frame([{"pnns_groups_1": "Sugary snacks"},
                                        {"pnns_groups_1": "SUGARY-SNACKS"}])
    out = preprocessor.transform(variants).to_numpy()
    assert np.array_equal(out[0], out[1])
