# ADR 0002: JWT RS256 con JWKS y refresh tokens opacos rotados

- **Estado:** aceptada
- **Fecha:** 2026-11-18

## Contexto

Varios servicios deben verificar la identidad del usuario. Solo uno debería poder emitir tokens.

## Alternativas

| Alternativa | Problema |
|---|---|
| HS256 con un secreto compartido | Todo verificador también puede firmar |
| Sesiones en la base de datos consultadas por cada servicio | Acopla todos los servicios a la tabla de sesiones |
| Proveedor externo (Supabase, Keycloak) | Descartado por la consigna y por el costo de operarlo |

## Decisión

- **Firma:** `auth_service` firma con RS256 y publica la clave pública en el JWKS. Los demás servicios solo verifican.
- **Access token:** dura 15 min y el frontend lo guarda en memoria.
- **Refresh token:**
  - opaco, de 256 bits;
  - en la base de datos se guarda solo su SHA-256;
  - viaja en una cookie `httpOnly; SameSite=Strict; Path=/auth`;
  - se rota en cada uso;
  - si alguien reutiliza uno ya rotado, se revoca toda su familia.
- **Servicios:** usan client credentials y reciben un token con audiencia distinta (`nutriscore-internal`).

## Consecuencias

- Un servicio comprometido no puede fabricar tokens.
- El robo de un refresh token se detecta en el primer reuso.
- La rotación estricta cierra la sesión si dos pestañas refrescan a la vez. Dentro de una pestaña, el cliente lo evita con un único refresco en vuelo.
- Revocar un access token antes de que venza no es posible; por eso dura solo 15 minutos.
