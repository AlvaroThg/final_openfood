#!/usr/bin/env bash
# Backup lógico de la base (formato custom de pg_dump: comprimido, restaurable por objeto).
#   bash scripts/backup_db.sh            → backups/nutriscore_<AAAAMMDD-HHMMSS>.dump
# Variable opcional COMPOSE para otro proyecto o archivo, p. ej.
#   COMPOSE="docker compose -p nutriscore_e2e" bash scripts/backup_db.sh
# Los modelos (volumen `models`) se respaldan aparte: son archivos inmutables por versión.
set -euo pipefail

COMPOSE=${COMPOSE:-docker compose}
DB=${DB:-nutriscore}
mkdir -p backups
target="backups/${DB}_$(date +%Y%m%d-%H%M%S).dump"

$COMPOSE exec -T postgres pg_dump -U postgres -d "$DB" --format=custom > "$target"
# Verificación: el archivo debe poder listarse (si está corrupto, pg_restore falla).
$COMPOSE exec -T postgres pg_restore --list < "$target" > /dev/null
echo "Backup verificado: $target ($(wc -c < "$target") bytes)"
