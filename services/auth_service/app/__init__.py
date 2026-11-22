"""auth_service: registro, login, sesiones (refresh tokens), JWT RS256 y JWKS.

Es el ÚNICO servicio con la clave privada del JWT y con acceso a las contraseñas.

Capas (dependencias solo hacia adentro):
    api/             FastAPI: rutas, cookies, rate limit y traducción de errores a HTTP
    schemas/         modelos Pydantic de entrada/salida (contrato HTTP)
    application/     casos de uso (AuthService) y puertos (Protocols)
    domain/          entidades, reglas e errores de negocio; sin dependencias externas
    infrastructure/  PostgreSQL, argon2 y firma RS256 (implementan los puertos)
    main.py          raíz de composición: arma todo a partir de la configuración

Los imports internos son relativos para que el paquete funcione como `app` en el contenedor y
como `services.auth_service.app` en las pruebas.
"""
