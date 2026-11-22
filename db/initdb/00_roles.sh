#!/bin/sh
# Crea los usuarios de base de datos de la plataforma. Postgres ejecuta este script UNA sola vez,
# cuando el volumen de datos está vacío (docker-entrypoint-initdb.d). Solo crea roles: el esquema,
# los permisos y la RLS los aplica Alembic (contenedor `migrate`).
#
#   app_migrator  dueño de la base y de las tablas; solo lo usa `migrate`
#   app_auth      auth_service
#   app_ia        ia_backend
#   app_retrain   retrain_service (API y worker)
#
# Ninguno es superusuario, ninguno puede crear roles ni bases y ninguno tiene BYPASSRLS.
# Los servicios no son dueños de ninguna tabla, así que la RLS siempre se les aplica.
#
# El entrypoint de Postgres puede ejecutar este archivo o leerlo con `.`; por eso no usa
# `set -u` ni `exit`. Los `:?` detienen la inicialización si falta una contraseña.

: "${APP_MIGRATOR_DB_PASSWORD:?Falta APP_MIGRATOR_DB_PASSWORD}"
: "${APP_AUTH_DB_PASSWORD:?Falta APP_AUTH_DB_PASSWORD}"
: "${APP_IA_DB_PASSWORD:?Falta APP_IA_DB_PASSWORD}"
: "${APP_RETRAIN_DB_PASSWORD:?Falta APP_RETRAIN_DB_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v dbname="$POSTGRES_DB" \
    -v migrator_pw="$APP_MIGRATOR_DB_PASSWORD" \
    -v auth_pw="$APP_AUTH_DB_PASSWORD" \
    -v ia_pw="$APP_IA_DB_PASSWORD" \
    -v retrain_pw="$APP_RETRAIN_DB_PASSWORD" <<'SQL'
CREATE ROLE app_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'migrator_pw';
CREATE ROLE app_auth     LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'auth_pw';
CREATE ROLE app_ia       LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'ia_pw';
CREATE ROLE app_retrain  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'retrain_pw';

-- El dueño de la base es dueño del esquema public (pg_database_owner, PostgreSQL 15+):
-- así app_migrator puede crear las tablas sin ser superusuario.
ALTER DATABASE :"dbname" OWNER TO app_migrator;
SQL
