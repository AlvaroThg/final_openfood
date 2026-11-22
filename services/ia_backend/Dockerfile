# ia_backend. Contexto de build: la raíz del repositorio (necesita ml/, services/common y constraints).
#   docker build -f services/ia_backend/Dockerfile -t nutriscore/ia_backend .
FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# LightGBM necesita la biblioteca de OpenMP.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY services/constraints.txt /tmp/constraints.txt
COPY services/ia_backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt -c /tmp/constraints.txt

# Paquete nutriscore con las MISMAS versiones exactas de ML que el entrenamiento (ml/pyproject.toml):
# si difieren, la carga del artefacto falla a propósito.
COPY ml/pyproject.toml /tmp/ml/pyproject.toml
COPY ml/nutriscore /tmp/ml/nutriscore
RUN pip install /tmp/ml && rm -rf /tmp/ml

COPY services/common/svc_common ./svc_common
COPY services/ia_backend/app ./app

RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status != 200)"]

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
