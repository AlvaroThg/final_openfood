# API

Cada servicio publica su OpenAPI en `/openapi.json` y su Swagger en `/docs`, dentro de la red interna.

Desde el navegador, todo pasa por el proxy del frontend:

| Prefijo | Servicio |
|---|---|
| `/auth/*` | `auth_service` |
| `/api/*` | `ia_backend` |
| `/retrain/*` | `retrain_api` |

## Formato de error (común a todos los servicios)

```json
{"error": {"code": "TOKEN_EXPIRED", "message": "El token expiró.", "request_id": "…", "details": [...]}}
```

- `code` es estable: es el contrato con el frontend.
- `details` aparece en validaciones (422), en entradas inconsistentes (400) y en datasets inválidos. Nunca incluye el valor recibido.
- Cada respuesta lleva `X-Request-ID`. Si llega uno válido, se reutiliza, lo que permite seguir una operación entre servicios.

## auth_service

| Método | Ruta | Acceso | Respuesta |
|---|---|---|---|
| POST | `/auth/register` | Público (10/h por IP) | `201 {id, email, role}` · 409 `EMAIL_TAKEN` · 422 |
| POST | `/auth/login` | Público (5/min por email, 20/min por IP) | `200 {access_token, token_type, expires_in}` + cookie · 401 `INVALID_CREDENTIALS` · 429 |
| POST | `/auth/refresh` | Cookie | `200` + cookie rotada · 401 `REFRESH_INVALID` / `REFRESH_REUSED` |
| POST | `/auth/logout` | Cookie | `204` (idempotente) |
| GET | `/auth/me` | Bearer | `{id, email, role}` |
| POST | `/auth/token` | Interno (no proxificado) | Token de servicio · 401 `INVALID_CLIENT` |
| GET | `/.well-known/jwks.json` | Interno | `{keys: [{kty, n, e, kid, alg, use}]}` |
| GET | `/health` | Interno | `{status}` · 503 `DB_UNAVAILABLE` |

## ia_backend

| Método | Ruta | Acceso | Respuesta |
|---|---|---|---|
| GET | `/model/schema` | Público | Features (nombre, unidad, rango, obligatoria) y grados |
| GET | `/model/info` | user/admin | Versión activa, algoritmo, dataset y política · 503 `MODEL_UNAVAILABLE` |
| GET | `/model/metrics` | user/admin | Métricas de validación y test, y la decisión de la política |
| POST | `/predict` | user/admin (30/min por usuario) | `201 {id, grade, probabilities, model_version, inference_ms, input, created_at}` · 400 `INCONSISTENT_INPUT` · 422 · 503 |
| GET | `/predictions?limit&offset&all` | user/admin | Historial propio; `all=true` solo para admin (403 si no) |
| GET | `/admin/models` | admin | Todas las versiones con estado, F1 y decisión |
| POST | `/admin/models/{v}/activate` | admin | `{version, changed, previous}` · 404 · 409 `MODEL_NOT_ACTIVATABLE` · 422 `MODEL_ARTIFACT_INVALID` |
| POST | `/internal/models/{v}/activate` | Token de servicio (`retrain_service`) | Igual que la anterior; idempotente |
| GET | `/health` | Interno | `{status, model_version}` · 503 `MODEL_LOAD_FAILED` / `DB_UNAVAILABLE` |

## retrain_api (todo requiere rol admin)

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/retrain/configs` | Configuraciones permitidas (`decision_tree`, `random_forest`, `lightgbm`) |
| GET/POST | `/retrain/datasets` | Lista · `201` registra un parquet del volumen · 404 · 409 · 422 `INVALID_DATASET` |
| GET/POST | `/retrain/jobs` | Lista · `202` encola · 404 `DATASET_NOT_FOUND` · 422 `UNKNOWN_CONFIG` |
| GET | `/retrain/jobs/{id}` | Estado: `queued`/`running`/`succeeded`/`failed`, versión y activación |

`tests/contracts/` fija las rutas públicas de `auth_service` y el formato del JWT. Un cambio de contrato rompe esas pruebas a propósito.
