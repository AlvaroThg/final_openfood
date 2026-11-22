"""Genera el archivo .env con secretos aleatorios (una sola vez, solo biblioteca estándar).

    python scripts/init_env.py [--admin-email admin@nutriscore.local]

Sin Python en el equipo:
    docker run --rm -v "${PWD}:/w" -w /w python:3.12.13-slim-bookworm python scripts/init_env.py

- Nunca sobrescribe un .env existente (perderías el acceso a la base de datos ya creada).
- Cada servicio recibe su propia contraseña de base de datos.
- El secreto del cliente de servicio (retrain_service) va en claro solo para el worker;
  auth_service recibe únicamente su SHA-256.
- La contraseña del administrador se muestra UNA vez en la consola.
- La clave privada del JWT no está aquí: la genera el contenedor `keygen` dentro de un volumen.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-email", default="admin@nutriscore.local")
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args(argv)

    if args.output.exists():
        print(f"{args.output} ya existe; no se sobrescribe. Bórralo a mano solo si también "
              "vas a borrar los volúmenes (docker compose down -v).", file=sys.stderr)
        return 1

    client_secret = token(32)
    admin_password = token(12)
    values = {
        "POSTGRES_PASSWORD": token(),
        "APP_MIGRATOR_DB_PASSWORD": token(),
        "APP_AUTH_DB_PASSWORD": token(),
        "APP_IA_DB_PASSWORD": token(),
        "APP_RETRAIN_DB_PASSWORD": token(),
        "RETRAIN_CLIENT_SECRET": client_secret,
        "RETRAIN_CLIENT_SECRET_SHA256": hashlib.sha256(client_secret.encode()).hexdigest(),
        "ADMIN_EMAIL": args.admin_email,
        "ADMIN_PASSWORD": admin_password,
        "COOKIE_SECURE": "true",
        "FRONTEND_PORT": "3000",
        "LOG_LEVEL": "INFO",
    }
    lines = ["# Generado por scripts/init_env.py. NO subir a git (está en .gitignore)."]
    lines += [f"{key}={value}" for key, value in values.items()]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Creado {args.output}")
    print(f"Administrador inicial: {args.admin_email}")
    print(f"Contraseña:            {admin_password}   (guárdala; no se vuelve a mostrar)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
