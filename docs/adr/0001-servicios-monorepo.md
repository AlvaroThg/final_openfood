# ADR 0001: Servicios separados en un monorepo

- **Estado:** aceptada
- **Fecha:** 2026-11-18

## Contexto

La consigna pide separar el backend del frontend, no usar MVC y tener un servicio de predicción. Además, el entrenamiento consume mucha memoria y no debe afectar a la predicción.

## Alternativas

1. **Un backend monolítico** (por ejemplo MVC): simple, pero el entrenamiento compite con la predicción y la clave del JWT convive con la inferencia.
2. **Servicios en repositorios separados:** versionado independiente, pero el paquete `nutriscore` habría que publicarlo y sincronizarlo en cada repositorio.
3. **Servicios en un monorepo:** un solo repositorio con varios procesos independientes.

## Decisión

Opción 3, con cinco servicios: `frontend`, `auth_service`, `ia_backend`, `retrain_service` (API + worker) y `postgres`.

- Cada uno tiene su Dockerfile, su usuario de base de datos y su contrato HTTP.
- Comparten código solo a través de dos paquetes:
  - `nutriscore`: el contrato de datos y el pipeline de ML;
  - `svc_common`: infraestructura transversal, sin reglas de negocio.

## Consecuencias

**A favor:**

- Un cambio de contrato se hace en un solo commit.
- Hay una sola CI.
- Si el entrenamiento se queda sin memoria, muere solo el worker.

**En contra:** más contenedores y contratos HTTP que mantener.
