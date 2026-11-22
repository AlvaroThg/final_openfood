"""Hash de contraseñas con argon2id (pwdlib).

Parámetros por defecto: time_cost=3, memory_cost=64 MiB, parallelism=4 (perfil de RFC 9106).
Si en el futuro se suben, `verify` devuelve un hash nuevo y el login lo reemplaza sin pedirle
nada al usuario.
"""

from __future__ import annotations

import secrets

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher


class Argon2PasswordHasher:
    def __init__(self, *, time_cost: int = 3, memory_cost: int = 65_536, parallelism: int = 4):
        self._hasher = PasswordHash(
            (Argon2Hasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism),)
        )
        # Hash de una contraseña aleatoria, con los mismos parámetros, para verify_dummy.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> tuple[bool, str | None]:
        return self._hasher.verify_and_update(password, password_hash)

    def verify_dummy(self, password: str) -> None:
        self._hasher.verify(password, self._dummy_hash)
