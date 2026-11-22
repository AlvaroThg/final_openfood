"""Endpoints de la API de reentrenamiento. Todos (salvo /health) exigen rol admin.

GET  /health
GET  /retrain/configs          configuraciones de entrenamiento permitidas
GET  /retrain/datasets         datasets registrados
POST /retrain/datasets         registra un parquet ya presente en el volumen → 201
GET  /retrain/jobs             últimos trabajos
POST /retrain/jobs             encola un entrenamiento → 202 (lo ejecuta el worker)
GET  /retrain/jobs/{id}        estado de un trabajo
"""

from __future__ import annotations

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from svc_common.auth import Principal, require_roles
from svc_common.http import error_response
from svc_common.tokens import Role

from ..domain.model import (
    DatasetAlreadyRegisteredError,
    DatasetFileNotFoundError,
    DatasetNotFoundError,
    DatasetRecord,
    InvalidDatasetError,
    JobNotFoundError,
    JobRecord,
    RetrainError,
    UnknownConfigError,
)
from ..schemas.api import (
    CreateJobRequest,
    DatasetResponse,
    ErrorResponse,
    HealthResponse,
    JobResponse,
    RegisterDatasetRequest,
)

router = APIRouter()
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]
_errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 503)
}


def get_container(request: Request) -> Any:
    return request.app.state.container


ContainerDep = Annotated[Any, Depends(get_container)]


def _dataset(record: DatasetRecord) -> DatasetResponse:
    return DatasetResponse(
        version=record.version,
        file_name=record.file_name,
        sha256=record.sha256,
        row_count=record.row_count,
        class_distribution=record.class_distribution,
        created_at=record.created_at,
    )


def _job(record: JobRecord) -> JobResponse:
    return JobResponse(
        id=record.id,
        dataset_version=record.dataset_version,
        config_name=record.config_name,
        status=record.status.value,
        model_version=record.model_version,
        activation=record.activation.value,
        error=record.error,
        attempts=record.attempts,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


@router.get("/health", response_model=HealthResponse, responses=_errors)
def health(c: ContainerDep) -> HealthResponse:
    c.health_check()
    return HealthResponse(status="ok")


@router.get("/retrain/configs", response_model=list[str], responses=_errors)
def configs(_: Admin, c: ContainerDep) -> list[str]:
    result: list[str] = c.jobs.configs()
    return result


@router.get("/retrain/datasets", response_model=list[DatasetResponse], responses=_errors)
def list_datasets(_: Admin, c: ContainerDep) -> list[DatasetResponse]:
    return [_dataset(d) for d in c.datasets.list()]


@router.post(
    "/retrain/datasets", response_model=DatasetResponse, status_code=201, responses=_errors
)
def register_dataset(
    body: RegisterDatasetRequest, admin: Admin, c: ContainerDep
) -> DatasetResponse:
    return _dataset(c.datasets.register(body.file_name, admin_id=admin.user_id))


@router.get("/retrain/jobs", response_model=list[JobResponse], responses=_errors)
def list_jobs(admin: Admin, c: ContainerDep) -> list[JobResponse]:
    return [_job(j) for j in c.jobs.list(admin_id=admin.user_id)]


@router.post("/retrain/jobs", response_model=JobResponse, status_code=202, responses=_errors)
def create_job(body: CreateJobRequest, admin: Admin, c: ContainerDep) -> JobResponse:
    job = c.jobs.enqueue(
        dataset_version=body.dataset_version, config_name=body.config_name, admin_id=admin.user_id
    )
    return _job(job)


@router.get("/retrain/jobs/{job_id}", response_model=JobResponse, responses=_errors)
def get_job(job_id: UUID, admin: Admin, c: ContainerDep) -> JobResponse:
    return _job(c.jobs.get(job_id, admin_id=admin.user_id))


# ---------------------------------------------------------------------------- errores
ERRORS: dict[type[RetrainError], tuple[int, str, str]] = {
    DatasetFileNotFoundError: (
        404,
        "DATASET_FILE_NOT_FOUND",
        "No existe ese archivo en el volumen de datasets.",
    ),
    InvalidDatasetError: (422, "INVALID_DATASET", "El dataset no cumple el contrato de datos."),
    DatasetAlreadyRegisteredError: (
        409,
        "DATASET_ALREADY_REGISTERED",
        "Ese contenido ya está registrado como otra versión.",
    ),
    DatasetNotFoundError: (404, "DATASET_NOT_FOUND", "No existe esa versión de dataset."),
    UnknownConfigError: (422, "UNKNOWN_CONFIG", "Configuración de entrenamiento no permitida."),
    JobNotFoundError: (404, "JOB_NOT_FOUND", "No existe ese trabajo."),
}


def install_retrain_error_handlers(app: FastAPI) -> None:
    async def handle(_: Request, raw: Exception) -> JSONResponse:
        exc = cast(RetrainError, raw)
        status, code, message = ERRORS.get(type(exc), (500, "INTERNAL_ERROR", "Error interno."))
        details = (
            [{"problem": p} for p in exc.problems] if isinstance(exc, InvalidDatasetError) else None
        )
        return error_response(status, code, message, details=details)

    app.add_exception_handler(RetrainError, handle)
