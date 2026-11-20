"""Prueba de punta a punta del flujo offline: TSV crudo -> parquet -> modelo versionado."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from nutriscore import schema
from nutriscore.artifacts import load_artifact
from tests.helpers.scripts import load_script
from tests.helpers.synthetic import make_clean_frame, to_raw_frame

FAST_CONFIG = """
name = "fast"
algorithm = "lightgbm"
seed = 1
[features]
numeric = {numeric}
categorical = ["pnns_groups_1", "pnns_groups_2"]
min_category_frequency = 10
[params]
n_estimators = 80
num_leaves = 15
class_weight = "balanced"
n_jobs = 1
deterministic = true
force_row_wise = true
verbose = -1
"""


def test_prepare_then_train_produces_traceable_model(tmp_path: Path) -> None:
    raw_path = tmp_path / "off.tsv"
    to_raw_frame(make_clean_frame(n_rows=4_000, seed=11)).to_csv(raw_path, sep="\t",
                                                                  index=False)
    parquet = tmp_path / "clean_v1.parquet"

    prepare = load_script("prepare_data")
    assert prepare.main(["--input", str(raw_path), "--output", str(parquet)]) == 0
    assert prepare.main(["--input", str(raw_path), "--output", str(parquet)]) == 1  # inmutable
    report = json.loads(parquet.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["rows_final"] == len(pd.read_parquet(parquet)) == 4_000

    config_path = tmp_path / "fast.toml"
    config_path.write_text(FAST_CONFIG.format(numeric=json.dumps(list(schema.NUMERIC_NAMES))),
                           encoding="utf-8")
    models = tmp_path / "models"
    exit_code = load_script("train").main([
        "--config", str(config_path), "--data", str(parquet), "--dataset-version", "v1",
        "--models-dir", str(models), "--version", "1.0.0",
    ])

    gate = json.loads((models / "v1.0.0.gate.json").read_text(encoding="utf-8"))
    assert exit_code == (0 if gate["passed"] else 3)
    assert gate["policy_version"] == "qp-1"

    metadata = json.loads((models / "v1.0.0" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["dataset"]["version"] == "v1"
    assert metadata["dataset"]["sha256"] == report["sha256"]
    assert metadata["config"]["seed"] == 1
    assert metadata["quality_policy_version"] == "qp-1"
    metrics = json.loads((models / "v1.0.0" / "metrics.json").read_text(encoding="utf-8"))
    assert {"validation", "test"} <= set(metrics)

    loaded = load_artifact(models, "1.0.0", metadata["artifact"]["sha256"])
    assert list(loaded.pipeline.classes_) == list(schema.GRADES)
