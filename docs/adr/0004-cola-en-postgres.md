# ADR 0004: Cola de entrenamiento en PostgreSQL (sin Redis ni Celery)

- **Estado:** aceptada
- **Fecha:** 2026-11-18

## Contexto

Un entrenamiento dura minutos y AGENTS.md prohíbe que dependa de una petición HTTP abierta. Se necesita separar la API del worker.

## Alternativas

| Alternativa | Por qué no |
|---|---|
| `BackgroundTasks` de FastAPI | Muere con la API y no deja rastro si se interrumpe |
| Redis + Celery | Dos piezas más que operar para un trabajo a la vez |
| Tabla `retrain_jobs` como cola | — (elegida) |

## Decisión

Se usa la tabla `retrain_jobs` como cola:

- **Toma de trabajos:** `SELECT … FOR UPDATE SKIP LOCKED`.
- **Un trabajo a la vez:** un índice único parcial impide dos trabajos `running` simultáneos.
- **Heartbeat:** el worker lo actualiza cada 15 s.
- **Trabajos huérfanos:** si el heartbeat tiene más de 10 minutos, el trabajo se marca como fallido y **no** se reintenta solo; un admin decide.

## Consecuencias

- No se agrega infraestructura.
- El estado de cada trabajo es auditable con SQL.
- Si en el futuro hubiera mucho volumen o prioridades, habría que pasar a una cola dedicada.
