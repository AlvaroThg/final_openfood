"""Genera la clave privada RSA del JWT dentro del volumen `jwt_keys` (contenedor `keygen`).

    python -m app.keygen [/keys/jwt_private.pem]

- Si la clave ya existe, no hace nada: regenerarla invalidaría todas las sesiones.
- RSA de 3072 bits, permisos 0600 y escritura atómica (archivo temporal + rename).
- La clave nunca sale del volumen: solo la monta auth_service (en solo lectura).
Para rotarla: detener el stack, borrar el volumen jwt_keys y volver a levantarlo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEY_BITS = 3072


def ensure_key(path: Path) -> bool:
    """Crea la clave si no existe. Devuelve True si la creó."""
    if path.exists():
        return False
    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    tmp.replace(path)
    return True


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0] if args else os.environ.get("JWT_PRIVATE_KEY_PATH", "/keys/jwt_private.pem"))
    created = ensure_key(path)
    print(f"[keygen] {'clave creada' if created else 'la clave ya existía'}: {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
