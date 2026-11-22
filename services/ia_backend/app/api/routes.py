"""Endpoints de ia_backend.

    GET  /health                              vivo, con BD y estado del modelo     público
    GET  /model/schema                        contrato de features (formulario)    público
    GET  /model/info                          versión activa y su origen           user/admin
    GET  /model/metrics                       métricas y decisión de calidad       user/admin
    POST /predict                             predicción (se guarda en historial)  user/admin
    GET  /predictions                         historial propio (?all=true: admin)  user/admin
    GET  /admin/models                        todas las versiones                  admin
    POST /admin/models/{version}/activate     activar o volver atrás (rollback)    admin
    POST /internal/models/{version}/activate  activación pedida por retrain        service

/internal/* exige un token de SERVICIO (audiencia nutriscore-internal) de un cliente permitido;
además, el frontend no reenvía esas rutas (no son accesibles desde el navegador).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from svc_common.auth import Principal, require_roles
from svc_common.http import AppError
from svc_common.tokens import INTERNAL_AUDIENCE, Role

from ..container import Container
from ..domain.model import (
    ModelUnavailableError,
    ModelVersionRecord,
    PredictionRecord,
)
from ..schemas.api import (
    ActivationResponse,
    ErrorResponse,
    HealthResponse,
    HistoryResponse,
    ModelInfoResponse,
    ModelMetricsResponse,
    ModelSchemaResponse,
    ModelVersionSummary,
    PredictionResponse,
    PredictRequest,
    schema_response,
)

router = APIRouter()

_errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 422, 429, 503)
}

AnyUser = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]
Service = Annotated[Principal, Depends(require_roles(Role.SERVICE, audience=INTERNAL_AUDIENCE))]


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def _prediction(record: PredictionRecord) -> PredictionResponse:
    return PredictionResponse(
        id=record.id,
        grade=record.grade,
        probabilities=record.probabilities,
        model_version=record.model_version,
        inference_ms=record.inference_ms,
        input=record.input,
        created_at=record.created_at,
    )


def _summary(record: ModelVersionRecord) -> ModelVersionSummary:
    validation = (record.metrics or {}).get("validation") or {}
    return ModelVersionSummary(
        version=record.version,
        status=str(record.status),
        algorithm=record.algorithm,
        dataset_version=record.dataset_version,
        f1_macro_validation=validation.get("f1_macro"),
        gate_passed=(record.gate or {}).get("passed"),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _active_record(c: Container) -> ModelVersionRecord:
    model = c.models.current
    record = c.registry.get(model.version) if model else None
    if record is None:
        raise ModelUnavailableError
    return record


# ------------------------------------------------------------------------------ público
@router.get("/health", response_model=HealthResponse, responses=_errors)
def health(c: ContainerDep) -> HealthResponse:
    c.health_check()
    if c.models.load_error is not None:
        raise AppError(
            503, "MODEL_LOAD_FAILED", "El modelo activo no pasó la verificación al cargarse."
        )
    model = c.models.current
    return HealthResponse(status="ok", model_version=model.version if model else None)


@router.get("/model/schema", response_model=ModelSchemaResponse)
def model_schema() -> ModelSchemaResponse:
    return schema_response()


# --------------------------------------------------------------------------- usuarios
@router.get("/model/info", response_model=ModelInfoResponse, responses=_errors)
def model_info(_: AnyUser, c: ContainerDep) -> ModelInfoResponse:
    record = _active_record(c)
    model = c.models.current
    trained_at = model.metadata.get("created_at") if model else None
    return ModelInfoResponse(
        version=record.version,
        algorithm=record.algorithm,
        dataset_version=record.dataset_version,
        quality_policy_version=record.quality_policy_version,
        trained_at=trained_at,
        activated_at=record.updated_at,
    )


@router.get("/model/metrics", response_model=ModelMetricsResponse, responses=_errors)
def model_metrics(_: AnyUser, c: ContainerDep) -> ModelMetricsResponse:
    record = _active_record(c)
    return ModelMetricsResponse(
        version=record.version,
        quality_policy_version=record.quality_policy_version,
        metrics=record.metrics or {},
        gate=record.gate,
    )


@router.post("/predict", response_model=PredictionResponse, status_code=201, responses=_errors)
def predict(
    # PredictRequest se genera desde nutriscore.schema; mypy no puede verlo como tipo.
    body: PredictRequest,  # type: ignore[valid-type]
    principal: AnyUser,
    c: ContainerDep,
) -> PredictionResponse:
    decision = c.predict_limiter.hit(principal.subject)
    if not decision.allowed:
        raise AppError(
            429,
            "RATE_LIMITED",
            "Demasiadas predicciones seguidas. Espera un momento.",
            headers={"Retry-After": str(decision.retry_after_s)},
        )
    features = body.model_dump()  # type: ignore[attr-defined]
    return _prediction(c.predictions.predict(principal, features))


@router.get("/predictions", response_model=HistoryResponse, responses=_errors)
def history(
    principal: AnyUser,
    c: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    all: Annotated[bool, Query(description="Solo admin: historial de todos")] = False,  # noqa: A002
) -> HistoryResponse:
    if all and principal.role is not Role.ADMIN:
        raise AppError(403, "FORBIDDEN", "Solo un administrador puede ver todo el historial.")
    records = c.predictions.history(principal, limit=limit, offset=offset, include_all=all)
    return HistoryResponse(items=[_prediction(r) for r in records], limit=limit, offset=offset)


# ------------------------------------------------------------------------------ admin
@router.get("/admin/models", response_model=list[ModelVersionSummary], responses=_errors)
def list_models(_: Admin, c: ContainerDep) -> list[ModelVersionSummary]:
    return [_summary(record) for record in c.registry.list()]


@router.post(
    "/admin/models/{version}/activate", response_model=ActivationResponse, responses=_errors
)
def activate_by_admin(version: str, principal: Admin, c: ContainerDep) -> ActivationResponse:
    result = c.models.activate(version, actor_user_id=principal.user_id, role=principal.role)
    return ActivationResponse(
        version=result.version, changed=result.changed, previous=result.previous
    )


# ---------------------------------------------------------------------------- interno
@router.post(
    "/internal/models/{version}/activate", response_model=ActivationResponse, responses=_errors
)
def activate_by_service(version: str, principal: Service, c: ContainerDep) -> ActivationResponse:
    if principal.subject not in c.settings.internal_clients:
        raise AppError(403, "FORBIDDEN", "Este cliente de servicio no puede activar modelos.")
    result = c.models.activate(version, actor_user_id=None, role=principal.role)
    return ActivationResponse(
        version=result.version, changed=result.changed, previous=result.previous
    )
