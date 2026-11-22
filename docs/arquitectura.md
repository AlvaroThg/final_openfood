# Arquitectura

## Servicios

Cada servicio es un proceso con un solo motivo de cambio. Los servicios se comunican **solo por HTTP** sobre una red interna de Docker. Cada uno usa **su propio usuario de base de datos**, con los permisos mínimos que necesita.

| Servicio | Hace | No hace |
|---|---|---|
| `frontend` (Next.js) | Interfaz y proxy de rutas explícitas | Reglas de negocio, acceso a la base de datos, guardar tokens en `localStorage` |
| `auth_service` (FastAPI) | Cuentas, contraseñas (argon2id), sesiones, firma de JWT RS256, JWKS, tokens de servicio | Predecir ni conocer modelos |
| `ia_backend` (FastAPI) | Validar la entrada, predecir, guardar el historial, activar versiones **aprobadas** tras verificarlas | Entrenar ni escribir modelos (monta el volumen en solo lectura) |
| `retrain_api` (FastAPI) | Registrar datasets y encolar trabajos (solo admin) | Entrenar dentro de una petición |
| `retrain_worker` | Tomar trabajos de la cola, entrenar, evaluar con la política de calidad, registrar la versión, **pedir** la activación | Activar ni atender predicciones |
| `postgres` | Persistencia, integridad (`CHECK`, FK, triggers) y RLS | — |

Además hay tres contenedores de una sola ejecución:

- `migrate`: aplica las migraciones de Alembic;
- `keygen`: genera la clave privada del JWT dentro de un volumen;
- `bootstrap`: genera el primer modelo por el flujo normal.

```
                      127.0.0.1:3000
 navegador ─────────► frontend ── red "edge" (única con puerto publicado)
                         │ proxy: /auth/{register,login,refresh,logout,me}
                         │        /api/{predict,predictions,model/*,admin/models*}
                         │        /retrain/{configs,datasets,jobs*}
 ════════════════════════╪═══════ red "backend" (internal: sin salida ni puertos) ═════
          ┌──────────────┼───────────────────┐
          ▼              ▼                   ▼
    auth_service     ia_backend ◄──────── retrain_worker ◄── cola ── retrain_api
     │  JWKS ──────►  │  (verifica)   POST /internal/models/{v}/activate
     │                │                (token de servicio, aud nutriscore-internal)
     ▼                ▼                   ▼
   ┌───────────────────────────────────────────────────┐
   │ postgres · app_auth · app_ia · app_retrain · RLS  │
   └───────────────────────────────────────────────────┘
     volúmenes: pgdata · models (rw: retrain, ro: ia) · datasets · jwt_keys (solo auth)
```

## Código compartido

**`ml/nutriscore`** es el único lugar con el contrato de datos y el pipeline de ML. Contiene:

- las features, rangos y reglas físicas;
- la limpieza, las particiones, el entrenamiento y la evaluación;
- la política de calidad y el ciclo de vida del modelo;
- los artefactos.

Lo usan el notebook, el worker y `ia_backend`, así que lo que se valida al entrenar es exactamente lo que se valida al predecir.

**`services/common/svc_common`** contiene la infraestructura transversal: logs JSON, `X-Request-ID`, formato de errores, verificación de JWT, cliente JWKS, rate limit y transacción con contexto de RLS. No contiene reglas de negocio.

## Organización interna (hexagonal)

| Capa | Contenido |
|---|---|
| `domain/` | Entidades, invariantes y errores de negocio. Sin dependencias externas. |
| `application/` | Casos de uso. Dependen de puertos (`Protocol`), no de Postgres ni de FastAPI. |
| `infrastructure/` | Postgres, argon2, RS256, sistema de archivos y HTTP saliente. |
| `api/` + `schemas/` | FastAPI: rutas, autorización por rol y traducción de errores. |
| `container.py` | Raíz de composición: el único lugar que conoce las implementaciones concretas. |

Gracias a esta separación, los casos de uso se prueban con implementaciones en memoria, y la integración se prueba con Postgres real.

## Flujos

### Login y sesión

1. `POST /auth/login` → access token de 15 min en el cuerpo y refresh token opaco en una cookie `httpOnly; SameSite=Strict; Path=/auth`.
2. El frontend guarda el access token **en memoria**.
3. Si una llamada recibe 401 por token vencido, el cliente hace un único `POST /auth/refresh` (compartido entre llamadas simultáneas) y reintenta.
4. Cada refresh rota el token. Si alguien reutiliza uno ya rotado, se revoca toda la familia ([ADR 0002](adr/0002-jwt-rs256-y-refresh-rotado.md)).

### Predicción

1. El frontend envía `POST /api/predict`, que el proxy reenvía a `ia_backend`.
2. `ia_backend` verifica el JWT con el JWKS cacheado (firma, `iss`, `aud`, `exp` y rol).
3. Pydantic valida la entrada, con un modelo generado desde `nutriscore.schema`.
4. Se aplican las reglas físicas (`consistency_violations`). Si se violan → 400.
5. Se ejecuta `predict_proba` con el modelo activo en memoria.
6. En una transacción con `app.user_id` fijado, se inserta la predicción con la versión **exacta** del modelo. Si la base de datos no responde → 503: no se entrega una predicción sin registrarla.

### Reentrenamiento y activación

1. Un admin llama a `POST /retrain/jobs`. La API responde `202` y el trabajo queda en estado `queued`.
2. El worker toma el trabajo con `FOR UPDATE SKIP LOCKED`, verifica el SHA-256 del dataset y marca la versión `candidate → evaluating`.
3. Entrena con *train*, decide con *validación* y reporta *test*. Guarda el artefacto inmutable con su `golden.json`.
4. Aplica la política de calidad `qp-1` → `approved` o `rejected`, guardando métricas, hash y decisión.
5. Si la versión quedó `approved`, el worker obtiene un token de servicio y llama a `POST /internal/models/{v}/activate` (timeout de 30 s y como máximo 3 intentos).
6. `ia_backend` hace la activación:
   1. verifica el estado de la versión;
   2. carga el artefacto: hash **antes** de deserializar, dependencias exactas y golden;
   3. en una transacción, archiva el modelo activo anterior y activa el nuevo;
   4. cambia la referencia en memoria.

   Si algo falla, sigue sirviendo el modelo anterior.
7. Rollback: un admin activa una versión `archived` desde Administración.

## Decisiones

Están en `docs/adr/`:

| ADR | Tema |
|---|---|
| [0001](adr/0001-servicios-monorepo.md) | Servicios y monorepo |
| [0002](adr/0002-jwt-rs256-y-refresh-rotado.md) | JWT RS256 y refresh rotado |
| [0003](adr/0003-rls-y-roles-por-servicio.md) | RLS y roles por servicio |
| [0004](adr/0004-cola-en-postgres.md) | Cola en Postgres |
| [0005](adr/0005-ejecucion-local.md) | Ejecución local |
| [0006](adr/0006-proxy-en-el-frontend.md) | Proxy en el frontend |
