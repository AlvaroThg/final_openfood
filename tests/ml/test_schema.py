from __future__ import annotations

import numpy as np
import pytest

from nutriscore import schema


def test_contract_has_20_unique_features() -> None:
    assert len(schema.FEATURE_NAMES) == 20
    assert len(set(schema.FEATURE_NAMES)) == len(schema.FEATURE_NAMES)


def test_leakage_and_redundant_columns_are_never_loaded() -> None:
    forbidden = set(schema.LEAKAGE_RAW_COLUMNS) | set(schema.REDUNDANT_RAW_COLUMNS)
    assert forbidden.isdisjoint(schema.RAW_COLUMNS)


def test_required_api_fields_are_formula_nutrients() -> None:
    assert set(schema.REQUIRED_FEATURES) <= set(schema.FORMULA_FEATURES)
    assert set(schema.REQUIRED_FEATURES) == {
        "energy_100g", "saturated_fat_100g", "sugars_100g", "proteins_100g", "salt_100g"
    }


@pytest.mark.parametrize(
    ("record", "rule"),
    [
        ({"fat_100g": 5, "saturated_fat_100g": 6}, "saturated_fat_gt_fat"),
        ({"fat_100g": 5, "trans_fat_100g": 5.6}, "trans_fat_gt_fat"),
        ({"carbohydrates_100g": 10, "sugars_100g": 12}, "sugars_gt_carbohydrates"),
        ({"fat_100g": 50, "carbohydrates_100g": 40, "proteins_100g": 20}, "macros_sum_gt_100"),
    ],
)
def test_consistency_rule_detects_violation(record: dict[str, float], rule: str) -> None:
    violations = schema.consistency_violations(schema.to_feature_frame([record]))
    assert violations.loc[0, rule]
    assert violations.loc[0].sum() == 1  # solo esa regla


def test_consistency_tolerates_rounding_and_ignores_missing() -> None:
    frame = schema.to_feature_frame([
        {"fat_100g": 5, "saturated_fat_100g": 5.4},  # dentro de la tolerancia de 0,5 g
        {"saturated_fat_100g": 80},  # grasa total faltante: no se puede afirmar violación
        {},
    ])
    assert not schema.consistency_violations(frame).to_numpy().any()


def test_range_violations_flag_only_out_of_range_values() -> None:
    frame = schema.to_feature_frame([
        {"sugars_100g": 101, "energy_100g": 3900, "salt_100g": -1, "proteins_100g": 50},
    ])
    flagged = schema.range_violations(frame).loc[0]
    assert set(flagged[flagged].index) == {"sugars_100g", "energy_100g", "salt_100g"}


def test_to_feature_frame_has_canonical_order_and_types() -> None:
    frame = schema.to_feature_frame([{"sugars_100g": 3, "pnns_groups_1": None, "extra": 1}])
    assert tuple(frame.columns) == schema.FEATURE_NAMES
    assert all(frame[name].dtype == np.float64 for name in schema.NUMERIC_NAMES)
    assert np.isnan(frame.loc[0, "fat_100g"])
    assert frame.loc[0, "pnns_groups_1"] is np.nan or np.isnan(frame.loc[0, "pnns_groups_1"])
