"""Entorno de retrain_service para pruebas: carpetas temporales, un dataset sintético en parquet,
una configuración de entrenamiento rápida y el pool de Postgres como app_retrain."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from psycopg_pool import ConnectionPool

from nutriscore.quality import QualityPolicy
from services.retrain_service.app.settings import RetrainSettings
from svc_common.db import DbConnection, create_pool
from tests.helpers import pg
from tests.helpers.synthetic import make_clean_frame

TEST_CONFIG = """
name = "test_tree"
algorithm = "decision_tree"
seed = 0

[features]
numeric = [
    "energy_100g", "fat_100g", "saturated_fat_100g", "trans_fat_100g", "cholesterol_100g",
    "carbohydrates_100g", "sugars_100g", "fiber_100g", "proteins_100g", "salt_100g",
    "calcium_100g", "iron_100g", "vitamin_a_100g", "vitamin_c_100g",
    "fruits_vegetables_nuts_100g", "additives_n", "ingredients_from_palm_oil_n",
    "ingredients_that_may_be_from_palm_oil_n",
]
categorical = ["pnns_groups_1", "pnns_groups_2"]
min_category_frequency = 10

[params]
max_depth = 6
class_weight = "balanced"
"""

# Política permisiva para probar el camino "aprobado" con datos sintéticos, e imposible para
# forzar el rechazo. La política real (qp-1) se prueba en tests/ml/test_quality.py.
LENIENT = QualityPolicy("qp-test", 0.0, 1.0, 0.0, 0.0, 50 * 1024 * 1024, 1.0, 50)
IMPOSSIBLE = QualityPolicy("qp-imposible", 1.01, 0.0, 1.01, 1.01, 1, 0.0, 50)


@dataclass
class RetrainEnv:
    db: str
    settings: RetrainSettings
    pool: ConnectionPool[DbConnection]
    admin_id: UUID


@pytest.fixture
def env(db: str, tmp_path: Path) -> Iterator[RetrainEnv]:
    datasets_dir, models_dir, configs_dir = (
        tmp_path / n for n in ("datasets", "models", "configs")
    )
    for directory in (datasets_dir, models_dir, configs_dir):
        directory.mkdir()
    (configs_dir / "test_tree.toml").write_text(TEST_CONFIG, encoding="utf-8")
    make_clean_frame(n_rows=3_000, seed=5).to_parquet(
        datasets_dir / "clean_v1.parquet", index=False
    )

    settings = RetrainSettings(
        database_url=pg.url_for("app_retrain", db),
        jwks_url="http://auth/jwks",
        datasets_dir=datasets_dir,
        models_dir=models_dir,
        configs_dir=configs_dir,
        training_configs=("test_tree",),
    )
    pool = create_pool(settings.database_url, name="test_retrain")
    pool.open(wait=True)
    try:
        yield RetrainEnv(
            db=db,
            settings=settings,
            pool=pool,
            admin_id=pg.insert_user(db, "admin@ejemplo.com", role="admin"),
        )
    finally:
        pool.close()
