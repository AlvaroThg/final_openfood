"""Preprocesamiento del modelo.

Todas las transformaciones viven DENTRO del Pipeline de scikit-learn y se serializan con
el modelo. Así el notebook, el worker de reentrenamiento y la API aplican exactamente las
mismas transformaciones (no existe una segunda implementación que pueda divergir).

Importante: las funciones usadas en el pipeline deben ser de nivel de módulo, porque
joblib las guarda por referencia (`nutriscore.preprocessing.normalize_categories`).
Renombrarlas o moverlas rompe la carga de artefactos existentes (cambio MAJOR).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder

UNKNOWN_CATEGORY = "unknown"
_SEPARATORS = re.compile(r"[\s_-]+")
_UNSAFE_NAME_CHARS = re.compile(r"[^0-9a-zA-Z]+")


def safe_feature_name(feature: str, category: object) -> str:
    """Nombre de columna one-hot sin caracteres especiales (LightGBM rechaza algunos, p. ej.
    comas o dos puntos). Ej.: ('pnns_groups_1', 'sugary snacks') -> 'pnns_groups_1__sugary_snacks'.
    Si dos categorías colisionaran, ColumnTransformer falla al entrenar (no en silencio)."""
    return f"{feature}__{_UNSAFE_NAME_CHARS.sub('_', str(category)).strip('_').lower()}"


def normalize_category_value(value: object) -> object:
    """'Sugary-snacks ' -> 'sugary snacks'. Vacío -> NaN (luego se imputa como 'unknown')."""
    text = _SEPARATORS.sub(" ", str(value)).strip().lower()
    return text if text else np.nan


def normalize_categories(frame: pd.DataFrame) -> pd.DataFrame:
    """Normaliza mayúsculas/separadores de las columnas categóricas (Open Food Facts mezcla
    'Sugary snacks', 'sugary-snacks', etc.)."""
    frame = pd.DataFrame(frame).copy()
    for column in frame.columns:
        frame[column] = frame[column].map(normalize_category_value, na_action="ignore")
        frame[column] = frame[column].astype(object).where(frame[column].notna(), np.nan)
    return frame


def build_preprocessor(
    numeric_features: Sequence[str],
    categorical_features: Sequence[str],
    *,
    min_category_frequency: int = 50,
) -> ColumnTransformer:
    """Construye el preprocesador.

    - Numéricas: mediana + indicador de faltante (el faltante es informativo en OFF: los
      productos de EE. UU. traen vitaminas y minerales; los europeos, casi nunca).
    - Categóricas: normalización + 'unknown' para faltantes + one-hot. Las categorías con
      menos de `min_category_frequency` filas se agrupan como infrecuentes, y una categoría
      nunca vista en la API cae en ese grupo en lugar de fallar.
    - No se escala: los modelos son de árboles, invariantes a escala.
    """
    numeric = SimpleImputer(strategy="median", add_indicator=True)
    categorical = Pipeline(
        steps=[
            ("normalize", FunctionTransformer(normalize_categories,
                                              feature_names_out="one-to-one")),
            ("impute", SimpleImputer(strategy="constant", fill_value=UNKNOWN_CATEGORY)),
            ("onehot", OneHotEncoder(handle_unknown="infrequent_if_exist",
                                     min_frequency=min_category_frequency,
                                     sparse_output=False,
                                     feature_name_combiner=safe_feature_name)),
        ]
    )
    transformers = [("numeric", numeric, list(numeric_features))]
    if categorical_features:
        transformers.append(("categorical", categorical, list(categorical_features)))
    # Salida pandas: el modelo se entrena y predice con los MISMOS nombres de columna
    # (sin advertencias espurias de LightGBM) y la importancia de variables es legible.
    preprocessor = ColumnTransformer(transformers, remainder="drop",
                                     verbose_feature_names_out=False)
    return preprocessor.set_output(transform="pandas")
