#!/usr/bin/env bash
# Restaura un backup creado con backup_db.sh sobre la base existente (reemplaza su contenido).
#   bash scripts/restore_db.sh backups/nutriscore_20261122-120000.dump
# Recomendado: detener antes los servicios que escriben (docker compose stop auth_service
# ia_backend retrain_api retrain_worker) y volver a levantarlos al terminar.
set -euo pipefail

file=${1:?Uso: restore_db.sh <archivo.dump>}
COMPOSE=${COMPOSE:-docker compose}
DB=${DB:-nutriscore}
[ -f "$file" ] || { echo "No existe $file" >&2; exit 1; }

read -r -p "Esto REEMPLAZA los datos de '$DB'. Escribe 'restaurar' para continuar: " answer
[ "$answer" = "restaurar" ] || { echo "Cancelado."; exit 1; }

# --clean --if-exists: borra y recrea cada objeto; conserva dueños, permisos, políticas RLS
# y triggers tal como estaban en el backup. --single-transaction: o todo o nada.
$COMPOSE exec -T postgres pg_restore -U postgres -d "$DB" --clean --if-exists \
    --single-transaction < "$file"
echo "Restaurado $file en $DB"
