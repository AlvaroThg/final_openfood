"""ia_backend: inferencia, historial de predicciones y activación de versiones aprobadas.

NO entrena (eso es retrain_service). Solo sirve modelos aprobados, cuyo artefacto verifica
(SHA-256 antes de deserializar, versiones de dependencias y golden) antes de usarlos.

Capas:
    api/             FastAPI: rutas, autorización por rol, rate limit, errores HTTP
    schemas/         contrato HTTP (la entrada de /predict se genera desde nutriscore.schema)
    application/     ModelManager (modelo activo y activación) y PredictService
    domain/          registros y errores de negocio
    infrastructure/  PostgreSQL con RLS y carga de artefactos desde el volumen de modelos
"""
