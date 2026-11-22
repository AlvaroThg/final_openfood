# Base de datos

PostgreSQL 16. El esquema se crea y evoluciona con **Alembic**:

| Ubicación | Contenido |
|---|---|
| `db/initdb/00_roles.sh` | Solo los roles. Corre una vez, con el volumen vacío. |
| `db/migrations/versions/0001_schema.py` | Tablas, restricciones e integridad del ciclo de vida del modelo |
| `db/migrations/versions/0002_security.py` | Permisos, RLS y guardia de roles |

## Tablas

| Tabla | Claves e invariantes | Quién escribe |
|---|---|---|
| `users` | email único y normalizado (`CHECK`); rol `user`/`admin` | `app_auth` |
| `refresh_tokens` | `token_hash` único (SHA-256); `family_id`; motivo de revocación (`CHECK`); `replaced_by` solo en rotaciones | `app_auth` |
| `datasets` | versión `vN` única; `sha256`; `row_count > 0`; inmutable (nadie tiene `UPDATE`) | `app_retrain` |
| `model_versions` | versión semver única; FK a `datasets`; estado con `CHECK`; **un solo `active`** (índice único parcial); evaluación completa para `approved`, `active` y `archived` (`CHECK`) | `app_retrain` (crear/evaluar), `app_ia` (solo `status`) |
| `model_version_events` | Auditoría de cada cambio de estado: rol de BD, usuario y `request_id` | Solo el trigger (`SECURITY DEFINER`) |
| `retrain_jobs` | Estado con `CHECK`; un solo `running` (índice único parcial); `failed` ⇒ `error` | `app_retrain` + RLS |
| `predictions` | FK a `users` y a `model_versions(version)`; grado A–E; JSON objeto; índice `(user_id, created_at desc)`; inmutable | `app_ia` + RLS |

## Triggers

- **`model_versions_integrity`:**
  - una versión nueva nace en `candidate`;
  - solo se permiten las transiciones de `nutriscore/lifecycle.py` (una prueba compara las 36 combinaciones con Python);
  - la identidad de la versión y su evaluación son inmutables una vez cerrada.
- **`model_versions_role_guard`:**
  - solo `app_ia` puede poner un modelo en `active`/`archived`;
  - solo `app_retrain` puede ponerlo en `evaluating`/`approved`/`rejected`.
- **`model_versions_log_*`:** escriben `model_version_events`. Ningún servicio puede insertar ni falsificar eventos.

## Roles y permisos

| Tabla | `app_auth` | `app_ia` | `app_retrain` |
|---|---|---|---|
| `users`, `refresh_tokens` | S I U | — | — |
| `datasets` | — | S | S I |
| `model_versions` | — | S, U(`status`) | S I, U(`status` + evaluación) |
| `model_version_events` | — | S | S |
| `retrain_jobs` | — | — | S I U (RLS: admin/service) |
| `predictions` | — | S I (RLS: propias, o todas si es admin) | — |

S = SELECT, I = INSERT, U = UPDATE. Nadie tiene `DELETE`: el historial no se borra.

`app_migrator` es el dueño de todo y solo lo usa el contenedor `migrate`. Ningún rol es superusuario ni tiene `BYPASSRLS`. Las pruebas de `tests/db/test_privileges.py` verifican la matriz, incluidas las negaciones.

## Migraciones

```bash
docker compose run --rm migrate                      # aplica las pendientes (también al levantar)
# Desarrollo, contra la base de pruebas:
cd db && MIGRATIONS_DATABASE_URL=postgresql://app_migrator:...@127.0.0.1:55432/<db> alembic upgrade head
```

- Cada migración tiene `downgrade`. `tests/db/test_migrations.py` prueba la secuencia `upgrade → downgrade base → upgrade`.
- **Un cambio de esquema = una migración nueva.** Nunca se edita una migración ya aplicada, y no se corrige producción a mano.
- Antes de aplicar una migración destructiva, se hace un backup (`scripts/backup_db.sh`).

## Backups

| Script | Qué hace |
|---|---|
| `scripts/backup_db.sh` | `pg_dump` en formato custom; luego verifica el archivo con `pg_restore --list` |
| `scripts/restore_db.sh` | `pg_restore --clean --single-transaction`; pide confirmación |

**Probado el 22/11/2026:**

1. Se hizo un backup y se vació la tabla.
2. Se restauró el backup.
3. Volvieron los datos, las 3 políticas RLS, `FORCE RLS`, los 4 triggers y el dueño `app_migrator`.
