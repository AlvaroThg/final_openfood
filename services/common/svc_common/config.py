"""Lectura de configuración desde variables de entorno.

Fail fast: una variable obligatoria ausente o un valor mal formado detiene el arranque con un
mensaje claro, en lugar de aparecer como un error raro en la primera petición.
"""

from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """Configuración ausente o inválida. Debe impedir que el servicio arranque."""


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Falta la variable de entorno obligatoria {name}")
    return value


def optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def as_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = optional(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} debe ser un entero, se recibió {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} debe ser >= {minimum}, se recibió {value}")
    return value


def as_bool(name: str, default: bool) -> bool:
    raw = optional(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name} debe ser true o false, se recibió {raw!r}")


def as_list(name: str) -> tuple[str, ...]:
    """Lista separada por comas; ignora espacios y elementos vacíos."""
    raw = optional(name)
    if raw is None:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())
