# ADR 0003: RLS nativa y un usuario de base de datos por servicio

- **Estado:** aceptada
- **Fecha:** 2026-11-18

## Contexto

Las predicciones pertenecen a un usuario y AGENTS.md pide aislarlas incluso si el frontend manipula identificadores. También exige mínimo privilegio por servicio.

## Alternativas

1. **Filtrar solo en el código (`WHERE user_id`):** un olvido expone datos ajenos.
2. **Usar un único usuario de base de datos para todo:** un servicio comprometido lo puede todo.
3. **Combinar filtro explícito, RLS forzada y usuarios separados.**

## Decisión

Opción 3, con estos elementos:

- **Usuarios:** `app_auth`, `app_ia` y `app_retrain`. Ninguno es superusuario, ni dueño de una tabla, ni tiene `BYPASSRLS`. El dueño es `app_migrator`, que solo usa el contenedor `migrate`.
- **RLS:** en `predictions` y `retrain_jobs`, con `FORCE ROW LEVEL SECURITY`.
- **Contexto:** se fija por transacción con `set_config(..., true)`.
- **Triggers:**
  - auditan cada cambio de estado de un modelo;
  - restringen quién puede activar un modelo (solo `app_ia`) y quién puede aprobarlo (solo `app_retrain`).

## Consecuencias

- **Errores del código:** la RLS los cubre, como un filtro olvidado o un ID manipulado.
- **Servicio comprometido:** la RLS no lo detiene, porque ese servicio puede fijar otro `app.user_id`. Lo limitan los permisos mínimos.
- **Transacciones:** cada consulta corre dentro de una transacción con el contexto fijado, lo que agrega un *round-trip* más por transacción.
