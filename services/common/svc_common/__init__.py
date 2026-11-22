"""Piezas transversales de los servicios Python (auth_service, ia_backend, retrain_service).

Solo contiene lo que los tres necesitan igual; ninguna regla de negocio vive aquí.

    config      lectura de variables de entorno con validación temprana (fail fast)
    logging     logs JSON con request_id y redacción de campos sensibles
    http        middleware de X-Request-ID + log de acceso, formato único de errores
    tokens      contrato del JWT (RS256, issuer, audiencias, roles) y su verificación
    rate_limit  limitador en memoria por clave (ventana deslizante)
    db          pool de PostgreSQL y transacción con el contexto de RLS

No es un paquete instalable: cada Dockerfile copia esta carpeta junto al servicio y pytest
la agrega al path (pyproject.toml). Las versiones de sus dependencias se fijan en
services/constraints.txt.
"""
