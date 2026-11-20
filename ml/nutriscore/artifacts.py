"""Guardado y carga de artefactos de modelo: inmutables, con hash y dependencias verificadas.

Estructura de una versión (nunca se sobrescribe):

    <models_root>/v1.1.0/
        model.joblib     Pipeline completo (preprocesamiento + modelo), comprimido
        metadata.json    versión, algoritmo, config, dataset, dependencias, hash, tamaño
        metrics.json     reporte de validación, de test y resultado de la política de calidad
        golden.json      entradas + probabilidades esperadas (prueba de paridad entre procesos)

Seguridad: joblib/pickle ejecuta código al cargar. Por eso el SHA-256 del archivo se
compara con el registrado en la base de datos ANTES de deserializar.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from nutriscore import schema
from nutriscore.lifecycle import parse_version

MODEL_FILE = "model.joblib"
METADATA_FILE = "metadata.json"
METRICS_FILE = "metrics.json"
GOLDEN_FILE = "golden.json"

# Deben coincidir EXACTAMENTE para cargar un artefacto (afectan la deserialización o la
# predicción). Las demás solo se registran para trazabilidad.
STRICT_PACKAGES: tuple[str, ...] = ("scikit-learn", "lightgbm", "numpy", "joblib")
RECORDED_PACKAGES: tuple[str, ...] = (*STRICT_PACKAGES, "pandas", "pyarrow", "nutriscore")

# Tolerancia de la prueba de paridad entre procesos/arquitecturas (exp, BLAS).
GOLDEN_ATOL = 1e-6


class ArtifactError(Exception):
    """Base de los errores de artefactos."""


class ArtifactExistsError(ArtifactError):
    """Se intentó sobrescribir una versión existente (las versiones son inmutables)."""


class ArtifactNotFoundError(ArtifactError):
    pass


class ArtifactIntegrityError(ArtifactError):
    """El hash del archivo no coincide con el registrado: no se deserializa."""


class DependencyMismatchError(ArtifactError):
    """Versiones de librerías distintas a las del entrenamiento."""


@dataclass(frozen=True, slots=True)
class ArtifactInfo:
    version: str
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class LoadedArtifact:
    pipeline: Pipeline
    metadata: dict[str, Any]
    golden: dict[str, Any] | None


def artifact_dir(models_root: Path, version: str) -> Path:
    parse_version(version)  # valida el formato antes de construir rutas
    return models_root / f"v{version}"


def dependency_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in RECORDED_PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True,
                               allow_nan=False), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_golden(pipeline: Pipeline, frame: pd.DataFrame) -> dict[str, Any]:
    features = frame[list(schema.FEATURE_NAMES)]
    records = features.astype(object).where(features.notna(), None).to_dict(orient="records")
    return {
        "classes": [str(c) for c in pipeline.classes_],
        "records": records,
        "probabilities": pipeline.predict_proba(features).tolist(),
    }


def verify_golden(pipeline: Pipeline, golden: dict[str, Any]) -> bool:
    """True si el pipeline reproduce las probabilidades registradas al entrenar."""
    if [str(c) for c in pipeline.classes_] != golden["classes"]:
        return False
    frame = schema.to_feature_frame(golden["records"])
    expected = np.asarray(golden["probabilities"], dtype="float64")
    return bool(np.allclose(pipeline.predict_proba(frame), expected, atol=GOLDEN_ATOL, rtol=0))


def save_artifact(
    pipeline: Pipeline,
    models_root: Path,
    version: str,
    *,
    metadata: dict[str, Any],
    metrics: dict[str, Any],
    golden_frame: pd.DataFrame | None = None,
) -> ArtifactInfo:
    """Escribe la versión en un directorio temporal y lo renombra al final: o queda completa
    o no queda nada (nunca una versión a medio escribir)."""
    target = artifact_dir(models_root, version)
    if target.exists():
        raise ArtifactExistsError(f"La versión {version} ya existe en {target}")
    models_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".tmp-v{version}-", dir=models_root))
    try:
        model_path = tmp / MODEL_FILE
        joblib.dump(pipeline, model_path, compress=3)
        digest = sha256_file(model_path)
        size = model_path.stat().st_size
        if golden_frame is not None:
            _write_json(tmp / GOLDEN_FILE, build_golden(pipeline, golden_frame))
        _write_json(tmp / METADATA_FILE, {
            **metadata,
            "version": version,
            "created_at": datetime.now(UTC).isoformat(),
            "artifact": {"file": MODEL_FILE, "sha256": digest, "size_bytes": size},
            "dependencies": dependency_versions(),
        })
        _write_json(tmp / METRICS_FILE, metrics)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return ArtifactInfo(version, target, digest, size)


def read_metadata(models_root: Path, version: str) -> dict[str, Any]:
    path = artifact_dir(models_root, version) / METADATA_FILE
    if not path.exists():
        raise ArtifactNotFoundError(f"No existe {path}")
    return _read_json(path)


def read_metrics(models_root: Path, version: str) -> dict[str, Any]:
    path = artifact_dir(models_root, version) / METRICS_FILE
    if not path.exists():
        raise ArtifactNotFoundError(f"No existe {path}")
    return _read_json(path)


def load_artifact(
    models_root: Path, version: str, expected_sha256: str, *, check_dependencies: bool = True
) -> LoadedArtifact:
    """Carga verificando, en orden: existencia, hash (antes de deserializar) y dependencias."""
    directory = artifact_dir(models_root, version)
    model_path = directory / MODEL_FILE
    if not model_path.exists():
        raise ArtifactNotFoundError(f"No existe {model_path}")

    actual = sha256_file(model_path)
    if actual != expected_sha256:
        raise ArtifactIntegrityError(
            f"Hash de {version} no coincide (esperado {expected_sha256[:12]}…, "
            f"real {actual[:12]}…)"
        )
    metadata = read_metadata(models_root, version)
    if metadata.get("artifact", {}).get("sha256") != expected_sha256:
        raise ArtifactIntegrityError(f"metadata.json de {version} no corresponde al artefacto")

    if check_dependencies:
        current = dependency_versions()
        recorded = metadata.get("dependencies", {})
        mismatches = {
            pkg: {"trained_with": recorded.get(pkg), "installed": current.get(pkg)}
            for pkg in STRICT_PACKAGES
            if recorded.get(pkg) != current.get(pkg)
        }
        if mismatches:
            raise DependencyMismatchError(f"Dependencias distintas para {version}: {mismatches}")

    pipeline = joblib.load(model_path)
    golden_path = directory / GOLDEN_FILE
    golden = _read_json(golden_path) if golden_path.exists() else None
    return LoadedArtifact(pipeline, metadata, golden)


def reload_parity(
    pipeline: Pipeline, models_root: Path, info: ArtifactInfo, frame: pd.DataFrame
) -> bool:
    """Criterio de calidad: el artefacto guardado predice EXACTAMENTE igual que en memoria."""
    loaded = load_artifact(models_root, info.version, info.sha256)
    features = frame[list(schema.FEATURE_NAMES)]
    same_labels = np.array_equal(pipeline.predict(features), loaded.pipeline.predict(features))
    same_proba = np.allclose(pipeline.predict_proba(features),
                             loaded.pipeline.predict_proba(features), atol=1e-12, rtol=0)
    return bool(same_labels and same_proba)
