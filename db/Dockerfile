# Contenedor `migrate`: aplica las migraciones de Alembic con el usuario app_migrator y termina.
# Contexto de build: la raíz del repositorio.
#   docker build -f db/Dockerfile -t nutriscore/migrate .
FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/db

COPY services/constraints.txt /tmp/constraints.txt
COPY db/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt -c /tmp/constraints.txt

COPY db/alembic.ini ./alembic.ini
COPY db/migrations ./migrations

RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

# Requiere MIGRATIONS_DATABASE_URL=postgresql://app_migrator:<clave>@postgres:5432/nutriscore
CMD ["alembic", "upgrade", "head"]
