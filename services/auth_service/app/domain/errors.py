"""Errores de negocio de la autenticación. La capa api/ los traduce a códigos HTTP."""


class AuthError(Exception):
    """Base de los errores de negocio de auth_service."""


class InvalidEmailError(AuthError):
    """El email no tiene un formato válido."""


class WeakPasswordError(AuthError):
    """La contraseña no cumple la política (longitud)."""


class EmailTakenError(AuthError):
    """Ya existe una cuenta con ese email."""


class InvalidCredentialsError(AuthError):
    """Login fallido. Mismo error para email inexistente, contraseña incorrecta o cuenta
    desactivada: no se revela cuál fue la causa."""


class RefreshTokenInvalidError(AuthError):
    """Refresh token ausente, desconocido o expirado."""


class RefreshTokenReusedError(AuthError):
    """Se presentó un refresh token ya revocado: posible robo. Se revocó toda su familia."""


class InvalidClientError(AuthError):
    """Credenciales de servicio (client credentials) inválidas."""


class UserNotFoundError(AuthError):
    """El usuario del token ya no existe o está desactivado."""


class InitialAdminConflictError(AuthError):
    """ADMIN_EMAIL ya pertenece a una cuenta que no es de administrador."""
