# retrain_service: UNA imagen, dos procesos (API y worker), más el comando bootstrap.
# Contexto de build: la raíz del repositorio.
#   API:       uvicorn retrain_service.app.main:create_app --factory --host 0.0.0.0 --port 8000
#   worker:    python -m retrain_service.worker.main
#   bootstrap: python -m retrain_service.worker.bootstrap
FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY services/constraints.txt /tmp/constraints.txt
COPY services/retrain_service/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt -c /tmp/constraints.txt

COPY ml/pyproject.toml /tmp/ml/pyproject.toml
COPY ml/nutriscore /tmp/ml/nutriscore
RUN pip install /tmp/ml && rm -rf /tmp/ml
# Configuraciones de entrenamiento versionadas (las mismas del notebook).
COPY ml/configs /srv/ml/configs

COPY services/common/svc_common ./svc_common
COPY services/retrain_service/app ./retrain_service/app
COPY services/retrain_service/worker ./retrain_service/worker

# Carpetas de los volúmenes, creadas con el dueño correcto: un volumen nuevo hereda estos permisos.
RUN useradd --system --uid 10001 --no-create-home appuser \
    && mkdir -p /models /datasets \
    && chown appuser /models /datasets
USER appuser

EXPOSE 8000
CMD ["uvicorn", "retrain_service.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
