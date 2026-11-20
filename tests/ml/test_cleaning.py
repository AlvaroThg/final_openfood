from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from nutriscore import schema
from nutriscore.cleaning import (
    CleaningReport,
    DatasetSchemaError,
    clean_chunk,
    load_clean_dataset,
    validate_clean_frame,
)
from tests.helpers.synthetic import make_clean_frame, to_raw_frame


@pytest.fixture
def raw() -> pd.DataFrame:
    frame = to_raw_frame(make_clean_frame(n_rows=40, seed=1))
    frame.loc[0, schema.RAW_TARGET] = None  # sin objetivo
    frame.loc[1, schema.RAW_TARGET] = "x"  # objetivo inválido
    frame.loc[2, "code"] = " "  # sin código
    frame.loc[3, "fiber_100g"] = "150"  # fuera de rango -> NaN, la fila se conserva
    frame.loc[4, "salt_100g"] = None  # obligatorio faltante -> descartada
    frame.loc[5, "fat_100g"] = "1"  # grasas saturadas > grasas -> descartada
    frame.loc[5, "saturated-fat_100g"] = "9"
    frame.loc[6, "sugars_100g"] = "abc"  # texto no numérico -> NaN -> obligatorio faltante
    return frame


def test_clean_chunk_applies_every_rule_and_reports_it(raw: pd.DataFrame) -> None:
    report = CleaningReport()
    clean = clean_chunk(raw, report)

    assert tuple(clean.columns) == schema.CANONICAL_COLUMNS
    assert report.rows_read == 40
    assert report.rows_without_target == 1
    assert report.rows_invalid_target == 1
    assert report.rows_without_code == 1
    assert report.rows_missing_required == 2
    assert report.rows_inconsistent["saturated_fat_gt_fat"] == 1
    assert report.values_out_of_range["fiber_100g"] == 1
    assert len(clean) == 40 - 6

    kept = clean.set_index(schema.ID_COLUMN)
    assert pd.isna(kept.loc[raw.loc[3, "code"], "fiber_100g"])
    assert set(clean[schema.TARGET]) <= set(schema.GRADES)  # minúsculas -> mayúsculas


def test_load_clean_dataset_reads_tsv_in_chunks_and_deduplicates(tmp_path: Path) -> None:
    raw = to_raw_frame(make_clean_frame(n_rows=200, seed=2))
    duplicate = raw.iloc[[0]].copy()
    duplicate[schema.RAW_TARGET] = "e"
    pd.concat([raw, duplicate]).to_csv(tmp_path / "off.tsv", sep="\t", index=False)

    clean, report = load_clean_dataset(tmp_path / "off.tsv", chunksize=37)

    assert report.duplicate_codes == 1
    assert report.rows_final == len(clean) == 200
    first_code = raw.loc[0, "code"]
    assert clean.set_index(schema.ID_COLUMN).loc[first_code, schema.TARGET] == "E"  # la última
    assert "nutrition-score-fr_100g" not in clean.columns


def test_missing_contract_column_fails_fast(tmp_path: Path) -> None:
    raw = to_raw_frame(make_clean_frame(n_rows=10)).drop(columns=[schema.RAW_TARGET])
    raw.to_csv(tmp_path / "off.tsv", sep="\t", index=False)
    with pytest.raises(DatasetSchemaError, match=schema.RAW_TARGET):
        load_clean_dataset(tmp_path / "off.tsv")


def test_validate_clean_frame_accepts_valid_dataset() -> None:
    assert validate_clean_frame(make_clean_frame(n_rows=1_500)) == []


@pytest.mark.parametrize(
    ("corrupt", "expected"),
    [
        (lambda f: f.drop(columns=["salt_100g"]), "Faltan columnas"),
        (lambda f: f.head(10), "al menos"),
        (lambda f: f.assign(grade="Z"), "grado fuera"),
        (lambda f: pd.concat([f, f.head(1)]), "duplicados"),
        (lambda f: f.assign(sugars_100g=150.0), "fuera de rango"),
        (lambda f: f.assign(salt_100g=float("nan")), "obligatorios"),
        (lambda f: f.assign(saturated_fat_100g=f["fat_100g"] + 5), "saturated_fat_gt_fat"),
    ],
)
def test_validate_clean_frame_rejects_invalid_dataset(corrupt, expected: str) -> None:
    problems = validate_clean_frame(corrupt(make_clean_frame(n_rows=1_500)))
    assert any(expected in problem for problem in problems), problems
