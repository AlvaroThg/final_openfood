"""Contrato HTTP de ia_backend.

PredictRequest se GENERA desde nutriscore.schema (fuente única): nombres, rangos físicos,
obligatorios y longitudes son los mismos que usó la limpieza del entrenamiento. Agregar una
feature al contrato la agrega aquí y en /model/schema sin tocar este archivo.

- `extra="forbid"`: un campo desconocido es 422, no se ignora.
- `allow_inf_nan=False`: NaN e infinito no son nutrientes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, create_model

from nutriscore import schema

Grade = Literal["A", "B", "C", "D", "E"]


def _build_predict_request() -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for feature in schema.NUMERIC_FEATURES:
        description = f"{feature.label} ({feature.unit})"
        if feature.required_in_api:
            fields[feature.name] = (
                float,
                Field(ge=feature.min_value, le=feature.max_value, description=description),
            )
        else:
            fields[feature.name] = (
                float | None,
                Field(None, ge=feature.min_value, le=feature.max_value, description=description),
            )
    for categorical in schema.CATEGORICAL_FEATURES:
        fields[categorical.name] = (
            str | None,
            Field(
                None, min_length=1, max_length=categorical.max_length, description=categorical.label
            ),
        )
    model: type[BaseModel] = create_model(
        "PredictRequest",
        __config__=ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True),
        **fields,
    )
    return model


PredictRequest = _build_predict_request()


class PredictionResponse(BaseModel):
    id: UUID
    grade: Grade
    probabilities: dict[str, float]
    model_version: str
    inference_ms: float
    input: dict[str, Any]
    created_at: datetime


class HistoryResponse(BaseModel):
    items: list[PredictionResponse]
    limit: int
    offset: int


class NumericFeatureInfo(BaseModel):
    name: str
    label: str
    unit: str
    min: float
    max: float
    required: bool
    in_formula: bool


class CategoricalFeatureInfo(BaseModel):
    name: str
    label: str
    max_length: int


class ModelSchemaResponse(BaseModel):
    grades: list[str]
    numeric: list[NumericFeatureInfo]
    categorical: list[CategoricalFeatureInfo]


class ModelInfoResponse(BaseModel):
    version: str
    algorithm: str
    dataset_version: str
    quality_policy_version: str | None
    trained_at: str | None
    activated_at: datetime


class ModelMetricsResponse(BaseModel):
    version: str
    quality_policy_version: str | None
    metrics: dict[str, Any]
    gate: dict[str, Any] | None


class ModelVersionSummary(BaseModel):
    version: str
    status: str
    algorithm: str
    dataset_version: str
    f1_macro_validation: float | None
    gate_passed: bool | None
    created_at: datetime
    updated_at: datetime


class ActivationResponse(BaseModel):
    version: str
    changed: bool
    previous: str | None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model_version: str | None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def schema_response() -> ModelSchemaResponse:
    return ModelSchemaResponse(
        grades=list(schema.GRADES),
        numeric=[
            NumericFeatureInfo(
                name=f.name,
                label=f.label,
                unit=f.unit,
                min=f.min_value,
                max=f.max_value,
                required=f.required_in_api,
                in_formula=f.in_formula,
            )
            for f in schema.NUMERIC_FEATURES
        ],
        categorical=[
            CategoricalFeatureInfo(name=c.name, label=c.label, max_length=c.max_length)
            for c in schema.CATEGORICAL_FEATURES
        ],
    )
