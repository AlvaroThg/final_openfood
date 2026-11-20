from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from nutriscore import artifacts, schema
from nutriscore.artifacts import (
    ArtifactExistsError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    DependencyMismatchError,
    load_artifact,
    reload_parity,
    save_artifact,
    verify_golden,
)


@pytest.fixture
def saved(tmp_path: Path, trained_pipeline: Pipeline, splits: dict[str, pd.DataFrame]):
    info = save_artifact(trained_pipeline, tmp_path, "1.0.0",
                         metadata={"algorithm": "lightgbm"},
                         metrics={"validation": {"f1_macro": 0.9}},
                         golden_frame=splits["val"].head(20))
    return tmp_path, info


def test_round_trip_reproduces_predictions(saved, trained_pipeline: Pipeline,
                                           splits: dict[str, pd.DataFrame]) -> None:
    root, info = saved
    loaded = load_artifact(root, "1.0.0", info.sha256)
    features = splits["val"][list(schema.FEATURE_NAMES)]
    assert np.array_equal(loaded.pipeline.predict_proba(features),
                          trained_pipeline.predict_proba(features))
    assert loaded.metadata["artifact"] == {"file": "model.joblib", "sha256": info.sha256,
                                           "size_bytes": info.size_bytes}
    assert loaded.metadata["dependencies"]["scikit-learn"]
    assert loaded.metadata["version"] == "1.0.0"
    assert sorted(p.name for p in info.path.iterdir()) == [
        "golden.json", "metadata.json", "metrics.json", "model.joblib"]


def test_versions_are_immutable(saved, trained_pipeline: Pipeline) -> None:
    root, info = saved
    before = artifacts.sha256_file(info.path / "model.joblib")
    with pytest.raises(ArtifactExistsError):
        save_artifact(trained_pipeline, root, "1.0.0", metadata={}, metrics={})
    assert artifacts.sha256_file(info.path / "model.joblib") == before


def test_tampered_file_is_rejected_before_deserializing(saved, monkeypatch) -> None:
    root, info = saved
    with (info.path / "model.joblib").open("ab") as handle:
        handle.write(b"payload malicioso")

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("joblib.load no debe ejecutarse con un hash inválido")

    monkeypatch.setattr(joblib, "load", must_not_be_called)
    with pytest.raises(ArtifactIntegrityError):
        load_artifact(root, "1.0.0", info.sha256)


def test_wrong_expected_hash_is_rejected(saved) -> None:
    root, _ = saved
    with pytest.raises(ArtifactIntegrityError):
        load_artifact(root, "1.0.0", "0" * 64)


def test_dependency_mismatch_is_detected(saved) -> None:
    root, info = saved
    metadata_path = info.path / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["dependencies"]["scikit-learn"] = "0.0.1"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(DependencyMismatchError, match="scikit-learn"):
        load_artifact(root, "1.0.0", info.sha256)


def test_missing_version_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ArtifactNotFoundError):
        load_artifact(tmp_path, "9.9.9", "0" * 64)


def test_golden_detects_a_different_model(saved, splits: dict[str, pd.DataFrame],
                                          small_lgbm_config) -> None:
    import dataclasses

    from nutriscore.training import fit

    root, info = saved
    loaded = load_artifact(root, "1.0.0", info.sha256)
    assert loaded.golden is not None
    assert verify_golden(loaded.pipeline, loaded.golden)

    other = fit(dataclasses.replace(small_lgbm_config, seed=99,
                                    params={**small_lgbm_config.params, "n_estimators": 5}),
                splits["train"])
    assert not verify_golden(other, loaded.golden)


def test_reload_parity_holds_for_saved_artifact(saved, trained_pipeline: Pipeline,
                                                splits: dict[str, pd.DataFrame]) -> None:
    root, info = saved
    assert reload_parity(trained_pipeline, root, info, splits["val"])


def test_failed_save_leaves_no_partial_version(tmp_path: Path, trained_pipeline: Pipeline,
                                               monkeypatch) -> None:
    def broken_dump(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(joblib, "dump", broken_dump)
    with pytest.raises(OSError, match="disco lleno"):
        save_artifact(trained_pipeline, tmp_path, "1.0.0", metadata={}, metrics={})
    assert list(tmp_path.iterdir()) == []
