"""Carga por bloques y limpieza del TSV de Open Food Facts.

Decisiones (documentadas en docs/ml-lifecycle.md):
- Solo se leen las columnas del contrato (schema.RAW_COLUMNS): ~22 de 163.
- Filas sin grado válido (A-E) o sin código se descartan.
- Valor fuera de rango físico -> NaN (se trata como faltante, no se inventa un valor).
- Fila que viola una regla de consistencia -> se descarta (dato no confiable).
- Fila sin alguno de los nutrientes obligatorios de la API -> se descarta. La API siempre
  los recibe, así que entrenar con ellos imputados crearía diferencias entre entrenamiento
  y producción (train/serve skew).
- Códigos duplicados -> se conserva la última aparición.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from nutriscore import schema


class DatasetSchemaError(ValueError):
    """El archivo no tiene las columnas esperadas por el contrato."""


@dataclass
class CleaningReport:
    rows_read: int = 0
    rows_without_target: int = 0
    rows_invalid_target: int = 0
    rows_without_code: int = 0
    rows_missing_required: int = 0
    rows_inconsistent: dict[str, int] = field(default_factory=dict)
    values_out_of_range: dict[str, int] = field(default_factory=dict)
    duplicate_codes: int = 0
    rows_final: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def check_raw_header(path: Path) -> None:
    """Falla rápido si el TSV no tiene las columnas del contrato (incluido el objetivo)."""
    header = pd.read_csv(path, sep="\t", nrows=0).columns
    missing = sorted(set(schema.RAW_COLUMNS) - set(header))
    if missing:
        raise DatasetSchemaError(f"Faltan columnas en {path.name}: {missing}")


def iter_raw_chunks(path: Path, chunksize: int = 50_000) -> Iterator[pd.DataFrame]:
    """Lee solo las columnas del contrato, como texto, en bloques (memoria acotada)."""
    check_raw_header(path)
    yield from pd.read_csv(
        path,
        sep="\t",
        usecols=list(schema.RAW_COLUMNS),
        dtype=str,
        chunksize=chunksize,
        on_bad_lines="warn",
        encoding="utf-8",
    )


def _add_counts(target: dict[str, int], counts: pd.Series) -> None:
    for key, value in counts.items():
        target[str(key)] = target.get(str(key), 0) + int(value)


def clean_chunk(raw: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Limpia un bloque con nombres del TSV y devuelve columnas canónicas."""
    report.rows_read += len(raw)
    frame = raw.rename(columns=schema.RAW_TO_CANONICAL)[list(schema.CANONICAL_COLUMNS)].copy()

    grade = frame[schema.TARGET].str.strip().str.upper()
    report.rows_without_target += int(grade.isna().sum())
    report.rows_invalid_target += int((grade.notna() & ~grade.isin(schema.GRADES)).sum())
    frame[schema.TARGET] = grade
    frame = frame[grade.isin(schema.GRADES)]

    code = frame[schema.ID_COLUMN].str.strip()
    has_code = code.notna() & (code != "")
    report.rows_without_code += int((~has_code).sum())
    frame = frame[has_code].assign(**{schema.ID_COLUMN: code[has_code]})

    for name in schema.NUMERIC_NAMES:
        frame[name] = pd.to_numeric(frame[name], errors="coerce").astype("float64")
    out_of_range = schema.range_violations(frame)
    _add_counts(report.values_out_of_range, out_of_range.sum())
    for name in schema.NUMERIC_NAMES:
        frame[name] = frame[name].mask(out_of_range[name])

    for name in schema.CATEGORICAL_NAMES:
        frame[name] = frame[name].str.strip().replace("", pd.NA)

    missing_required = frame[list(schema.REQUIRED_FEATURES)].isna().any(axis=1)
    report.rows_missing_required += int(missing_required.sum())
    frame = frame[~missing_required]

    violations = schema.consistency_violations(frame)
    _add_counts(report.rows_inconsistent, violations.sum())
    return frame[~violations.any(axis=1)]


def finalize(frame: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    """Elimina duplicados por código y fija tipos canónicos."""
    before = len(frame)
    frame = frame.drop_duplicates(subset=schema.ID_COLUMN, keep="last").copy()
    report.duplicate_codes = before - len(frame)
    for name in schema.CATEGORICAL_NAMES:
        frame[name] = frame[name].astype(object).where(frame[name].notna(), None)
    report.rows_final = len(frame)
    return frame.reset_index(drop=True)


def load_clean_dataset(path: Path, chunksize: int = 50_000) -> tuple[pd.DataFrame, CleaningReport]:
    """Pipeline completo: TSV crudo -> DataFrame canónico limpio + reporte."""
    report = CleaningReport()
    chunks = [clean_chunk(chunk, report) for chunk in iter_raw_chunks(path, chunksize)]
    if not chunks:
        raise DatasetSchemaError(f"{path.name} no contiene filas")
    return finalize(pd.concat(chunks, ignore_index=True), report), report


def validate_clean_frame(frame: pd.DataFrame, *, min_rows: int = 1_000) -> list[str]:
    """Valida un dataset YA procesado (p. ej. subido a retrain_service).

    Devuelve la lista de problemas encontrados; vacía = válido. No corrige nada: un dataset
    inválido se rechaza completo (fail fast, sin correcciones silenciosas).
    """
    missing = [c for c in schema.CANONICAL_COLUMNS if c not in frame.columns]
    if missing:
        return [f"Faltan columnas: {missing}"]

    problems: list[str] = []
    if len(frame) < min_rows:
        problems.append(f"Se requieren al menos {min_rows} filas (hay {len(frame)})")
    if frame[schema.ID_COLUMN].isna().any():
        problems.append("Hay filas sin código")
    if frame[schema.ID_COLUMN].duplicated().any():
        problems.append("Hay códigos duplicados")
    invalid_grades = ~frame[schema.TARGET].isin(schema.GRADES)
    if invalid_grades.any():
        problems.append(f"{int(invalid_grades.sum())} filas con grado fuera de {schema.GRADES}")

    for name in schema.NUMERIC_NAMES:
        if not pd.api.types.is_numeric_dtype(frame[name]):
            problems.append(f"La columna {name} no es numérica")
    if problems:
        return problems

    out_of_range = schema.range_violations(frame).sum()
    for name, count in out_of_range[out_of_range > 0].items():
        problems.append(f"{int(count)} valores fuera de rango en {name}")
    missing_required = frame[list(schema.REQUIRED_FEATURES)].isna().any(axis=1).sum()
    if missing_required:
        problems.append(f"{int(missing_required)} filas sin nutrientes obligatorios")
    inconsistent = schema.consistency_violations(frame).sum()
    for rule, count in inconsistent[inconsistent > 0].items():
        problems.append(f"{int(count)} filas violan la regla {rule}")
    return problems
