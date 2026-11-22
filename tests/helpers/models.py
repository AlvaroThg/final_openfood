"""Modelos reales pequeños para probar los servicios (entrenan en menos de un segundo).

`save_small_model` produce un artefacto verificable de verdad (hash, metadata, golden), así las
pruebas de ia_backend y retrain_service ejercitan la misma carga que en producción.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd

from nutriscore import schema
from nutriscore.artifacts import ArtifactInfo, save_artifact
from nutriscore.evaluation import classification_report
from nutriscore.quality import CURRENT_POLICY
from nutriscore.splits import split_frame
from nutriscore.training import TrainingConfig, fit
from tests.helpers import pg
from tests.helpers.synthetic import make_clean_frame


@functools.cache
def small_splits(seed: int = 1) -> dict[str, pd.DataFrame]:
    return split_frame(make_clean_frame(n_rows=2_000, seed=seed))


def small_config(seed: int = 0) -> TrainingConfig:
    return TrainingConfig(
        name="test_tree",
        algorithm="decision_tree",
        seed=seed,
        numeric_features=schema.NUMERIC_NAMES,
        categorical_features=schema.CATEGORICAL_NAMES,
        params={"max_depth": 6, "class_weight": "balanced"},
        min_category_frequency=10,
    )


def save_small_model(models_dir: Path, version: str, *, seed: int = 0) -> ArtifactInfo:
    splits = small_splits()
    config = small_config(seed)
    pipeline = fit(config, splits["train"])
    val = splits["val"]
    validation = classification_report(val[schema.TARGET], pd.Series(pipeline.predict(val)))
    return save_artifact(
        pipeline,
        models_dir,
        version,
        metadata={"algorithm": config.algorithm, "config": config.to_dict()},
        metrics={"validation": validation, "test": None},
        golden_frame=val.head(20),
    )


def valid_features() -> dict[str, Any]:
    """Una entrada válida para /predict (los cinco obligatorios y algunos opcionales)."""
    return {
        "energy_100g": 1500.0,
        "saturated_fat_100g": 3.0,
        "fat_100g": 10.0,
        "sugars_100g": 12.0,
        "carbohydrates_100g": 40.0,
        "proteins_100g": 6.0,
        "salt_100g": 0.8,
        "fiber_100g": 2.5,
        "pnns_groups_1": "Sugary snacks",
    }


def register_model(
    db: str,
    version: str,
    info: ArtifactInfo,
    *,
    dataset_id: UUID,
    approve: bool = True,
    f1_macro: float = 0.9,
) -> UUID:
    """Registra la versión como lo hace retrain_service (app_retrain):
    candidate → evaluating → approved (o rejected)."""
    model_id = pg.insert_model_version(db, version, dataset_id)
    metrics = {"validation": {"f1_macro": f1_macro}, "test": None}
    gate = {"policy_version": CURRENT_POLICY.version, "passed": approve, "checks": []}
    with pg.connect("app_retrain", db) as conn, conn.transaction():
        conn.execute("UPDATE model_versions SET status = 'evaluating' WHERE id = %s", (model_id,))
        conn.execute(
            "UPDATE model_versions SET status = %s, artifact_sha256 = %s,"
            " artifact_size_bytes = %s, metrics = %s, dependencies = '{}',"
            " quality_policy_version = %s, gate = %s WHERE id = %s",
            (
                "approved" if approve else "rejected",
                info.sha256,
                info.size_bytes,
                json.dumps(metrics),
                CURRENT_POLICY.version,
                json.dumps(gate),
                model_id,
            ),
        )
    return model_id
