# Seguridad

## Activos y amenazas

| Activo | Amenaza | Controles |
|---|---|---|
| Cuentas | Fuerza bruta y enumeración | argon2id; rate limit en login (5/min por email, 20/min por IP) y en registro (10/h por IP); error genérico en el login; verificación ficticia cuando el email no existe |
| Sesiones | Robo del token (XSS, filtración) | Access token de 15 min solo en memoria; refresh opaco en cookie `httpOnly; SameSite=Strict; Path=/auth; Secure`; rotación en cada uso; reuso → se revoca la familia |
| Clave de firma | Falsificar tokens | RSA de 3072 bits generada dentro del volumen `jwt_keys`, montada **solo** en `auth_service` y en solo lectura; los demás servicios solo tienen la clave pública (JWKS) |
| Historial de predicciones | Ver datos de otro usuario | Filtro explícito por usuario + RLS forzada (`FORCE ROW LEVEL SECURITY`) |
| Modelos | Artefacto manipulado (pickle → ejecución de código) | SHA-256 verificado **antes** de `joblib.load`, dependencias exactas, golden; volumen en solo lectura para `ia_backend` |
| Activación | Activar un modelo no aprobado | Solo `ia_backend` activa; un trigger en la base impide a cualquier otro rol pasar un modelo a `active`/`archived`; el endpoint interno exige un token de servicio con audiencia `nutriscore-internal` y un cliente permitido |
| Base de datos | Acceso directo o servicio comprometido | Sin puerto publicado; red interna; un usuario por servicio sin `SUPERUSER`/`BYPASSRLS` y sin ser dueño de ninguna tabla; nadie tiene `DELETE` |
| Secretos | Filtración por Git o por logs | `.env` generado (en `.gitignore`); logs sin tokens ni contraseñas (y redacción de seguridad); los errores no reflejan la entrada recibida |

## JWT

| Tipo | Contenido | Duración |
|---|---|---|
| Access token de usuario | `iss=nutriscore-auth`, `aud=nutriscore-api`, `sub`, `role` (`user`/`admin`), `iat`, `exp`, `jti`; header con `kid` | 15 min |
| Token de servicio | `aud=nutriscore-internal`, `role=service`, `sub=<client_id>` | 5 min |

**Verificación (`svc_common.tokens.decode_token`, igual en todos los servicios):**

- **Algoritmo:** exactamente RS256. Se rechazan `alg=none` y HS256 firmado con la clave pública como secreto (confusión de algoritmo).
- **Firma y `kid`:** la firma se verifica con la clave correspondiente al `kid` del header.
- **Claims obligatorios:** deben estar presentes y `iss`/`aud` deben coincidir.
- **Rol y audiencia:** el rol debe corresponder a la audiencia; un token de usuario no sirve en `/internal` y uno de servicio no sirve en la API.

**JWKS:**

- se cachea 5 minutos;
- se vuelve a pedir ante un `kid` desconocido (rotación de claves), como máximo una vez cada 10 s;
- si `auth_service` está caído, se usa la caché hasta 1 hora; después se responde 503 `AUTH_UNAVAILABLE`.

## RLS: modelo de amenazas

- **Contexto:** se fija por transacción con `set_config('app.user_id' | 'app.role' | 'app.request_id', …, true)`. El `true` hace que muera con la transacción, así que una conexión reutilizada del pool nunca hereda el usuario anterior (hay una prueba para esto).
- **Contra qué protege:** errores del código, como una consulta sin filtro o un ID manipulado.
- **Contra qué no protege:** un servicio comprometido, que puede fijar cualquier `app.user_id`. Ahí protegen los permisos mínimos: por ejemplo, `app_ia` no puede leer `users` ni modificar o borrar predicciones.
- **Primera capa:** la autorización en la aplicación. El historial filtra por usuario explícitamente, y solo un admin puede pedir el de todos.

## Superficie expuesta

- **Puertos:** solo `127.0.0.1:3000`, el frontend.
- **Proxy:** reenvía una lista blanca de rutas. `/internal/*`, `/auth/token` y el JWKS no son accesibles desde el navegador; la prueba E2E lo verifica (404).
- **Red interna `backend`:** tiene `internal: true`, así que los servicios no tienen salida a internet.
- **Contenedores:** corren con un usuario sin privilegios (uid 10001 en Python, `node` en el frontend).

## Límites conocidos y aceptados

| Límite | Consecuencia o mitigación |
|---|---|
| Rate limit en memoria | Se pierde al reiniciar y sirve para una sola réplica |
| Rotación estricta del refresh token | Dos **pestañas** que refrescan a la vez cierran la sesión; dentro de una pestaña, el cliente serializa los refrescos |
| `409 EMAIL_TAKEN` en el registro | Revela qué emails existen; se acepta por usabilidad y lo acota el rate limit |
| HTTP sin TLS en localhost | Aceptable en local; para exponerlo en una red habría que poner TLS delante del frontend |
| Sin rotación automática de la clave JWT | Manual: borrar el volumen `jwt_keys` y reiniciar, lo que cierra todas las sesiones |
