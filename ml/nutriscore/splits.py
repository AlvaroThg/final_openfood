"""Partición determinista train/val/test y muestreo estratificado.

La partición se decide por hash del código de producto (no al azar):
- Es reproducible sin guardar índices.
- Un mismo producto cae SIEMPRE en la misma partición, incluso en versiones nuevas del
  dataset. Así un producto de test nunca se filtra al entrenamiento de un modelo futuro.

Con cientos de miles de productos, el hash reparte las clases de forma prácticamente
proporcional; tests/ml/test_splits.py lo verifica.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from nutriscore import schema

# Cambiar la sal o los cortes invalida la comparabilidad entre modelos: requiere ADR.
SPLIT_SALT = "nutriscore-split-v1"
TRAIN_CUTOFF = 70  # buckets [0, 70)  -> train (70 %)
VAL_CUTOFF = 85  # buckets [70, 85) -> val   (15 %); [85, 100) -> test (15 %)
SPLITS: tuple[str, ...] = ("train", "val", "test")


def split_bucket(code: str) -> int:
    digest = hashlib.sha256(f"{SPLIT_SALT}:{code}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def assign_splits(codes: pd.Series) -> pd.Series:
    buckets = codes.astype(str).map(split_bucket).to_numpy()
    labels = np.select([buckets < TRAIN_CUTOFF, buckets < VAL_CUTOFF], ["train", "val"], "test")
    return pd.Series(labels, index=codes.index, name="split")


def split_frame(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    assignment = assign_splits(frame[schema.ID_COLUMN])
    return {name: frame[assignment == name].reset_index(drop=True) for name in SPLITS}


def stratified_sample(frame: pd.DataFrame, n_rows: int, seed: int) -> pd.DataFrame:
    """Muestra estratificada por grado (para explorar y comparar modelos rápido)."""
    if n_rows >= len(frame):
        return frame.copy()
    sample, _ = train_test_split(
        frame, train_size=n_rows, stratify=frame[schema.TARGET], random_state=seed
    )
    return sample.reset_index(drop=True)
