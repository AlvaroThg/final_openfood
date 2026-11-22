"""Generador de datos sintéticos con el contrato de schema.py.

Permite probar limpieza, entrenamiento y artefactos sin descargar el dataset real (CI).
El grado se deriva de una puntuación tipo Nutri-Score (penaliza energía, grasas saturadas,
azúcares y sal; bonifica fibra y proteínas), así que es aprendible por los modelos.
Todas las filas respetan rangos y reglas de consistencia.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nutriscore import schema

PNNS_1 = [
    "Sugary snacks",
    "Beverages",
    "Milk and dairy products",
    "Cereals and potatoes",
    "unknown",
    None,
]
PNNS_2 = ["Biscuits and cakes", "Sweetened beverages", "Cheese", "Bread", "unknown", None]


def _with_missing(rng: np.random.Generator, values: np.ndarray, rate: float) -> np.ndarray:
    values = values.astype("float64")
    values[rng.random(len(values)) < rate] = np.nan
    return values


def make_clean_frame(n_rows: int = 3_000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    fat = rng.uniform(0, 35, n_rows)
    saturated = fat * rng.uniform(0, 0.6, n_rows)
    carbs = rng.uniform(0, 45, n_rows)
    sugars = carbs * rng.uniform(0, 0.8, n_rows)
    proteins = rng.uniform(0, 20, n_rows)
    salt = rng.uniform(0, 3, n_rows)
    fiber = rng.uniform(0, 10, n_rows)
    energy = 37 * fat + 17 * (carbs + proteins)

    score = energy / 400 + saturated / 1.5 + sugars / 5 + salt * 4 - fiber / 1.5 - proteins / 4
    grade = pd.qcut(score, q=5, labels=list(schema.GRADES)).astype(str)

    frame = pd.DataFrame(
        {
            schema.ID_COLUMN: [f"{seed:02d}{i:010d}" for i in range(n_rows)],
            schema.TARGET: grade,
            "energy_100g": energy,
            "fat_100g": fat,
            "saturated_fat_100g": saturated,
            "trans_fat_100g": _with_missing(rng, fat * 0.05, 0.7),
            "cholesterol_100g": _with_missing(rng, rng.uniform(0, 0.1, n_rows), 0.7),
            "carbohydrates_100g": carbs,
            "sugars_100g": sugars,
            "fiber_100g": _with_missing(rng, fiber, 0.3),
            "proteins_100g": proteins,
            "salt_100g": salt,
            "calcium_100g": _with_missing(rng, rng.uniform(0, 1, n_rows), 0.6),
            "iron_100g": _with_missing(rng, rng.uniform(0, 0.01, n_rows), 0.6),
            "vitamin_a_100g": _with_missing(rng, rng.uniform(0, 0.001, n_rows), 0.6),
            "vitamin_c_100g": _with_missing(rng, rng.uniform(0, 0.05, n_rows), 0.6),
            "fruits_vegetables_nuts_100g": _with_missing(rng, rng.uniform(0, 100, n_rows), 0.9),
            "additives_n": _with_missing(rng, rng.integers(0, 10, n_rows), 0.2),
            "ingredients_from_palm_oil_n": _with_missing(rng, rng.integers(0, 2, n_rows), 0.2),
            "ingredients_that_may_be_from_palm_oil_n": _with_missing(
                rng, rng.integers(0, 3, n_rows), 0.2
            ),
            "pnns_groups_1": rng.choice(np.array(PNNS_1, dtype=object), n_rows),
            "pnns_groups_2": rng.choice(np.array(PNNS_2, dtype=object), n_rows),
        }
    )
    return frame[list(schema.CANONICAL_COLUMNS)]


CANONICAL_TO_RAW = {canonical: raw for raw, canonical in schema.RAW_TO_CANONICAL.items()}


def to_raw_frame(clean: pd.DataFrame) -> pd.DataFrame:
    """Convierte al formato del TSV de Open Food Facts: nombres originales, texto, grado en
    minúsculas y columnas extra (incluida la de fuga) que la limpieza debe ignorar."""
    raw = clean.rename(columns=CANONICAL_TO_RAW).copy()
    raw[schema.RAW_TARGET] = raw[schema.RAW_TARGET].str.lower()
    raw["product_name"] = "producto"
    raw["nutrition-score-fr_100g"] = "10"
    raw["sodium_100g"] = "0.4"
    return raw.astype(object).where(raw.notna(), None)
