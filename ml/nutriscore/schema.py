"""Contrato de datos del proyecto: fuente ÚNICA de features, rangos y reglas de consistencia.

Lo usan:
- cleaning.py: limpieza del dataset de Open Food Facts.
- training.py: selección de columnas del modelo.
- retrain_service: validación de datasets subidos.
- ia_backend: validación de solicitudes de predicción (rangos y consistencia).

Cambiar, agregar o quitar una feature aquí es un cambio MAJOR del contrato de la API.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

GRADES: tuple[str, ...] = ("A", "B", "C", "D", "E")

ID_COLUMN = "code"
TARGET = "grade"
RAW_TARGET = "nutrition_grade_fr"

# Columnas que NUNCA se usan como features.
# - Fuga de datos: el puntaje numérico determina el grado por umbrales fijos.
# - Redundancia: sal = sodio × 2,5 (se conserva solo la sal).
LEAKAGE_RAW_COLUMNS: tuple[str, ...] = ("nutrition-score-fr_100g", "nutrition-score-uk_100g")
REDUNDANT_RAW_COLUMNS: tuple[str, ...] = ("sodium_100g",)


@dataclass(frozen=True, slots=True)
class NumericFeature:
    name: str  # nombre canónico (snake_case): API, datasets procesados y modelo
    raw_name: str  # nombre en el TSV de Open Food Facts
    unit: str
    min_value: float
    max_value: float
    in_formula: bool  # la usa la fórmula oficial del Nutri-Score
    required_in_api: bool
    label: str


@dataclass(frozen=True, slots=True)
class CategoricalFeature:
    name: str
    raw_name: str
    label: str
    max_length: int = 100


def _grams(
    name: str, raw_name: str, label: str, *, formula: bool = False, required: bool = False
) -> NumericFeature:
    """Nutriente expresado en gramos por 100 g: rango físico [0, 100]."""
    return NumericFeature(name, raw_name, "g/100g", 0.0, 100.0, formula, required, label)


NUMERIC_FEATURES: tuple[NumericFeature, ...] = (
    # 3800 kJ ≈ 900 kcal: máximo físico (grasa pura ≈ 3700 kJ).
    NumericFeature("energy_100g", "energy_100g", "kJ/100g", 0.0, 3800.0, True, True, "Energía"),
    _grams("fat_100g", "fat_100g", "Grasas totales"),
    _grams("saturated_fat_100g", "saturated-fat_100g", "Grasas saturadas", formula=True,
           required=True),
    _grams("trans_fat_100g", "trans-fat_100g", "Grasas trans"),
    _grams("cholesterol_100g", "cholesterol_100g", "Colesterol"),
    _grams("carbohydrates_100g", "carbohydrates_100g", "Carbohidratos"),
    _grams("sugars_100g", "sugars_100g", "Azúcares", formula=True, required=True),
    _grams("fiber_100g", "fiber_100g", "Fibra", formula=True),
    _grams("proteins_100g", "proteins_100g", "Proteínas", formula=True, required=True),
    _grams("salt_100g", "salt_100g", "Sal", formula=True, required=True),
    _grams("calcium_100g", "calcium_100g", "Calcio"),
    _grams("iron_100g", "iron_100g", "Hierro"),
    _grams("vitamin_a_100g", "vitamin-a_100g", "Vitamina A"),
    _grams("vitamin_c_100g", "vitamin-c_100g", "Vitamina C"),
    NumericFeature("fruits_vegetables_nuts_100g", "fruits-vegetables-nuts_100g", "%", 0.0,
                   100.0, True, False, "Frutas, verduras y frutos secos"),
    NumericFeature("additives_n", "additives_n", "count", 0.0, 100.0, False, False,
                   "Número de aditivos"),
    NumericFeature("ingredients_from_palm_oil_n", "ingredients_from_palm_oil_n", "count", 0.0,
                   20.0, False, False, "Ingredientes de aceite de palma"),
    NumericFeature("ingredients_that_may_be_from_palm_oil_n",
                   "ingredients_that_may_be_from_palm_oil_n", "count", 0.0, 20.0, False, False,
                   "Ingredientes que pueden ser de aceite de palma"),
)

CATEGORICAL_FEATURES: tuple[CategoricalFeature, ...] = (
    CategoricalFeature("pnns_groups_1", "pnns_groups_1", "Grupo de alimento (PNNS 1)"),
    CategoricalFeature("pnns_groups_2", "pnns_groups_2", "Subgrupo de alimento (PNNS 2)"),
)

NUMERIC_NAMES: tuple[str, ...] = tuple(f.name for f in NUMERIC_FEATURES)
CATEGORICAL_NAMES: tuple[str, ...] = tuple(f.name for f in CATEGORICAL_FEATURES)
FEATURE_NAMES: tuple[str, ...] = NUMERIC_NAMES + CATEGORICAL_NAMES
FORMULA_FEATURES: tuple[str, ...] = tuple(f.name for f in NUMERIC_FEATURES if f.in_formula)
REQUIRED_FEATURES: tuple[str, ...] = tuple(f.name for f in NUMERIC_FEATURES if f.required_in_api)
NUMERIC_BY_NAME: dict[str, NumericFeature] = {f.name: f for f in NUMERIC_FEATURES}

# Mapeo nombre del TSV -> nombre canónico (incluye id y objetivo).
RAW_TO_CANONICAL: dict[str, str] = {
    ID_COLUMN: ID_COLUMN,
    RAW_TARGET: TARGET,
    **{f.raw_name: f.name for f in NUMERIC_FEATURES},
    **{f.raw_name: f.name for f in CATEGORICAL_FEATURES},
}
RAW_COLUMNS: tuple[str, ...] = tuple(RAW_TO_CANONICAL)
CANONICAL_COLUMNS: tuple[str, ...] = (ID_COLUMN, TARGET, *FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Reglas de consistencia entre nutrientes.
# Se aplican igual al limpiar el dataset (fila descartada) y al validar una
# solicitud de predicción (error 400). Un valor faltante nunca es violación.
# ---------------------------------------------------------------------------

# Tolerancia por redondeo de las etiquetas nutricionales.
TOLERANCE_G = 0.5
# Grasas + carbohidratos + proteínas + sal. La fibra se excluye porque en EE. UU.
# se reporta dentro de los carbohidratos y se contaría dos veces.
MACROS_FOR_SUM: tuple[str, ...] = ("fat_100g", "carbohydrates_100g", "proteins_100g", "salt_100g")
MACROS_SUM_LIMIT_G = 105.0


@dataclass(frozen=True, slots=True)
class ConsistencyRule:
    code: str
    message: str
    violated: Callable[[pd.DataFrame], pd.Series]  # True = la fila viola la regla


def _exceeds(part: str, whole: str) -> Callable[[pd.DataFrame], pd.Series]:
    # Comparaciones con NaN son False: un faltante no es una violación.
    return lambda df: df[part] > df[whole] + TOLERANCE_G


CONSISTENCY_RULES: tuple[ConsistencyRule, ...] = (
    ConsistencyRule("saturated_fat_gt_fat",
                    "Las grasas saturadas no pueden superar las grasas totales",
                    _exceeds("saturated_fat_100g", "fat_100g")),
    ConsistencyRule("trans_fat_gt_fat",
                    "Las grasas trans no pueden superar las grasas totales",
                    _exceeds("trans_fat_100g", "fat_100g")),
    ConsistencyRule("sugars_gt_carbohydrates",
                    "Los azúcares no pueden superar los carbohidratos",
                    _exceeds("sugars_100g", "carbohydrates_100g")),
    ConsistencyRule("macros_sum_gt_100",
                    "Grasas + carbohidratos + proteínas + sal no pueden superar 100 g por 100 g",
                    lambda df: df[list(MACROS_FOR_SUM)].sum(axis=1, min_count=1)
                    > MACROS_SUM_LIMIT_G),
)


def consistency_violations(frame: pd.DataFrame) -> pd.DataFrame:
    """Devuelve un DataFrame booleano (una columna por regla) con True donde hay violación."""
    return pd.DataFrame(
        {rule.code: rule.violated(frame).fillna(False).astype(bool) for rule in CONSISTENCY_RULES},
        index=frame.index,
    )


def range_violations(frame: pd.DataFrame) -> pd.DataFrame:
    """True donde un valor numérico está fuera de su rango físico (NaN nunca viola)."""
    return pd.DataFrame(
        {f.name: (frame[f.name] < f.min_value) | (frame[f.name] > f.max_value)
         for f in NUMERIC_FEATURES},
        index=frame.index,
    )


def to_feature_frame(records: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    """Construye el DataFrame de features con el orden y tipos canónicos.

    Lo usa ia_backend para convertir una solicitud en la entrada del pipeline, de modo que
    la API y el entrenamiento ven exactamente la misma estructura. Claves ausentes -> NaN.
    """
    rows = [{name: record.get(name) for name in FEATURE_NAMES} for record in records]
    frame = pd.DataFrame(rows, columns=list(FEATURE_NAMES))
    for name in NUMERIC_NAMES:
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype("float64")
    for name in CATEGORICAL_NAMES:
        frame[name] = frame[name].astype(object).where(frame[name].notna(), np.nan)
    return frame
