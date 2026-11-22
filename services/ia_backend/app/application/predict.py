"""Caso de uso de predicción e historial.

predict(usuario, entrada):
    modelo = el activo en memoria; si no hay → ModelUnavailableError (503)
    construir el DataFrame con nutriscore.schema.to_feature_frame (mismo formato que al entrenar)
    reglas físicas (nutriscore.schema.consistency_violations) → InconsistentInputError (400)
    predict_proba → grado = clase con mayor probabilidad; medir inference_ms
    guardar en predictions (con RLS, la versión EXACTA del modelo) y responder
Si la base de datos no está disponible, el error se propaga (503): no se entrega una
predicción sin registrarla.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nutriscore import schema
from svc_common.auth import Principal

from ..domain.model import InconsistentInputError, ModelUnavailableError, PredictionRecord
from .model_manager import ModelManager
from .ports import PredictionRepository

logger = logging.getLogger(__name__)

_RULE_MESSAGES = {rule.code: rule.message for rule in schema.CONSISTENCY_RULES}


class PredictService:
    def __init__(self, *, models: ModelManager, predictions: PredictionRepository) -> None:
        self._models = models
        self._predictions = predictions

    def predict(self, principal: Principal, features: dict[str, Any]) -> PredictionRecord:
        model = self._models.current
        if model is None:
            raise ModelUnavailableError

        frame = schema.to_feature_frame([features])
        violated = schema.consistency_violations(frame).iloc[0]
        if violated.any():
            raise InconsistentInputError(
                [
                    {"rule": code, "message": _RULE_MESSAGES[code]}
                    for code in violated[violated].index
                ]
            )

        started = time.perf_counter()
        probabilities = model.pipeline.predict_proba(frame)[0]
        inference_ms = (time.perf_counter() - started) * 1000
        classes = [str(c) for c in model.pipeline.classes_]
        by_class = {
            grade: round(float(p), 6) for grade, p in zip(classes, probabilities, strict=True)
        }
        grade = max(by_class, key=lambda g: by_class[g])

        record = self._predictions.add(
            user_id=principal.user_id,
            role=principal.role,
            model_version=model.version,
            input={k: v for k, v in features.items() if v is not None},
            grade=grade,
            probabilities=by_class,
            inference_ms=round(inference_ms, 3),
        )
        logger.info(
            "prediction",
            extra={"model_version": model.version, "grade": grade, "inference_ms": inference_ms},
        )
        return record

    def history(
        self, principal: Principal, *, limit: int, offset: int, include_all: bool = False
    ) -> list[PredictionRecord]:
        return self._predictions.list_for(
            user_id=principal.user_id,
            role=principal.role,
            limit=limit,
            offset=offset,
            include_all=include_all,
        )
