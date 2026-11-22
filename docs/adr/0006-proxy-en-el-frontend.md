# ADR 0006: El frontend como único punto expuesto (proxy con lista blanca)

- **Estado:** aceptada
- **Fecha:** 2026-11-22

## Contexto

El navegador necesita hablar con `auth_service`, `ia_backend` y `retrain_api`. El refresh token viaja en una cookie que debe ser de primera parte.

## Alternativas

1. **Publicar cada servicio en su propio puerto y configurar CORS con credenciales:** más superficie expuesta y cookies entre orígenes.
2. **Agregar un gateway (nginx o Traefik):** un contenedor más.
3. **Usar los `rewrites` de Next.js** con una lista explícita de rutas.

## Decisión

Opción 3:

- **Un solo origen:** `localhost:3000`, sin CORS.
- **Lista blanca:** solo se reenvían las rutas que usa la interfaz. Nunca `/internal/*`, `/auth/token` ni el JWKS (verificado en la prueba E2E: 404).
- **IP real del cliente:** `auth_service` confía en un salto de `X-Forwarded-For` (`TRUSTED_PROXY_HOPS=1`) para su rate limit por IP.

## Consecuencias

- Los servicios internos y Postgres no se publican en ningún puerto del host.
- El frontend queda como pieza de entrada. Si se cae, la API no es accesible desde afuera, lo cual es aceptable en este contexto.
