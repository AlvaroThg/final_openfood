"""Contrato HTTP de la API de reentrenamiento."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterDatasetRequest(_Strict):
    file_name: str = Field(
        min_length=9,
        max_length=108,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.parquet$",
        examples=["clean_v1.parquet"],
        description="Archivo ya presente en el volumen de datasets",
    )


class CreateJobRequest(_Strict):
    dataset_version: str = Field(pattern=r"^v[1-9][0-9]*$", examples=["v1"])
    config_name: str = Field(pattern=r"^[a-z0-9_]{1,64}$", examples=["lightgbm"])


class DatasetResponse(BaseModel):
    version: str
    file_name: str
    sha256: str
    row_count: int
    class_distribution: dict[str, Any]
    created_at: datetime


class JobResponse(BaseModel):
    id: UUID
    dataset_version: str
    config_name: str
    status: Literal["queued", "running", "succeeded", "failed"]
    model_version: str | None
    activation: Literal["not_requested", "succeeded", "failed", "skipped"]
    error: str | None
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
