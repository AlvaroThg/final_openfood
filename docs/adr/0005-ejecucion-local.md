# ADR 0005: Ejecución local con Docker Compose (sin Dokploy)

- **Estado:** aceptada. Reemplaza el despliegue en Dokploy previsto en la Fase 3.
- **Fecha:** 2026-11-19

## Contexto

El requisito cambió: el sistema no necesita publicarse ni tener dominio. Debe funcionar en local, en cualquier computadora.

AGENTS.md (§2, §11 y §13) todavía menciona Dokploy y `docs/despliegue-dokploy.md`.

## Alternativas

1. **Mantener Dokploy:** exige un VPS, un dominio y TLS que ya no se necesitan.
2. **Instalar cada pieza a mano:** no es reproducible.
3. **Docker Compose local.**

## Decisión

Opción 3. Un `docker-compose.yml` con:

- red interna sin salida a internet;
- solo el frontend publicado, en `127.0.0.1`;
- secretos generados por `scripts/init_env.py`;
- la clave privada del JWT generada dentro de un volumen por el contenedor `keygen`.

La documentación de despliegue pasa a ser `docs/despliegue-local.md`.

## Consecuencias

**Se elimina:** Traefik, Let's Encrypt y la configuración del VPS.

**Se mantiene:** el mismo archivo de compose sigue siendo compatible con Dokploy si en el futuro se quisiera publicar. En ese caso habría que agregar TLS delante del frontend.

**La cookie `Secure`:** funciona en `http://localhost` en los navegadores modernos. Si alguno no la acepta, existe `COOKIE_SECURE=false`.
