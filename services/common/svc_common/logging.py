"""Logs estructurados en JSON (una línea por evento) con el request_id de la petición en curso.

Uso:
    configure_logging("auth_service", "INFO")
    logger.info("login_ok", extra={"user_id": str(user.id)})

Los campos pasados en `extra` se agregan al JSON. Defensa adicional: un campo cuyo nombre
sugiera un secreto (password, token, secret, authorization, cookie) se reemplaza por
"[redactado]". La regla principal sigue siendo no pasarlos nunca.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SENSITIVE = re.compile(r"pass|token|secret|authorization|cookie|private", re.IGNORECASE)
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            payload[key] = "[redactado]" if _SENSITIVE.search(key) else value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    """Reemplaza los handlers del logger raíz por uno JSON a stdout (Docker recoge stdout)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
