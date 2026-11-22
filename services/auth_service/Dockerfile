# auth_service. Contexto de build: la raíz del repositorio (necesita services/common y constraints).
#   docker build -f services/auth_service/Dockerfile -t nutriscore/auth_service .
FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Primero las dependencias (capa cacheable), después el código.
COPY services/constraints.txt /tmp/constraints.txt
COPY services/auth_service/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt -c /tmp/constraints.txt

COPY services/common/svc_common ./svc_common
COPY services/auth_service/app ./app

# Usuario sin privilegios. /keys es el punto de montaje del volumen jwt_keys: creado con este
# dueño, un volumen nuevo lo hereda y `keygen` puede escribir la clave (0600) ahí.
RUN useradd --system --uid 10001 --no-create-home appuser     && mkdir -p /keys && chown appuser /keys && chmod 700 /keys
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status != 200)"]

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
