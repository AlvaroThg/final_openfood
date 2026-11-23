#!/usr/bin/env bash
# Genera docs/database/schema.sql, el esquema completo de la base de datos.
#
# Procedimiento: levanta un PostgreSQL efímero con los mismos roles que producción, aplica las
# migraciones de Alembic desde cero y vuelca el esquema con pg_dump. Así el archivo siempre
# corresponde a las migraciones y no a una base editada a mano.
#
#   bash scripts/export_schema.sh
#
# Requiere Docker y el entorno virtual con alembic instalado (pip install -e "ml[dev]" y las
# dependencias de services/constraints.txt). En Windows usa Git Bash.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.test.yml"
DB="nutriscore_schema_export"
PY=".venv/Scripts/python"
[ -x "$PY" ] || PY=".venv/bin/python"
ALEMBIC="${PY%python}alembic"

$COMPOSE up -d --wait
$COMPOSE exec -T postgres psql -U postgres -qc "DROP DATABASE IF EXISTS $DB WITH (FORCE)"
$COMPOSE exec -T postgres psql -U postgres -qc "CREATE DATABASE $DB OWNER app_migrator"

(cd db && MIGRATIONS_DATABASE_URL="postgresql://app_migrator:test-migrator@127.0.0.1:55432/$DB" \
    "../$ALEMBIC" upgrade head)

mkdir -p docs/database
# --schema-only: sin datos. --no-owner y --no-privileges se omiten a propósito, porque los
# dueños, los permisos y las políticas RLS forman parte del diseño que se quiere documentar.
$COMPOSE exec -T postgres pg_dump -U postgres -d "$DB" --schema-only \
    > docs/database/schema.sql

$COMPOSE exec -T postgres psql -U postgres -qc "DROP DATABASE $DB WITH (FORCE)"
$COMPOSE down
echo "Generado docs/database/schema.sql ($(wc -l < docs/database/schema.sql) líneas)"
