"""Carga de artefactos desde el volumen de modelos (montado en solo lectura).

Orden de verificación (nutriscore.artifacts.load_artifact + golden):
1. el archivo existe;
2. su SHA-256 coincide con el registrado en model_versions, ANTES de deserializar (joblib
   ejecuta código al cargar: un pickle manipulado sería ejecución remota de código);
3. las versiones de scikit-learn, lightgbm, numpy y joblib son exactamente las del
   entrenamiento;
4. el modelo reproduce las probabilidades del golden.json (si no, no se sirve).
"""

from __future__ import annotations

from pathlib import Path

from nutriscore.artifacts import ArtifactError, load_artifact, verify_golden

from ..application.ports import LoadedModel
from ..domain.model import ModelLoadError


class FsArtifactStore:
    def __init__(self, models_dir: Path) -> None:
        self._models_dir = models_dir

    def load(self, version: str, sha256: str) -> LoadedModel:
        try:
            artifact = load_artifact(self._models_dir, version, sha256)
        except (ArtifactError, ValueError, OSError) as exc:
            raise ModelLoadError(f"{version}: {exc}") from exc
        if artifact.golden is None:
            raise ModelLoadError(f"{version}: falta golden.json, no se puede verificar")
        if not verify_golden(artifact.pipeline, artifact.golden):
            raise ModelLoadError(f"{version}: no reproduce las predicciones del golden")
        return LoadedModel(version=version, pipeline=artifact.pipeline, metadata=artifact.metadata)
