# Ejecución local con Docker Compose

El sistema se ejecuta en local, en cualquier computadora con Docker. No se publica en internet: no hay dominio, TLS público ni Dokploy (ver [ADR 0005](adr/0005-ejecucion-local.md)).

## Primer arranque

1. `python scripts/init_env.py` genera el `.env` con contraseñas aleatorias y muestra la del administrador.
   - Se ejecuta una sola vez.
   - El archivo `.env` está en `.gitignore`.
2. `docker compose up -d --build` construye y levanta los servicios en este orden:

   | Servicio | Qué hace |
   |---|---|
   | `postgres` | Arranca y crea los roles con `db/initdb` |
   | `migrate` | Aplica las migraciones de Alembic y termina |
   | `keygen` | Crea la clave privada del JWT dentro del volumen `jwt_keys` y termina |
   | `auth_service` | Arranca y crea el administrador inicial |
   | `ia_backend`, `retrain_api`, `retrain_worker`, `frontend` | Arrancan después |

3. Coloca los datos en `./data/raw/` (TSV de Kaggle) o en `./data/processed/clean_v1.parquet` (desde el notebook).
4. `docker compose --profile bootstrap run --rm bootstrap` hace todo el ciclo inicial:
   1. limpia o copia el dataset;
   2. lo registra;
   3. encola un entrenamiento LightGBM y espera al worker;
   4. el worker evalúa con la política `qp-1` y, si el modelo aprueba, pide la activación a `ia_backend`.
5. Abre http://localhost:3000.

Si el modelo **no** aprueba la política de calidad, queda en estado `rejected` y no se activa. No hay atajos: el detalle está en la sección *Modelo* y en `GET /retrain/jobs`.

## Operación

| Tarea | Comando |
|---|---|
| Ver el estado | `docker compose ps` |
| Logs (JSON, con `request_id`) | `docker compose logs -f ia_backend` |
| Detener (conserva datos) | `docker compose down` |
| Reentrenar | Administración → *Entrenar*, o `POST /retrain/jobs` |
| Activar una versión o volver atrás (rollback) | Administración → *Activar* / *Volver a esta* |
| Backup | `bash scripts/backup_db.sh` → `backups/nutriscore_<fecha>.dump` |
| Restaurar | `bash scripts/restore_db.sh backups/<archivo>.dump` |
| Borrar todo (datos incluidos) | `docker compose down -v` y después borrar `.env` |

Los modelos viven en el volumen `models`:

- `retrain_service` escribe en él;
- `ia_backend` lo monta en solo lectura.

Los datasets procesados viven en el volumen `datasets`.

## Recursos

Límites de memoria definidos en `docker-compose.yml`:

| Servicio | Límite |
|---|---|
| `postgres` | 512 MB |
| `auth_service` | 256 MB |
| `ia_backend` | 768 MB |
| `retrain_api` | 1 GB |
| `retrain_worker` | 3 GB |
| `frontend` | 256 MB |

Si el worker se queda sin memoria al entrenar con el dataset completo, muere solo ese contenedor:

- la predicción sigue funcionando;
- el trabajo se marca como fallido al reiniciarse el worker.

> Los límites son estimaciones iniciales y no se midieron con el dataset completo. Pendiente: medirlos con `docker stats` durante un entrenamiento real.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| Al recargar la página se pierde la sesión | El navegador no acepta cookies `Secure` en `http://localhost`. Pon `COOKIE_SECURE=false` en `.env` y ejecuta `docker compose up -d auth_service`. |
| `/predict` responde *Todavía no hay un modelo activo* | Falta el bootstrap, o el modelo fue rechazado por la política de calidad. |
| `ia_backend` no queda *healthy* (`MODEL_LOAD_FAILED`) | El artefacto activo no pasó la verificación (hash, dependencias o golden). Activa otra versión desde Administración. |
| El bootstrap dice *No hay datos* | Falta el TSV en `data/raw/` o el parquet en `data/processed/`. |
| `docker compose up` pide variables | No existe el `.env`: ejecuta `python scripts/init_env.py`. |
